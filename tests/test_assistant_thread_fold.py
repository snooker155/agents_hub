"""
The assistant's thread as short-term memory.

An entity chat shows its agent the last twelve messages and nothing else, which
is right for a scenario or a view (the entity itself is re-rendered every turn)
and wrong for the assistant, whose thread is the conversation itself: turn
seven used to vanish without a trace. A thread with ``fold_history`` folds its
older turns into the session's summary after a reply and prompts with that
summary plus every turn since.

Also here: the readers of a chat turn's words. A chat run leaves the ``input``
and ``output`` columns empty, so a reader of the columns alone (the memory
consolidation did) sees a conversation with nothing in it.
"""
import asyncio
import threading

import pytest

import chat.entity_chat as entity_chat
from chat.entity_chat import (
    EntityChatSpec, RecordingQueue, fold_session_history, folded_history, transcript_block,
)
from common.entity_chat_store import EntityChatStore
from common.session_service import (
    get_or_create_chat_session, get_session_summary, set_session_summary,
)


class _FakeSummarizer:
    def __init__(self, text="SUMMARY: the person asked about the release plan"):
        self.text = text
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        from langchain_core.messages import AIMessage
        return AIMessage(content=self.text)


@pytest.fixture
def summarizer(monkeypatch):
    fake = _FakeSummarizer()
    import agents.agent_utils as agent_utils
    monkeypatch.setattr(agent_utils, "build_chat_model", lambda **kw: fake)
    return fake


def _thread(n, size=1000, start=0):
    """n exchanges, each side ``size`` characters, distinct per turn."""
    out = []
    for i in range(start, start + n):
        out.append({"role": "user", "content": f"question {i} " + "q" * size})
        out.append({"role": "assistant", "content": f"answer {i} " + "a" * size})
    return out


def _session():
    return get_or_create_chat_session("assistantchat:fold-test", "Assistant")


# ── the transcript ───────────────────────────────────────────────────────────

def test_the_transcript_puts_the_summary_first_and_keeps_every_turn_without_a_limit():
    history = [{"role": "summary", "content": "they want a bard"}] + _thread(10, size=5)
    text = transcript_block(history, limit=None)
    assert text.splitlines()[0] == "(Earlier in this conversation, summarised: they want a bard)"
    assert "question 0" in text and "answer 9" in text


def test_the_default_transcript_is_still_the_last_twelve():
    text = transcript_block(_thread(10, size=5))
    assert "question 0" not in text and "answer 9" in text


# ── folding ───────────────────────────────────────────────────────────────────

def test_a_short_thread_is_sent_whole_and_folds_nothing(summarizer):
    sid = _session()
    history = _thread(3) + [{"role": "user", "content": "and now?"}]
    assert folded_history(history, sid) == history
    fold_session_history(sid, history)
    assert get_session_summary(sid) == {}
    assert summarizer.calls == []


def test_a_long_thread_folds_its_older_turns_into_the_session(summarizer):
    sid = _session()
    history = _thread(20)  # 40k characters, past the budget
    fold_session_history(sid, history, provider="openai", model="gpt-x")

    stored = get_session_summary(sid)
    assert stored["text"] == summarizer.text
    assert 0 < stored["covers_until"] < len(history)
    # Exactly the folded turns went to the summariser, and none of the tail.
    folded_text = summarizer.calls[0][-1].content
    assert "question 0 " in folded_text
    assert "answer 19 " not in folded_text

    nxt = folded_history(history + [{"role": "user", "content": "what did I ask first?"}], sid)
    assert nxt[0] == {"role": "summary", "content": summarizer.text}
    assert nxt[-1]["content"] == "what did I ask first?"
    tail = nxt[1:-1]
    assert tail == history[stored["covers_until"]:]
    assert sum(len(m["content"]) for m in tail) <= entity_chat.FOLD_TARGET_CHARS


def test_a_second_fold_extends_the_first_summary(summarizer):
    sid = _session()
    history = _thread(20)
    fold_session_history(sid, history)
    first = get_session_summary(sid)["covers_until"]

    summarizer.text = "SUMMARY v2"
    longer = history + _thread(15, start=20)
    fold_session_history(sid, longer)
    stored = get_session_summary(sid)
    assert stored["text"] == "SUMMARY v2"
    assert stored["covers_until"] > first
    # The previous summary went in, not the turns it already speaks for.
    body = summarizer.calls[-1][-1].content
    assert "SUMMARY: the person asked" in body
    assert "question 0 " not in body


def test_a_failed_summary_call_still_folds_with_the_heuristic(monkeypatch):
    class _Broken:
        def invoke(self, messages):
            raise RuntimeError("provider down")

    import agents.agent_utils as agent_utils
    monkeypatch.setattr(agent_utils, "build_chat_model", lambda **kw: _Broken())
    sid = _session()
    fold_session_history(sid, _thread(20))
    assert get_session_summary(sid).get("text")


def test_an_unfolded_long_thread_keeps_only_its_newest_turns():
    """A thread from before folding existed, on its first turn: the prompt is
    bounded by the hard ceiling, newest turns kept."""
    history = _thread(40) + [{"role": "user", "content": "hi"}]
    out = folded_history(history, None)
    kept = out[:-1]
    assert sum(len(m["content"]) for m in kept) <= entity_chat.FOLD_HARD_TAIL_CHARS
    assert kept[-1] == history[-2]
    assert "question 0 " not in "".join(m["content"] for m in kept)


def test_a_summary_whose_anchor_is_gone_does_not_skip_turns():
    sid = _session()
    set_session_summary(sid, "old summary", 4, anchor="u:doesnotexist0000")
    history = _thread(3, size=5) + [{"role": "user", "content": "next"}]
    out = folded_history(history, sid)
    assert out[0]["role"] == "summary"
    assert out[1:] == history


# ── the turn ──────────────────────────────────────────────────────────────────

class _Agent:
    provider, model = "openai", "gpt-x"

    def __init__(self):
        self.prompts = []

    async def arun(self, prompt, callbacks=None):
        self.prompts.append(prompt)
        from types import SimpleNamespace
        return SimpleNamespace(ok=True, agent_output="Noted.", error=None)


@pytest.fixture
def turn(monkeypatch, tmp_path, summarizer):
    import common.entity_chat_store as store_module
    chat_store = EntityChatStore(path=tmp_path / "chats.json")
    monkeypatch.setattr(store_module, "entity_chat_store", lambda: chat_store)
    import common.bootstrap as bootstrap
    monkeypatch.setattr(bootstrap, "ensure_system_agent", lambda agent_id: True)
    agent = _Agent()
    import agents.agent_factory as agent_factory
    monkeypatch.setattr(agent_factory, "create_agent", lambda *a, **k: agent)
    import managers.run_manager as run_manager
    monkeypatch.setattr(run_manager, "update_run", lambda *a, **k: None)
    monkeypatch.setattr(run_manager, "run_log_path", lambda rid: tmp_path / f"{rid}.log")

    import agents.callbacks as callbacks

    class _Callback:
        bound_provider, bound_model = "openai", "gpt-x"

        def __init__(self, *a, **k):
            self.tool_history = self.thinking_history = self.llm_invocations = []
            self.llm_invoke_responses = self.artifact_history = []
            self.prompt_tokens = self.completion_tokens = self.total_tokens = 0
            self.tool_calls = self.context_window = self.max_prompt_tokens = 0

        def bind_model(self, provider, model):
            pass

    monkeypatch.setattr(callbacks, "ChatStreamCallback", _Callback)

    def run(message, fold=True):
        spec = EntityChatSpec(kind="assistant", agent_id="assistant", title="Assistant",
                              fold_history=fold)
        asyncio.run(entity_chat.run_entity_chat_turn(
            RecordingQueue(), spec, "me", message,
            lambda history: transcript_block(history, limit=None if fold else 12)))
        for t in threading.enumerate():
            if t.name.startswith("fold-"):
                t.join(timeout=10)

    return {"store": chat_store, "agent": agent, "run": run}


def test_a_folding_thread_remembers_its_first_turn_after_a_fold(turn):
    for m in _thread(20):
        turn["store"].append_message("assistant", "me", m["role"], m["content"])
    turn["run"]("one more")
    # The reply triggered the fold: the next prompt opens with the summary
    # instead of having dropped the first turns.
    turn["run"]("what did I ask first?")
    prompt = turn["agent"].prompts[-1]
    assert prompt.startswith("(Earlier in this conversation, summarised: SUMMARY")
    assert "what did I ask first?" in prompt
    assert "one more" in prompt


def test_a_thread_without_folding_keeps_the_last_twelve(turn):
    for m in _thread(20, size=5):
        turn["store"].append_message("assistant", "me", m["role"], m["content"])
    turn["run"]("hello", fold=False)
    prompt = turn["agent"].prompts[-1]
    assert "question 0 " not in prompt and "summarised" not in prompt


# ── reading a chat turn's words ──────────────────────────────────────────────

def _chat_run(run_id="run-chat-1", agent_id="assistant"):
    from managers import run_manager as rm
    rm.upsert_run({"run_id": run_id, "agent_id": agent_id, "status": "completed",
                   "session_type": "chat", "title": "what is the pl",
                   "started_at": "2026-10-06T10:00:00+00:00"})
    rm.update_run(run_id, {"response": "the plan is v2", "process": {
        "llm_input_context": {"user_message": "what is the plan for the release?",
                              "response": "the plan is v2"}}})
    return rm.get_runs_by_ids([run_id])[run_id]


def test_run_exchange_reads_a_chat_turn_from_its_payload():
    from managers.run_manager import run_exchange
    run = _chat_run()
    assert run.get("input") is None and run.get("output") is None
    assert run_exchange(run) == ("what is the plan for the release?", "the plan is v2")


def test_run_exchange_falls_back_to_the_columns():
    from managers import run_manager as rm
    rm.upsert_run({"run_id": "run-old", "agent_id": "a", "status": "completed",
                   "input": "old question", "output": "old answer"})
    assert rm.run_exchange(rm.get_runs_by_ids(["run-old"])["run-old"]) == ("old question", "old answer")


def test_the_consolidation_excerpt_carries_a_chat_turns_words():
    from memory.consolidation import _session_excerpt
    text = _session_excerpt(_chat_run())
    assert "User: what is the plan for the release?" in text
    assert "Agent: the plan is v2" in text
