"""Conversation handoff: the history filters, the sink, the tool's checks and
the agent loop ending a turn on a tool's word (chat/handoff.py,
tools/handoff.py, agents/agent_loop.end_turn).

The end-to-end turn (two runs, one final ``done``) is in
tests/test_handoff_pipeline.py.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agents import agent_loop, registry
from agents.registry import AgentSpec
from chat import handoff as handoff_mod
from tools.handoff import HANDOFF_TOOL_NAME, create_handoff_tools


def _history():
    return [
        HumanMessage(content="hi"), AIMessage(content="hello"),
        HumanMessage(content="my invoice is wrong"), AIMessage(content="which one?"),
        HumanMessage(content="number 42"), AIMessage(content="let me look"),
    ]


def _intent(**overrides):
    fields = dict(from_agent_id="front", from_agent_name="Front desk", to_agent_id="billing",
                  to_agent_name="Billing", reason="invoice question", note="invoice 42 is disputed",
                  history_filter="full")
    fields.update(overrides)
    return handoff_mod.HandoffIntent(**fields)


# ── the filters ──────────────────────────────────────────────────────────────

def test_full_keeps_every_prior_turn():
    history = _history()
    assert handoff_mod.filter_history(history, "full") == history


def test_last_n_keeps_the_tail():
    history = _history()
    kept = handoff_mod.filter_history(history, "last_n:2")
    assert [m.content for m in kept] == ["number 42", "let me look"]
    assert handoff_mod.filter_history(history, "last_n:50") == history


def test_summary_is_one_leading_message():
    kept = handoff_mod.filter_history(_history(), "summary", summary="User disputes invoice 42.")
    assert len(kept) == 1
    assert isinstance(kept[0], SystemMessage)
    assert "User disputes invoice 42." in kept[0].content if isinstance(kept[0].content, str) \
        else "User disputes invoice 42." in json.dumps(kept[0].content)


def test_summary_for_a_provider_without_mid_conversation_system_messages():
    kept = handoff_mod.filter_history(_history(), "summary", summary="s", provider="google")
    assert isinstance(kept[0], HumanMessage)


def test_summary_without_text_hands_over_nothing():
    assert handoff_mod.filter_history(_history(), "summary", summary="  ") == []


def test_none_hands_over_nothing():
    assert handoff_mod.filter_history(_history(), "none") == []


@pytest.mark.parametrize("value,expected", [
    ("full", "full"), ("SUMMARY", "summary"), ("last_n:6", "last_n:6"), (" none ", "none"),
    ("last_n", None), ("last_n:0", None), ("last_n:999", None), ("everything", None), ("", None),
])
def test_filter_spelling(value, expected):
    assert handoff_mod.normalize_filter(value) == expected


def test_a_call_may_only_narrow_the_default():
    assert handoff_mod.narrow_filter(None, "full") == "full"
    assert handoff_mod.narrow_filter("", "summary") == "summary"
    assert handoff_mod.narrow_filter("summary", "full") == "summary"
    assert handoff_mod.narrow_filter("last_n:3", "summary") == "last_n:3"
    assert handoff_mod.narrow_filter("last_n:3", "last_n:10") == "last_n:3"
    assert handoff_mod.narrow_filter("none", "last_n:10") == "none"
    for wider, default in [("full", "summary"), ("summary", "last_n:5"),
                           ("last_n:20", "last_n:10"), ("last_n:1", "none")]:
        with pytest.raises(ValueError, match="only narrow"):
            handoff_mod.narrow_filter(wider, default)
    with pytest.raises(ValueError, match="not a history filter"):
        handoff_mod.narrow_filter("most", "full")


def test_every_filter_adds_the_handoff_note():
    for value in ("full", "summary", "last_n:4", "none"):
        note = handoff_mod.handoff_note(_intent(history_filter=value))
        assert "Front desk" in note and "`front`" in note
        assert "Reason: invoice question" in note
        assert "Note from Front desk for you: invoice 42 is disputed" in note
        assert "History you received:" in note


def test_only_full_shows_what_the_agents_before_said_this_turn():
    replies = [{"agent_name": "Front desk", "text": "Passing you to billing."}]
    assert "Passing you to billing." in handoff_mod.handoff_note(_intent(), replies=replies)
    assert "Passing you to billing." not in handoff_mod.handoff_note(
        _intent(history_filter="none"), replies=replies)


def test_receiving_prompt_puts_the_note_first():
    prompt = handoff_mod.receiving_prompt("NOTE", "the user's message")
    assert prompt.startswith("NOTE") and prompt.endswith("the user's message")


def test_only_full_reads_the_stored_session_summary():
    assert handoff_mod.uses_session_summary("full")
    assert not handoff_mod.uses_session_summary("summary")
    assert not handoff_mod.uses_session_summary("last_n:3")
    assert not handoff_mod.uses_session_summary("none")


def test_the_turn_refuses_a_loop_and_the_depth_limit(monkeypatch):
    assert handoff_mod.refusal_by_turn(["front"], _intent()) is None
    assert "already held" in handoff_mod.refusal_by_turn(["billing", "front"], _intent())
    monkeypatch.setenv(handoff_mod.MAX_DEPTH_ENV, "2")
    assert handoff_mod.refusal_by_turn(["a", "b"], _intent()) is None
    assert "limit" in handoff_mod.refusal_by_turn(["a", "b", "front"], _intent())


# ── the summary is priced on the handing run ─────────────────────────────────

class _FakeSummariser:
    def __init__(self):
        self.calls = []
        self.model_name = "gpt-4o-mini"

    def invoke(self, messages, *a, **kw):
        self.calls.append(messages)
        return AIMessage(content="The user disputes invoice 42.",
                         usage_metadata={"input_tokens": 120, "output_tokens": 12, "total_tokens": 132},
                         response_metadata={"model_name": "gpt-4o-mini"})


def test_the_summary_is_an_aux_call_of_the_running_loop(monkeypatch):
    llm = _FakeSummariser()
    monkeypatch.setattr("chat.compaction.summarizer_llm", lambda agent: llm)
    sink = handoff_mod.HandoffSink(summarizer=SimpleNamespace(provider="openai", model="gpt-4o-mini"))
    sink.set_conversation(SimpleNamespace(summary="", messages=_history()))

    state = agent_loop.LoopState(run_id="r1")
    token = agent_loop.set_state(state)
    try:
        text = handoff_mod.summarize_for_handoff(sink)
    finally:
        agent_loop.reset_state(token)

    assert text == "The user disputes invoice 42."
    assert len(llm.calls) == 1
    assert [a["purpose"] for a in state.aux_calls] == [handoff_mod.SUMMARY_PURPOSE]
    assert state.aux_calls[0]["input_tokens"] == 120
    assert state.aux_calls[0]["output_tokens"] == 12


def test_a_stored_summary_is_continued_not_reread():
    sink = handoff_mod.HandoffSink()
    sink.set_conversation(SimpleNamespace(
        summary="Earlier: the user asked about plans.",
        messages=[SystemMessage(content="folded"), HumanMessage(content="and invoices?")]))
    assert sink.previous_summary == "Earlier: the user asked about plans."
    assert [m.content for m in sink.conversation] == ["and invoices?"]


def test_no_conversation_means_no_summary_call(monkeypatch):
    monkeypatch.setattr("chat.compaction.summarizer_llm",
                        lambda agent: pytest.fail("no model should be built"))
    sink = handoff_mod.HandoffSink(summarizer=object())
    assert handoff_mod.summarize_for_handoff(sink) == ""


# ── the tool ─────────────────────────────────────────────────────────────────

@pytest.fixture
def agents(monkeypatch):
    specs = {
        "front": AgentSpec(id="front", name="Front desk", type="langchain", entrypoint="x",
                           handoffs=["billing", "support", "ghost"]),
        "billing": AgentSpec(id="billing", name="Billing", type="langchain", entrypoint="x",
                             description="Answers invoice questions"),
        "support": AgentSpec(id="support", name="Support", type="langchain", entrypoint="x"),
        "narrow": AgentSpec(id="narrow", name="Narrow", type="langchain", entrypoint="x",
                            handoffs=["billing"], handoff_history="last_n:4"),
    }
    monkeypatch.setattr("agents.registry.get_agent", lambda aid: specs.get(aid))
    return specs


def _tool(spec):
    tools = create_handoff_tools(spec, workspace=None)
    assert [t.name for t in tools] == [HANDOFF_TOOL_NAME]
    return tools[0]


def _call(tool, sink, **args):
    token = handoff_mod.set_sink(sink)
    try:
        return json.loads(tool.invoke(args))
    finally:
        handoff_mod.reset_sink(token)


def test_no_targets_no_tool(agents):
    assert create_handoff_tools(agents["billing"]) == []


def test_the_description_lists_the_targets(agents):
    description = _tool(agents["front"]).description
    assert "`billing` (Billing): Answers invoice questions" in description
    assert "`support` (Support)" in description
    assert "ghost" not in description  # a target that no longer exists is not offered


def test_outside_a_conversation_it_points_at_delegation(agents):
    out = json.loads(_tool(agents["front"]).invoke({"agent_id": "billing", "reason": "invoice"}))
    assert out["ok"] is False and out["code"] == "no_conversation"
    assert "delegate_task_tool" in out["error"]


def test_a_target_not_on_the_list_is_refused(agents):
    sink = handoff_mod.HandoffSink(agent_id="front", chain=["front"])
    out = _call(_tool(agents["front"]), sink, agent_id="narrow", reason="x")
    assert out["code"] == "not_allowed" and "billing" in out["allowed"]
    assert sink.intent is None


def test_an_unknown_target_is_refused(agents):
    sink = handoff_mod.HandoffSink(agent_id="front", chain=["front"])
    out = _call(_tool(agents["front"]), sink, agent_id="ghost", reason="x")
    assert out["code"] == "not_found" and sink.intent is None


def test_a_target_not_usable_in_the_workspace_is_refused(agents, monkeypatch):
    monkeypatch.setattr("common.workspace_context.filter_agents_for_workspace", lambda specs, ws: [])
    sink = handoff_mod.HandoffSink(agent_id="front", chain=["front"], workspace="team-a")
    out = _call(_tool(agents["front"]), sink, agent_id="billing", reason="x")
    assert out["code"] == "forbidden" and sink.intent is None


def test_no_ping_pong(agents):
    sink = handoff_mod.HandoffSink(agent_id="front", chain=["billing", "front"], depth=1)
    out = _call(_tool(agents["front"]), sink, agent_id="billing", reason="back to you")
    assert out["code"] == "already_in_turn" and sink.intent is None


def test_the_turn_limit(agents):
    sink = handoff_mod.HandoffSink(agent_id="front", chain=["a", "b", "c", "front"], depth=3, max_depth=3)
    out = _call(_tool(agents["front"]), sink, agent_id="billing", reason="x")
    assert out["code"] == "too_deep" and sink.intent is None


def test_history_may_only_narrow(agents):
    """A wider filter than the default falls back to the default, and so does
    a value that is no filter at all (models put prose there): the handoff
    still happens, with no more history than the agent was configured for."""
    for asked in ("full", "The user wants a plan for learning French."):
        sink = handoff_mod.HandoffSink(agent_id="narrow", chain=["narrow"])
        out = _call(_tool(agents["narrow"]), sink, agent_id="billing", reason="x", history=asked)
        assert out["ok"] is True and out["history"] == "last_n:4"
        assert "default" in out["history_note"]
        assert sink.intent.history_filter == "last_n:4"
    sink = handoff_mod.HandoffSink(agent_id="narrow", chain=["narrow"])
    out = _call(_tool(agents["narrow"]), sink, agent_id="billing", reason="x", history="last_n:2")
    assert out["ok"] is True and sink.intent.history_filter == "last_n:2"
    assert "history_note" not in out


def test_the_default_filter_applies_when_none_is_asked(agents):
    sink = handoff_mod.HandoffSink(agent_id="narrow", chain=["narrow"])
    _call(_tool(agents["narrow"]), sink, agent_id="billing", reason="x")
    assert sink.intent.history_filter == "last_n:4"


def test_a_handoff_is_recorded_and_ends_the_turn(agents):
    sink = handoff_mod.HandoffSink(agent_id="front", chain=["front"])
    state = agent_loop.LoopState(run_id="r1")
    token = agent_loop.set_state(state)
    try:
        out = _call(_tool(agents["front"]), sink, agent_id="billing",
                    reason="  invoice   question ", note="invoice 42")
    finally:
        agent_loop.reset_state(token)
    assert out["ok"] is True and out["handed_over_to"] == "billing"
    intent = sink.intent
    assert (intent.from_agent_id, intent.to_agent_id, intent.to_agent_name) == ("front", "billing", "Billing")
    assert intent.reason == "invoice question" and intent.note == "invoice 42"
    assert intent.history_filter == "full"
    assert state.ended_by == {"tool": HANDOFF_TOOL_NAME, "output": "invoice question",
                              "prefer_model_text": True}
    assert state.summary()["ended_by_tool"] == HANDOFF_TOOL_NAME
    # A second call in the same turn changes nothing.
    again = _call(_tool(agents["front"]), sink, agent_id="support", reason="y")
    assert again["code"] == "already_handed_over" and sink.intent.to_agent_id == "billing"


def test_a_summary_handoff_writes_the_summary_during_the_call(agents, monkeypatch):
    monkeypatch.setattr("chat.compaction.summarizer_llm", lambda agent: _FakeSummariser())
    sink = handoff_mod.HandoffSink(agent_id="front", chain=["front"], summarizer=object())
    sink.set_conversation(SimpleNamespace(summary="", messages=_history()))
    _call(_tool(agents["front"]), sink, agent_id="billing", reason="x", history="summary")
    assert sink.intent.summary == "The user disputes invoice 42."


# ── the registry field ───────────────────────────────────────────────────────

def test_the_record_round_trips_and_drops_itself():
    spec = registry._validate_agent_dict({
        "id": "front", "name": "Front", "type": "langchain", "entrypoint": "a.b:c",
        "handoffs": ["billing", "front", "billing", " support "], "handoff_history": "LAST_N:6",
    })
    assert spec.handoffs == ["billing", "support"]
    assert spec.handoff_history == "last_n:6"
    stored = spec.to_dict()
    assert stored["handoffs"] == ["billing", "support"] and stored["handoff_history"] == "last_n:6"
    plain = registry._validate_agent_dict({"id": "x", "name": "X", "type": "langchain",
                                           "entrypoint": "a.b:c", "handoff_history": "bogus"})
    assert plain.handoffs == [] and plain.handoff_history == "full"
    assert "handoffs" not in plain.to_dict() and "handoff_history" not in plain.to_dict()


def test_versions_fingerprint_the_handoff_fields():
    from agents.versions import _spec_parts
    base = AgentSpec(id="a", name="A", type="langchain", entrypoint="x")
    assert "handoffs" not in _spec_parts(base)
    parts = _spec_parts(AgentSpec(id="a", name="A", type="langchain", entrypoint="x",
                                  handoffs=["b"], handoff_history="none"))
    assert parts["handoffs"] == ["b"] and parts["handoff_history"] == "none"


# ── the agent loop ends a turn on a tool's word ──────────────────────────────

def _executor(tool, replies):
    """A real AgentExecutor over the hub's loop runnable, streaming as in a
    chat turn. The fake model has no stream of its own, so LangChain streams
    each reply whole, tool calls included."""
    from langchain.agents import AgentExecutor
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

    class _FakeToolCalling(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    llm = _FakeToolCalling(responses=list(replies))
    prompt = ChatPromptTemplate.from_messages([
        ("system", "SYS"), ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    return AgentExecutor(agent=agent_loop.build_agent_runnable(llm, [tool], prompt),
                         tools=[tool], return_intermediate_steps=True)


def _ending_tool(prefer_model_text):
    from langchain_core.tools import tool

    @tool("finish_it")
    def finish_it(x: str) -> str:
        """End the turn."""
        agent_loop.end_turn("the tool's text", tool="finish_it", prefer_model_text=prefer_model_text)
        return "done"
    return finish_it


def _call_turn(text="Passing you on."):
    return AIMessage(content=text, tool_calls=[{"name": "finish_it", "args": {"x": "1"}, "id": "c1"}])


@pytest.mark.parametrize("prefer,expected", [(True, "Passing you on."), (False, "the tool's text")])
def test_end_turn_finishes_without_another_model_call(prefer, expected):
    executor = _executor(_ending_tool(prefer), [_call_turn(), AIMessage(content="NEVER")])
    state = agent_loop.LoopState()
    token = agent_loop.set_state(state)
    try:
        out = executor.invoke({"input": "go"})
    finally:
        agent_loop.reset_state(token)
    assert out["output"] == expected
    assert state.model_calls == 1


def test_prefer_model_text_falls_back_to_the_tool_text():
    executor = _executor(_ending_tool(True), [_call_turn(text=""), AIMessage(content="NEVER")])
    token = agent_loop.set_state(agent_loop.LoopState())
    try:
        out = executor.invoke({"input": "go"})
    finally:
        agent_loop.reset_state(token)
    assert out["output"] == "the tool's text"


def test_end_turn_outside_a_loop_is_a_no_op():
    assert agent_loop.end_turn("x") is False


def test_return_direct_is_honoured_through_a_wrapper():
    """The approval guard and the think gate wrap tools without copying
    ``return_direct``; the loop looks through ``.inner`` and ends the turn."""
    from langchain_core.tools import BaseTool, tool

    @tool("finish_it", return_direct=True)
    def inner(x: str) -> str:
        """Answer directly."""
        return "direct answer"

    class Wrapper(BaseTool):
        name: str = "finish_it"
        description: str = "wrapped"
        inner: BaseTool

        def _run(self, *args, **kwargs):
            kwargs.pop("run_manager", None)
            return self.inner.run(kwargs or args[0])

    wrapped = Wrapper(inner=inner, args_schema=inner.args_schema)
    assert wrapped.return_direct is False
    executor = _executor(wrapped, [_call_turn(), AIMessage(content="NEVER")])
    token = agent_loop.set_state(agent_loop.LoopState())
    try:
        out = executor.invoke({"input": "go"})
    finally:
        agent_loop.reset_state(token)
    assert out["output"] == "direct answer"


def test_a_turn_ended_by_a_tool_skips_the_answer_schema():
    from agents.standard_agent import StandardAgent
    agent = StandardAgent(agent_id="a", name="A", system_prompt="", tools=[])
    state = agent_loop.LoopState()
    state.ended_by = {"tool": HANDOFF_TOOL_NAME, "output": "x"}
    assert agent._structured_output(state, "plain text, no schema here") == ("plain text, no schema here", None)
