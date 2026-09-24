"""
The agent loop's own chain (agents/agent_loop.py): with no extension it
behaves like LangChain's tool-calling chain, and each hook sees and changes
what it is documented to.
"""
from __future__ import annotations

from typing import Any, List

from langchain.agents import AgentExecutor
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

from agents import agent_loop
from agents.agent_loop import LoopExtension, LoopState, build_agent_runnable


class _ToolModel(GenericFakeChatModel):
    """A fake chat model that records what it was sent and bound."""

    seen: List[Any] = []
    bound: List[Any] = []

    def bind_tools(self, tools, **kwargs):
        type(self).bound.append(([getattr(t, "name", t) for t in tools], kwargs))
        return self.bind(tools=[getattr(t, "name", t) for t in tools], **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        type(self).seen.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)



@tool
def echo(text: str) -> str:
    """Echo the text back."""
    return f"echo:{text}"


@tool
def other(text: str) -> str:
    """Another tool."""
    return "other"


def _prompt():
    return ChatPromptTemplate.from_messages([
        SystemMessage(content="sys"),
        MessagesPlaceholder(variable_name="chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])


def _model():
    _ToolModel.seen = []
    _ToolModel.bound = []
    # The executor streams the agent runnable; the fake's own streaming drops
    # tool calls, so it answers whole (the chain under test is the same).
    return _ToolModel(disable_streaming=True, messages=iter([
        AIMessage(content="", tool_calls=[{"name": "echo", "args": {"text": "hi"}, "id": "c1"}]),
        AIMessage(content="done"),
    ]))


def _executor(extensions=None):
    tools = [echo, other]
    runnable = build_agent_runnable(_model(), tools, _prompt(), extensions)
    return AgentExecutor(agent=runnable, tools=tools, return_intermediate_steps=True)


def test_plain_chain_runs_tools_and_finishes():
    result = _executor().invoke({"input": "go"})
    assert result["output"] == "done"
    assert [a.tool for a, _ in result["intermediate_steps"]] == ["echo"]
    # Both tools bound on both calls, and the second call carries the tool result.
    assert [names for names, _ in _ToolModel.bound] == [["echo", "other"], ["echo", "other"]]
    second = _ToolModel.seen[-1]
    assert any("echo:hi" in str(getattr(m, "content", "")) for m in second)


class _Probe(LoopExtension):
    name = "probe"

    def shape_messages(self, state, inputs, scratchpad):
        state.scratch.setdefault("calls", 0)
        state.scratch["calls"] += 1
        return [*scratchpad, HumanMessage(content=f"note {state.scratch['calls']}")]

    def select_tools(self, state, tools):
        return [t for t in tools if t.name == "echo"]

    def bind_kwargs(self, state, llm, tools):
        return {"strict": True}

    def model_kwargs(self, state, llm):
        return {"extra_param": 1}


def test_extension_hooks_shape_select_and_bind():
    state = LoopState(run_id="r1")
    token = agent_loop.set_state(state)
    try:
        result = _executor([_Probe()]).invoke({"input": "go"})
    finally:
        agent_loop.reset_state(token)
    assert result["output"] == "done"
    assert state.model_calls == 2
    assert [names for names, _ in _ToolModel.bound] == [["echo"], ["echo"]]
    assert all(kw == {"strict": True} for _, kw in _ToolModel.bound)
    assert "note 2" in str(_ToolModel.seen[-1][-1].content)


class _Broken(LoopExtension):
    name = "broken"

    def shape_messages(self, state, inputs, scratchpad):
        raise RuntimeError("boom")

    def select_tools(self, state, tools):
        raise RuntimeError("boom")


def test_broken_extension_falls_back_to_plain_chain():
    result = _executor([_Broken()]).invoke({"input": "go"})
    assert result["output"] == "done"
    assert [names for names, _ in _ToolModel.bound][0] == ["echo", "other"]


def test_state_summary_keeps_only_non_empty_sections():
    state = LoopState()
    assert state.summary() == {}
    state.answered_by.append({"provider": "openai", "model": "b", "fallback": True})
    state.loaded_tools.append("x")
    summary = state.summary()
    assert summary["fallback_used"] is True
    assert summary["loaded_tools"] == ["x"]
    assert "injections" not in summary


def test_load_extensions_skips_missing_modules(monkeypatch):
    monkeypatch.setattr(agent_loop, "EXTENSION_MODULES", ("agents.loop_ext.nope_missing",))
    assert agent_loop.load_extensions(object()) == []


def test_nested_state_does_not_take_the_parent_run_id_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_RUN_ID", "parent-run")
    outer = agent_loop.new_state(None)
    assert outer.run_id == "parent-run"
    token = agent_loop.set_state(outer)
    try:
        inner = agent_loop.new_state(None)
        assert inner.run_id == ""
        assert agent_loop.new_state(None, run_id="child").run_id == "child"
    finally:
        agent_loop.reset_state(token)


def test_a_cancelled_run_leaves_its_summary_for_the_canceller():
    state = LoopState(run_id="chat-run-1")
    state.injections.append({"after_step": 1, "text": "stop that", "msg_id": "m1"})
    agent_loop.keep_cancelled(state)
    assert agent_loop.pop_cancelled_summary("chat-run-1")["injections"][0]["text"] == "stop that"
    assert agent_loop.pop_cancelled_summary("chat-run-1") == {}   # once
    agent_loop.keep_cancelled(LoopState())   # no run id: nothing kept
    assert agent_loop.pop_cancelled_summary("") == {}
