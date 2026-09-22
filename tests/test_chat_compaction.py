"""Folding a conversation that outgrew the model, instead of failing on it.

A chat only ever gets longer, and every turn re-sends all of it. The old
behaviour was to keep sending until the provider refused, and then to show the
user an error whose only answer was to clear the chat. Compaction is the other
answer: past a budget the older turns become one summary, the recent tail is
sent verbatim, and the summary is stored on the session so the next turn starts
from it.

What has to hold: the budget follows the model that actually runs, the fold
keeps the recent turns and the opening context, a second fold extends the first
summary rather than starting over, a summariser that fails does not take the
turn down with it, and a provider that refuses the turn anyway gets one more
attempt on a folded history.

Run: ``python -m pytest tests/test_chat_compaction.py -q``
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from chat import compaction as comp
from chat.compaction import (
    BUDGET_FRACTION,
    CHARS_PER_TOKEN,
    FALLBACK_BUDGET_CHARS,
    SUMMARY_PREFIX,
    compact_for_turn,
    compact_history,
    compaction_event,
    heuristic_summary,
    history_budget_chars,
)


class _FakeLLM:
    """The summariser: records what it was asked and answers in one call."""

    def __init__(self, reply="the earlier conversation, summarised"):
        self.reply = reply
        self.calls: list = []

    def invoke(self, messages, **kwargs):
        self.calls.append(messages)
        return AIMessage(content=self.reply)


class _BrokenLLM:
    def __init__(self):
        self.calls = 0

    def invoke(self, messages, **kwargs):
        self.calls += 1
        raise RuntimeError("the provider is having a day")


def _talk(n: int, size: int = 1000) -> list:
    """``n`` alternating turns of ``size`` characters each."""
    out = []
    for i in range(n):
        text = f"turn {i} " + ("x" * size)
        out.append(HumanMessage(content=text) if i % 2 == 0 else AIMessage(content=text))
    return out


def _prompt_text(messages) -> str:
    """The text the summariser was handed."""
    return "\n".join(comp.message_text(m) for m in messages)


# ── the budget ───────────────────────────────────────────────────────────────

def test_the_budget_is_a_share_of_the_window_of_the_model_that_runs(monkeypatch):
    monkeypatch.setattr("providers.context_windows.get_model_context_window",
                        lambda provider, model: 200_000)
    assert history_budget_chars("anthropic", "claude-x") == int(
        200_000 * BUDGET_FRACTION * CHARS_PER_TOKEN)


def test_a_bigger_window_earns_a_bigger_budget(monkeypatch):
    windows = {"small": 8_192, "big": 1_000_000}
    monkeypatch.setattr("providers.context_windows.get_model_context_window",
                        lambda provider, model: windows[model])
    assert history_budget_chars("p", "big") > history_budget_chars("p", "small")


def test_an_unknown_window_falls_back_to_a_flat_budget(monkeypatch):
    """A guessed window would fold conversations that fit perfectly well."""
    monkeypatch.setattr("providers.context_windows.get_model_context_window",
                        lambda provider, model: 0)
    assert history_budget_chars("ollama", "something-local") == FALLBACK_BUDGET_CHARS


def test_the_fallback_budget_is_the_same_constant_as_the_prompt_history_bound():
    """One number, one source: chat.context.HISTORY_CHAR_BUDGET is what this
    module's fallback imports, so the two can never quietly drift apart."""
    from chat.context import HISTORY_CHAR_BUDGET
    assert FALLBACK_BUDGET_CHARS == HISTORY_CHAR_BUDGET


# ── the fold ─────────────────────────────────────────────────────────────────

def test_a_conversation_inside_the_budget_is_sent_as_it_is():
    history = _talk(6)
    llm = _FakeLLM()

    result = compact_history(history, budget_chars=100_000, llm=llm)

    assert result.messages == history
    assert result.folded == 0 and result.changed is False and result.summary == ""
    assert llm.calls == []  # nothing to summarise, nothing paid for


def test_the_system_prompt_counts_against_the_budget():
    """It is re-sent every turn too, so the history's share is what is left."""
    history = _talk(8, size=1000)
    llm = _FakeLLM()

    roomy = compact_history(history, budget_chars=20_000, system_prompt="s", llm=llm)
    crowded = compact_history(history, budget_chars=20_000,
                              system_prompt="s" * 19_000, llm=llm)

    assert roomy.folded == 0
    assert crowded.folded > 0


def test_the_older_turns_become_one_summary_and_the_tail_stays_verbatim():
    history = _talk(12, size=1000)
    llm = _FakeLLM()

    result = compact_history(history, budget_chars=6_000, llm=llm)

    assert result.folded > 0 and result.changed is True
    head, *tail = result.messages
    assert isinstance(head, SystemMessage)
    assert SUMMARY_PREFIX in head.content and "summarised" in head.content
    # The tail is the end of the conversation, untouched.
    assert tail == history[result.covers_until:]
    assert len(tail) == len(history) - result.folded
    assert tail[-1] is history[-1]


def test_the_summariser_is_shown_the_turns_it_is_folding():
    history = _talk(12, size=1000)
    llm = _FakeLLM()

    result = compact_history(history, budget_chars=6_000, llm=llm)

    asked = _prompt_text(llm.calls[0])
    assert "turn 0" in asked
    # …and not the turns that are still being sent verbatim.
    assert f"turn {len(history) - 1}" not in asked
    assert result.fallback is False


def test_the_most_recent_exchanges_are_never_folded_away():
    """"Try that again" needs the turn it refers to."""
    history = _talk(20, size=5_000)

    result = compact_history(history, budget_chars=2_000, llm=_FakeLLM())

    assert len(result.messages) >= 2  # the summary plus at least one real turn
    assert result.messages[-1] is history[-1]


def test_a_fold_always_makes_progress():
    """A pass that folded nothing would reach the same verdict next turn."""
    history = _talk(4, size=50_000)
    result = compact_history(history, budget_chars=1_000, llm=_FakeLLM())
    assert result.folded >= 1


# ── folding again ────────────────────────────────────────────────────────────

def test_a_second_fold_extends_the_first_summary():
    history = _talk(12, size=1000)
    llm = _FakeLLM()
    first = compact_history(history, budget_chars=6_000, llm=llm)

    grown = history + _talk(8, size=1000)
    second = compact_history(
        grown, budget_chars=6_000, summary=first.summary,
        covers_until=first.covers_until, llm=_FakeLLM("a newer summary"))

    assert second.covers_until > first.covers_until
    assert second.summary == "a newer summary"
    assert second.messages[0].content.endswith("a newer summary")


def test_the_second_summariser_is_given_the_first_summary_to_continue():
    history = _talk(20, size=1000)
    llm = _FakeLLM("second")

    compact_history(history, budget_chars=6_000, summary="what came before",
                    covers_until=6, llm=llm)

    asked = _prompt_text(llm.calls[0])
    assert "what came before" in asked
    # The turns the first summary already speaks for are not re-sent to be
    # summarised a second time.
    assert "turn 0 " not in asked


def test_a_stored_summary_is_kept_when_nothing_new_has_to_be_folded():
    history = _talk(4, size=100)
    llm = _FakeLLM()

    result = compact_history(history, budget_chars=100_000, summary="earlier",
                             covers_until=2, llm=llm)

    assert result.changed is False and result.folded == 0
    assert result.summary == "earlier" and result.covers_until == 2
    assert result.messages[0].content.endswith("earlier")
    assert result.messages[1:] == history[2:]
    assert llm.calls == []


# ── when the summariser fails ────────────────────────────────────────────────

def test_a_failed_summary_call_falls_back_to_a_lossy_summary():
    """The moment compaction is needed is the moment a provider is under strain,
    so the fold cannot depend on one more call to it succeeding."""
    history = _talk(12, size=1000)
    llm = _BrokenLLM()

    result = compact_history(history, budget_chars=6_000, llm=llm)

    assert llm.calls == 1
    assert result.fallback is True and result.changed is True and result.folded > 0
    assert "turn 0" in result.summary          # the opening request survives
    assert "dropped without a summary" in result.summary  # and the gap is admitted
    assert result.messages[-1] is history[-1]


def test_the_heuristic_keeps_the_opening_the_recent_turns_and_an_honest_gap():
    folded = _talk(10, size=10)
    text = heuristic_summary("", folded)

    assert "turn 0" in text
    assert "turn 9" in text
    assert "message(s) in between were dropped" in text


def test_the_heuristic_carries_an_earlier_summary_forward():
    text = heuristic_summary("what came before", _talk(6, size=10))
    assert text.startswith("what came before")


# ── the session remembers the summary ────────────────────────────────────────

@pytest.fixture
def session_id():
    from common.session_service import get_or_create_chat_session
    return get_or_create_chat_session(conversation_id="conv-1", title="t")


def _agent(system_prompt: str = "sys"):
    return SimpleNamespace(provider="openai", model="gpt-4o",
                           system_prompt=system_prompt, api_key=None, base_url=None)


def test_the_summary_is_stored_on_the_session_and_read_back_next_turn(session_id, monkeypatch):
    from common.session_service import get_session_summary

    monkeypatch.setattr("providers.context_windows.get_model_context_window",
                        lambda provider, model: 2_000)  # ~4.8k chars of budget
    history = _talk(20, size=1000)

    first = compact_for_turn(agent=_agent(), history=history, system_prompt="sys",
                             session_id=session_id, llm=_FakeLLM("stored summary"))
    assert first.folded > 0

    stored = get_session_summary(session_id)
    assert stored["text"] == "stored summary"
    assert stored["covers_until"] == first.covers_until

    # The next turn starts from what was stored rather than summarising again.
    llm = _FakeLLM("should not be needed")
    second = compact_for_turn(agent=_agent(), history=history, system_prompt="sys",
                              session_id=session_id, llm=llm)
    assert second.summary == "stored summary"
    assert second.changed is False and llm.calls == []


def test_the_summary_is_found_again_after_the_history_window_slid(session_id, monkeypatch):
    """The surface re-sends a bounded window, so the turn the summary ends at is
    at a lower index every time the window slides. Following it by content keeps
    the messages either side of the seam from being lost."""
    from common.session_service import get_session_summary

    monkeypatch.setattr("providers.context_windows.get_model_context_window",
                        lambda provider, model: 2_000)
    history = _talk(20, size=1000)
    first = compact_for_turn(agent=_agent(), history=history, system_prompt="sys",
                             session_id=session_id, llm=_FakeLLM("stored"))
    assert first.folded > 0
    assert get_session_summary(session_id)["anchor"]

    # The window slid by three messages: the same conversation, lower indices.
    slid = history[3:]
    again = compact_for_turn(agent=_agent(), history=slid, system_prompt="sys",
                             session_id=session_id, llm=_FakeLLM("second"))

    # The tail still starts exactly where the summary ends — no turn is skipped
    # and none is sent twice.
    assert again.messages[1:] == slid[first.covers_until - 3:]


def test_a_summary_whose_turns_have_scrolled_away_keeps_the_whole_window():
    """When nothing the summary folded is in the window any more, everything in
    it is tail: the summary speaks for what came before, and no message of the
    window is quietly dropped."""
    from chat.compaction import message_anchor, realign_covers_until

    history = _talk(10, size=10)
    gone = message_anchor(HumanMessage(content="a turn from long ago"))
    assert realign_covers_until(history, 6, gone) == 0
    # A record written before anchors existed still trusts its index.
    assert realign_covers_until(history, 6, "") == 6


def test_a_turn_that_needs_no_fold_stores_nothing(session_id, monkeypatch):
    from common.session_service import get_session_summary

    monkeypatch.setattr("providers.context_windows.get_model_context_window",
                        lambda provider, model: 200_000)
    result = compact_for_turn(agent=_agent(), history=_talk(4),
                              session_id=session_id, llm=_FakeLLM())

    assert result.folded == 0 and result.compacted is False
    assert get_session_summary(session_id) == {}


def test_compaction_survives_a_session_it_cannot_reach(monkeypatch):
    """A conversation with no session context still has to answer."""
    monkeypatch.setattr("providers.context_windows.get_model_context_window",
                        lambda provider, model: 200_000)
    history = _talk(4)
    result = compact_for_turn(agent=_agent(), history=history, session_id=None,
                              llm=_FakeLLM())
    assert result.messages == history


def test_a_provider_that_takes_no_mid_conversation_system_message_gets_a_note():
    """Gemini's client raises on a system message anywhere but the first
    position, so its summary travels as a user note instead."""
    from langchain_core.messages import HumanMessage as _Human

    from chat.compaction import summary_message

    assert isinstance(summary_message("s", provider="google"), _Human)
    assert isinstance(summary_message("s", provider="openai"), SystemMessage)


def test_the_fold_is_announced_on_the_stream():
    history = _talk(12, size=1000)
    result = compact_history(history, budget_chars=6_000, llm=_FakeLLM("abc"))

    event = compaction_event(result)
    assert event["type"] == "compaction"
    assert event["folded"] == result.folded
    assert event["summary_chars"] == 3


# ── the provider's own verdict ───────────────────────────────────────────────

def _overflow_result():
    return SimpleNamespace(
        ok=False, agent_output="", status="error",
        error="Error code: 400 - prompt is too long: 205000 tokens > 200000 maximum")


def test_an_overflow_error_folds_the_history_and_retries_the_turn_once(monkeypatch):
    """The provider is the last word on what fits. When it refuses a turn the
    arithmetic thought was fine, the turn is worth one more attempt on a folded
    history rather than an error the user can only answer by clearing the chat."""
    import agents.registry as registry
    import agents.agent_invoke as agent_invoke
    import chat.send as send_mod
    from chat.models import ChatHistoryMessage, ChatRequest
    from chat.send import send_chat_message

    monkeypatch.setattr(registry, "get_agent",
                        lambda agent_id: SimpleNamespace(tools=[], model_overrides=lambda: {}))
    monkeypatch.setattr(send_mod, "create_agent",
                        lambda agent_id, workspace=None, **kw: _agent())
    monkeypatch.setattr(comp, "summarizer_llm", lambda agent: _FakeLLM("folded"))
    monkeypatch.setattr("providers.context_windows.get_model_context_window",
                        lambda provider, model: 200_000)

    seen: list = []

    def _fake_invoke(agent, prompt, **kwargs):
        seen.append(list(kwargs.get("history") or []))
        if len(seen) == 1:
            return SimpleNamespace(result=_overflow_result())
        return SimpleNamespace(result=SimpleNamespace(
            ok=True, agent_output="answered after folding", error=None, status="done"))

    monkeypatch.setattr(agent_invoke, "invoke_agent", _fake_invoke)

    request = ChatRequest(
        agent_id="a", message="and now?", conversation_id="conv-overflow",
        history=[ChatHistoryMessage(role="user" if i % 2 == 0 else "agent",
                                    content=f"turn {i}") for i in range(8)],
    )
    out = asyncio.run(send_chat_message(request))

    assert out["ok"] is True and out["response"] == "answered after folding"
    assert len(seen) == 2, "the turn is retried exactly once"
    # The retry runs on a folded history: a summary in front of a shorter tail.
    assert isinstance(seen[1][0], SystemMessage)
    assert "folded" in seen[1][0].content
    verbatim = seen[1][1:]
    assert len(verbatim) < len(seen[0])
    assert verbatim == seen[0][len(seen[0]) - len(verbatim):]


def test_the_streamed_turn_folds_announces_it_and_retries_once(monkeypatch):
    """The same contract on the streaming path, which is where chat actually
    runs: the conversation reaches the agent as messages, a fold is announced on
    the stream, and a refused turn is retried once on the folded history."""
    import agents.registry as registry
    import chat.pipelines as pipelines
    from chat.models import ChatHistoryMessage, ChatRequest

    monkeypatch.setattr(registry, "get_agent",
                        lambda agent_id: SimpleNamespace(tools=[], model_overrides=lambda: {}))
    monkeypatch.setattr(comp, "summarizer_llm", lambda agent: _FakeLLM("folded"))
    monkeypatch.setattr("providers.context_windows.get_model_context_window",
                        lambda provider, model: 200_000)

    calls: list = []

    class _Agent:
        provider, model, system_prompt = "openai", "gpt-4o", "sys"

        async def arun(self, prompt, history=None, callbacks=None):
            calls.append(list(history or []))
            if len(calls) == 1:
                return _overflow_result()
            return SimpleNamespace(ok=True, agent_output="answered after folding",
                                   error=None, response=None, status="done")

    monkeypatch.setattr(pipelines, "create_agent",
                        lambda agent_id, workspace=None, streaming=False, **kw: _Agent())

    request = ChatRequest(
        agent_id="a", message="and now?", conversation_id="conv-streamed",
        history=[ChatHistoryMessage(role="user" if i % 2 == 0 else "agent",
                                    content=f"turn {i}") for i in range(8)],
    )

    async def _drive():
        return [event async for event in pipelines._run_chat_pipeline(request)]

    events = asyncio.run(_drive())

    done = [e for e in events if e["type"] == "done"][-1]
    assert done["ok"] is True and done["response"] == "answered after folding"
    assert len(calls) == 2
    assert [type(m) for m in calls[0]] == [HumanMessage, AIMessage] * 4
    assert isinstance(calls[1][0], SystemMessage)
    assert len(calls[1][1:]) < len(calls[0])

    folds = [e for e in events if e.get("type") == "compaction"]
    assert len(folds) == 1
    assert folds[0]["folded"] >= 1 and folds[0]["summary_chars"] == len("folded")


def test_an_ordinary_failure_is_not_retried(monkeypatch):
    import agents.registry as registry
    import agents.agent_invoke as agent_invoke
    import chat.send as send_mod
    from chat.send import send_chat_message
    from chat.models import ChatRequest

    monkeypatch.setattr(registry, "get_agent",
                        lambda agent_id: SimpleNamespace(tools=[], model_overrides=lambda: {}))
    monkeypatch.setattr(send_mod, "create_agent",
                        lambda agent_id, workspace=None, **kw: _agent())
    monkeypatch.setattr(comp, "summarizer_llm", lambda agent: _FakeLLM())

    calls = []

    def _fake_invoke(agent, prompt, **kwargs):
        calls.append(1)
        return SimpleNamespace(result=SimpleNamespace(
            ok=False, agent_output="", error="tool 'read_file' failed", status="error"))

    monkeypatch.setattr(agent_invoke, "invoke_agent", _fake_invoke)

    out = asyncio.run(send_chat_message(ChatRequest(agent_id="a", message="hi")))
    assert out["ok"] is False and len(calls) == 1
