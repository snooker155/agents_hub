"""
Compaction inside the agent loop (agents/loop_ext/compaction.py): old tool
results cleared oldest first with every call still paired with its result,
the oldest steps folded into a summary that later calls reuse, the history
shortened when it alone is too big, Anthropic's server-side clearing sent on
the agent's own model, and the context-window guard handing an overflow to
compaction before it stops the run.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, List

import httpx
import pytest
from agents.loop_executor import LoopExecutor
from langchain_core.agents import AgentActionMessageLog
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

from agents import agent_loop
from agents.agent_loop import LoopState, build_agent_runnable
from agents.callbacks.guards import ContextWindowExceededError, ContextWindowGuard
from agents.loop_ext import compaction
from agents.loop_ext.compaction import CompactionExtension

WINDOW = 1000  # tokens; budget = 0.7 * 1000 * 4 = 2800 characters


class _Summarizer:
    """Stands in for the agent's model when it writes a summary."""

    def __init__(self, text: str = "SUMMARY") -> None:
        self.calls: List[Any] = []
        self.text = text

    def invoke(self, messages, **_kw):
        self.calls.append(messages)
        return AIMessage(content=f"{self.text} {len(self.calls)}")


def _agent(**kw) -> Any:
    ns = SimpleNamespace(
        agent_id="a1", provider="openai", model="gpt-test", system_prompt="sys",
        workspace=None, spec=None, _tools=[], _llm=None, api_key=None, base_url=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    ns.effective_provider = lambda llm=None: ns.provider
    return ns


@pytest.fixture
def window(monkeypatch):
    import providers.context_windows as cw
    monkeypatch.setattr(cw, "get_model_context_window", lambda provider, model: WINDOW)
    for key in ("AGENTS_HUB_LOOP_COMPACTION", "AGENTS_HUB_LOOP_COMPACTION_FRACTION",
                "AGENTS_HUB_LOOP_COMPACTION_KEEP", "AGENTS_HUB_LOOP_NATIVE"):
        monkeypatch.delenv(key, raising=False)
    return WINDOW


def _ext(agent=None) -> CompactionExtension:
    ext = compaction.extension_for(agent or _agent())
    assert ext is not None
    summarizer = _Summarizer()
    ext.make_summarizer = lambda: summarizer
    ext.summarizer = summarizer
    return ext


def _step_messages(n: int, result_chars: int = 900, args_chars: int = 0) -> List[Any]:
    out: List[Any] = []
    for i in range(n):
        args = {"path": f"f{i}.txt", **({"body": "x" * args_chars} if args_chars else {})}
        out.append(AIMessage(content="", tool_calls=[{"name": "read_file", "args": args, "id": f"c{i}"}]))
        out.append(ToolMessage(content=f"r{i}:" + "y" * result_chars, tool_call_id=f"c{i}",
                               additional_kwargs={"name": "read_file"}))
    return out


def _paired(messages: List[Any]) -> bool:
    calls = [c["id"] for m in messages for c in (getattr(m, "tool_calls", None) or [])]
    results = [m.tool_call_id for m in messages if isinstance(m, ToolMessage)]
    return calls == results


# ── switching on and off ─────────────────────────────────────────────────────

def test_unknown_window_leaves_the_agent_alone(monkeypatch):
    import providers.context_windows as cw
    monkeypatch.setattr(cw, "get_model_context_window", lambda provider, model: 0)
    assert compaction.extension_for(_agent()) is None


def test_agent_flag_and_setting_switch_it_off(window, monkeypatch):
    assert compaction.extension_for(_agent(spec=SimpleNamespace(compaction=False))) is None
    assert compaction.extension_for(_agent(spec=SimpleNamespace(compaction=None))) is not None
    monkeypatch.setenv("AGENTS_HUB_LOOP_COMPACTION", "0")
    assert compaction.extension_for(_agent()) is None
    monkeypatch.delenv("AGENTS_HUB_LOOP_COMPACTION")
    import agents.loop_ext.settings as loop_settings
    monkeypatch.setattr(loop_settings, "_workspace_block",
                        lambda ws: {"compaction": False})
    assert compaction.extension_for(_agent(workspace="w")) is None


def test_fraction_and_keep_come_from_the_workspace(window, monkeypatch):
    import agents.loop_ext.settings as loop_settings
    monkeypatch.setattr(loop_settings, "_workspace_block",
                        lambda ws: {"compaction_fraction": 0.5, "compaction_keep": 1})
    ext = compaction.extension_for(_agent(workspace="w"))
    assert ext.fraction == 0.5 and ext.keep == 1
    assert ext.budget_chars == 2000


# ── the passes ───────────────────────────────────────────────────────────────

def test_below_the_threshold_nothing_changes(window):
    ext, state = _ext(), LoopState()
    pad = _step_messages(2, result_chars=100)
    out = ext.shape_messages(state, {"input": "go", "intermediate_steps": [1, 2]}, list(pad))
    assert out == pad
    assert state.compactions == []
    assert ext.summarizer.calls == []


def test_old_tool_results_are_cleared_oldest_first_with_pairing_intact(window):
    ext, state = _ext(), LoopState()
    pad = _step_messages(5, result_chars=700)  # ~3.7k chars against a 2.8k budget
    inputs = {"input": "go", "intermediate_steps": [0] * 5}
    out = ext.shape_messages(state, inputs, list(pad))

    results = [m for m in out if isinstance(m, ToolMessage)]
    assert [r.content.startswith("[Tool result cleared") for r in results] == \
        [True, True, False, False, False]
    assert "read_file returned 703 characters" in results[0].content
    assert _paired(out)
    assert state.compactions[0]["kind"] == "clear_tool_results"
    assert state.compactions[0]["cleared"] == 2
    assert state.compactions[0]["chars_after"] < state.compactions[0]["chars_before"]
    assert ext.summarizer.calls == []

    # The next call re-applies the same clearing without a new pass.
    again = ext.shape_messages(state, inputs, list(pad))
    assert [m.content for m in again] == [m.content for m in out]
    assert len(state.compactions) == 1


def test_steps_fold_into_a_cached_summary_when_clearing_is_not_enough(window):
    ext, state = _ext(), LoopState()
    # The weight is in the calls' arguments, which clearing never touches.
    pad = _step_messages(6, result_chars=10, args_chars=700)
    inputs = {"input": "go", "intermediate_steps": [0] * 6}
    out = ext.shape_messages(state, inputs, list(pad))

    assert isinstance(out[0], HumanMessage)
    assert out[0].content.startswith(compaction.STEPS_SUMMARY_PREFIX)
    assert "SUMMARY 1" in out[0].content
    assert _paired(out[1:])
    kept = [m for m in out if isinstance(m, AIMessage)]
    assert len(kept) >= compaction.MIN_KEEP_STEPS
    assert kept[-1].tool_calls[0]["id"] == "c5"
    summaries = [c for c in state.compactions if c["kind"] == "summary"]
    assert summaries and summaries[0]["scope"] == "steps"
    folded = summaries[0]["cleared"]
    assert len(ext.summarizer.calls) == 1

    # The summary is reused, not written again, while the trail still fits.
    again = ext.shape_messages(state, inputs, list(pad))
    assert again[0].content == out[0].content
    assert len(ext.summarizer.calls) == 1

    # More steps past the line extend the same summary.
    more = _step_messages(9, result_chars=10, args_chars=700)
    third = ext.shape_messages(state, {"input": "go", "intermediate_steps": [0] * 9}, list(more))
    assert "SUMMARY 2" in third[0].content
    assert len(ext.summarizer.calls) == 2
    record = str(ext.summarizer.calls[1][1].content)
    assert "SUMMARY 1" in record  # the earlier summary is continued
    assert state.scratch["compaction"]["fold"]["steps"] > folded
    assert _paired(third[1:])


def test_fold_falls_back_to_a_heuristic_when_the_model_fails(window):
    ext, state = _ext(), LoopState()

    class _Broken:
        def invoke(self, *_a, **_k):
            raise RuntimeError("overloaded")

    ext.make_summarizer = lambda: _Broken()
    pad = _step_messages(6, result_chars=10, args_chars=700)
    out = ext.shape_messages(state, {"input": "go", "intermediate_steps": [0] * 6}, list(pad))
    assert "read_file(" in out[0].content
    assert state.compactions[-1]["fallback"] is True


def test_a_long_history_is_shortened(window):
    ext, state = _ext(), LoopState()
    history = []
    for i in range(10):
        history.append(HumanMessage(content=f"question {i} " + "q" * 300))
        history.append(AIMessage(content=f"answer {i} " + "a" * 300))
    inputs = {"input": "go", "chat_history": list(history), "intermediate_steps": []}
    ext.shape_messages(state, inputs, [])

    shortened = inputs["chat_history"]
    assert len(shortened) < len(history)
    assert "SUMMARY" in str(shortened[0].content)
    assert shortened[-1].content == history[-1].content
    entry = [c for c in state.compactions if c.get("scope") == "history"][0]
    assert entry["kind"] == "summary" and entry["cleared"] > 0

    # Reused on the next call of the same run without a second summary.
    calls = len(ext.summarizer.calls)
    again = {"input": "go", "chat_history": list(history), "intermediate_steps": []}
    ext.shape_messages(state, again, [])
    assert [m.content for m in again["chat_history"]] == [m.content for m in shortened]
    assert len(ext.summarizer.calls) == calls


def test_the_agent_loop_sends_the_cleared_trail(window):
    """End to end through the executor: later model calls see the notes."""

    @tool
    def read_file(path: str) -> str:
        """Read a file."""
        return "z" * 480

    class _Model(GenericFakeChatModel):
        seen: List[Any] = []

        def bind_tools(self, tools, **kwargs):
            return self.bind(tools=[t.name for t in tools], **kwargs)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            type(self).seen.append(list(messages))
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    _Model.seen = []
    replies = [AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"path": f"{i}"},
                                                  "id": f"t{i}"}]) for i in range(6)]
    model = _Model(disable_streaming=True, messages=iter([*replies, AIMessage(content="done")]))
    ext = _ext(_agent(_tools=[read_file]))
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content="sys"), ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad")])
    runnable = build_agent_runnable(model, [read_file], prompt, [ext])
    executor = LoopExecutor(agent=runnable, tools=[read_file], return_intermediate_steps=True)
    state = LoopState(run_id="r1")
    token = agent_loop.set_state(state)
    try:
        result = executor.invoke({"input": "go"})
    finally:
        agent_loop.reset_state(token)
    assert result["output"] == "done"
    last = _Model.seen[-1]
    tool_msgs = [m for m in last if isinstance(m, ToolMessage)]
    assert [m.content.startswith("[Tool result cleared") for m in tool_msgs] == \
        [True, True, True, False, False, False]
    assert tool_msgs[-1].content == "z" * 480
    assert _paired([m for m in last if isinstance(m, (AIMessage, ToolMessage))])
    assert any(c["kind"] == "clear_tool_results" for c in state.summary()["compactions"])


# ── Anthropic's server-side clearing ─────────────────────────────────────────

def _anthropic(model: str = "claude-opus-4-6", **kw):
    from providers.anthropic_driver import AnthropicChatModel
    return AnthropicChatModel(model=model, api_key="test-key", **kw)


def _capture(llm, response: dict) -> List[dict]:
    """Route *llm*'s HTTP calls to a local handler; returns what was sent."""
    import anthropic
    sent: List[dict] = []

    def handler(request):
        sent.append({"url": str(request.url), "headers": dict(request.headers),
                     "body": json.loads(request.content)})
        return httpx.Response(200, json=response)

    llm.root_client = anthropic.Client(
        api_key="test-key", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return sent


_REPLY = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-4-6",
          "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
          "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 2}}


def test_anthropic_models_get_the_server_side_clearing(window):
    llm = _anthropic()
    ext = _ext(_agent(provider="anthropic", model="claude-opus-4-6", _llm=llm,
                      _tools=[SimpleNamespace(name="read_file"), SimpleNamespace(name="think")]))
    assert ext.native
    state = LoopState()
    kwargs = ext.model_kwargs(state, llm)
    assert kwargs["betas"] == ["context-management-2025-06-27"]
    edit = kwargs["context_management"]["edits"][0]
    assert edit["type"] == "clear_tool_uses_20250919"
    assert edit["trigger"] == {"type": "input_tokens", "value": 700}
    assert edit["keep"] == {"type": "tool_uses", "value": 3}
    assert edit["exclude_tools"] == ["think"]
    # Recorded once per run however many calls carry it.
    ext.model_kwargs(state, llm)
    assert [c["kind"] for c in state.compactions] == ["server"]
    # A fallback model on another provider gets nothing.
    assert ext.model_kwargs(state, object()) == {}

    # What the installed client actually sends with it.
    sent = _capture(llm, _REPLY)
    llm.bind(**kwargs).invoke([HumanMessage(content="hi")])
    assert sent[0]["url"].endswith("/v1/messages?beta=true")
    assert sent[0]["headers"]["anthropic-beta"] == "context-management-2025-06-27"
    assert sent[0]["body"]["context_management"] == {"edits": [edit]}


def test_server_side_clearing_replaces_the_client_pass(window):
    llm = _anthropic()
    ext = _ext(_agent(provider="anthropic", model="claude-opus-4-6", _llm=llm))
    state = LoopState()
    ext.model_kwargs(state, llm)
    pad = _step_messages(5, result_chars=900)  # past the local estimate
    # What the provider reported after its own clearing is what counts.
    ai = AIMessage(content="", usage_metadata={"input_tokens": 300, "output_tokens": 1,
                                               "total_tokens": 301})
    steps = [(AgentActionMessageLog(tool="read_file", tool_input={}, log="", message_log=[ai]), "r")]
    out = ext.shape_messages(state, {"input": "go", "intermediate_steps": steps}, list(pad))
    assert [m.content for m in out] == [m.content for m in pad]
    assert not any(c["kind"] == "clear_tool_results" for c in state.compactions)


def test_server_clearing_counts_come_from_the_response(window):
    llm = _anthropic()
    ext = _ext(_agent(provider="anthropic", model="claude-opus-4-6", _llm=llm))
    state = LoopState()
    ext.model_kwargs(state, llm)
    ai = AIMessage(content="", tool_calls=[{"name": "read_file", "args": {}, "id": "c1"}],
                   usage_metadata={"input_tokens": 300, "output_tokens": 5, "total_tokens": 305},
                   response_metadata={"context_management": {"applied_edits": [
                       {"type": "clear_tool_uses_20250919", "cleared_tool_uses": 4,
                        "cleared_input_tokens": 500}]}})
    step = (AgentActionMessageLog(tool="read_file", tool_input={}, log="", message_log=[ai]), "r")
    ext.shape_messages(state, {"input": "go", "intermediate_steps": [step]}, [])
    server = state.compactions[0]
    assert server["kind"] == "server" and server["cleared"] == 4
    assert server["cleared_input_tokens"] == 500
    assert server["chars_before"] == 800 * 4 and server["chars_after"] == 300 * 4


def test_server_path_folds_when_the_reported_prompt_is_still_too_big(window):
    llm = _anthropic()
    ext = _ext(_agent(provider="anthropic", model="claude-opus-4-6", _llm=llm))
    state = LoopState()
    pad = _step_messages(6, result_chars=10)
    big = AIMessage(content="", usage_metadata={"input_tokens": 900, "output_tokens": 1,
                                                "total_tokens": 901})
    steps = [(AgentActionMessageLog(tool="read_file", tool_input={}, log="", message_log=[big]), "r")]
    out = ext.shape_messages(state, {"input": "go", "intermediate_steps": steps}, list(pad))
    assert out[0].content.startswith(compaction.STEPS_SUMMARY_PREFIX)


def test_old_claude_models_stay_client_side(window):
    llm = _anthropic("claude-3-5-sonnet-20241022")
    ext = _ext(_agent(provider="anthropic", model="claude-3-5-sonnet-20241022", _llm=llm))
    assert not ext.native
    assert ext.model_kwargs(LoopState(), llm) == {}


def test_native_can_be_switched_off(window, monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_LOOP_NATIVE", "false")
    llm = _anthropic()
    assert not _ext(_agent(provider="anthropic", model="claude-opus-4-6", _llm=llm)).native


# ── the context-window guard ─────────────────────────────────────────────────

def _usage(prompt_tokens: int) -> LLMResult:
    msg = AIMessage(content="x", usage_metadata={"input_tokens": prompt_tokens,
                                                 "output_tokens": 1,
                                                 "total_tokens": prompt_tokens + 1})
    return LLMResult(generations=[[ChatGeneration(message=msg)]])


def test_guard_hands_an_overflow_to_compaction_before_stopping():
    guard = ContextWindowGuard(100, model_name="m")
    with pytest.raises(ContextWindowExceededError):
        guard.on_llm_end(_usage(150))  # no loop state: stops at once

    state = LoopState()
    CompactionExtension.scratch(state)
    token = agent_loop.set_state(state)
    try:
        guard.on_llm_end(_usage(150))
        record = state.scratch["compaction"]
        assert record["force"] is True and record["overflows"] == 1
        record["force"] = False
        guard.on_llm_end(_usage(80))  # a call that fits resets the count
        assert record["overflows"] == 0
        guard.on_llm_end(_usage(150))
        guard.on_llm_end(_usage(150))
        with pytest.raises(ContextWindowExceededError):
            guard.on_llm_end(_usage(150))
    finally:
        agent_loop.reset_state(token)


def test_a_forced_pass_compacts_regardless_of_the_estimate(window):
    ext, state = _ext(), LoopState()
    pad = _step_messages(5, result_chars=450)  # ~2.4k chars: under the 2.8k budget
    inputs = {"input": "go", "intermediate_steps": [0] * 5}
    assert ext.shape_messages(state, inputs, list(pad)) == pad
    state.scratch["compaction"]["force"] = True
    out = ext.shape_messages(state, inputs, list(pad))
    assert out[0].content.startswith(compaction.STEPS_SUMMARY_PREFIX)
    assert state.scratch["compaction"]["force"] is False
    kinds = [c["kind"] for c in state.compactions]
    assert kinds[0] == "clear_tool_results" and "summary" in kinds
