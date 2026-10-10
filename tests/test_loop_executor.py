"""The hub's own executor (agents/loop_executor.py) keeps the contract of the
one it replaced: the result shape, the callback events and their order, tool
errors raised through, the iteration limit, ``return_direct``, an unknown
tool name and an unreadable model answer as observations, and concurrent
tools on the async path.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, List

import pytest
from langchain_core.agents import AgentFinish
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool

from agents.agent_loop import build_agent_runnable, format_steps, parse_actions
from agents.loop_executor import PARSING_ERROR_OBSERVATION, STOPPED_OUTPUT, LoopExecutor


class _Model(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self.bind(tools=[getattr(t, "name", t) for t in tools], **kwargs)


@tool
def echo(text: str) -> str:
    """Echo the text back."""
    return f"echo:{text}"


@tool
def boom(text: str) -> str:
    """Always fails."""
    raise RuntimeError(f"boom:{text}")


@tool(return_direct=True)
def final(text: str) -> str:
    """Answer directly."""
    return f"direct:{text}"


@tool
async def slow(text: str) -> str:
    """Sleeps a little."""
    await asyncio.sleep(0.2)
    return f"slow:{text}"


def _prompt():
    return ChatPromptTemplate.from_messages([
        SystemMessage(content="sys"),
        MessagesPlaceholder(variable_name="chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])


def _call(name: str, text: str, id_: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": {"text": text}, "id": id_}])


def _executor(replies: List[AIMessage], tools: List[Any], **kw) -> LoopExecutor:
    model = _Model(disable_streaming=True, messages=iter(replies))
    return LoopExecutor(agent=build_agent_runnable(model, tools, _prompt(), []), tools=tools, **kw)


class _Events(BaseCallbackHandler):
    def __init__(self) -> None:
        self.events: List[str] = []

    def on_chain_start(self, serialized, inputs, **kwargs):
        self.events.append(f"chain_start:{(serialized or {}).get('name', '?')}")

    def on_chain_end(self, outputs, **kwargs):
        self.events.append("chain_end")

    def on_chain_error(self, error, **kwargs):
        self.events.append(f"chain_error:{type(error).__name__}")

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self.events.append("model_start")

    def on_llm_end(self, response, **kwargs):
        self.events.append("model_end")

    def on_tool_start(self, serialized, input_str, **kwargs):
        self.events.append(f"tool_start:{(serialized or {}).get('name')}")

    def on_tool_end(self, output, **kwargs):
        self.events.append("tool_end")

    def on_tool_error(self, error, **kwargs):
        self.events.append(f"tool_error:{type(error).__name__}")

    def on_agent_action(self, action, **kwargs):
        self.events.append(f"agent_action:{action.tool}")

    def on_agent_finish(self, finish, **kwargs):
        self.events.append("agent_finish")


def test_the_result_and_the_event_order():
    events = _Events()
    out = _executor([_call("echo", "hi", "c1"), AIMessage(content="done")], [echo]).invoke(
        {"input": "go"}, config={"callbacks": [events]})
    assert out["output"] == "done"
    action, observation = out["intermediate_steps"][0]
    assert action.tool == "echo" and action.tool_input == {"text": "hi"} and action.tool_call_id == "c1"
    assert observation == "echo:hi"
    loop_events = [e for e in events.events if not e.startswith("chain_start:") or e == "chain_start:LoopExecutor"]
    assert [e for e in loop_events if e != "chain_end" or loop_events.index(e) == len(loop_events) - 1][:9] == [
        "chain_start:LoopExecutor", "model_start", "model_end", "agent_action:echo", "tool_start:echo",
        "tool_end", "model_start", "model_end", "agent_finish"]
    assert events.events[-1] == "chain_end"


def test_a_tool_error_raises_through_the_executor():
    events = _Events()
    with pytest.raises(RuntimeError, match="boom:x"):
        _executor([_call("boom", "x", "c1")], [boom]).invoke({"input": "go"}, config={"callbacks": [events]})
    assert "tool_error:RuntimeError" in events.events and events.events[-1] == "chain_error:RuntimeError"


def test_an_unknown_tool_is_an_observation_the_model_sees():
    out = _executor([_call("nothing", "x", "c1"), AIMessage(content="ok")], [echo]).invoke({"input": "go"})
    _action, observation = out["intermediate_steps"][0]
    assert observation == "nothing is not a valid tool, try one of [echo]."
    assert out["output"] == "ok"


def test_the_iteration_limit_stops_the_loop():
    replies = [_call("echo", str(i), f"c{i}") for i in range(5)]
    out = _executor(replies, [echo], max_iterations=2).invoke({"input": "go"})
    assert out["output"] == STOPPED_OUTPUT and len(out["intermediate_steps"]) == 2


def test_a_return_direct_tool_ends_the_turn_with_its_output():
    out = _executor([_call("final", "x", "c1"), AIMessage(content="never")], [final]).invoke({"input": "go"})
    assert out["output"] == "direct:x" and len(out["intermediate_steps"]) == 1


def test_an_unreadable_answer_becomes_an_observation():
    def broken(_inputs):
        raise OutputParserException("bad json")

    executor = LoopExecutor(agent=RunnableLambda(broken), tools=[echo], max_iterations=1)
    out = executor.invoke({"input": "go"})
    action, observation = out["intermediate_steps"][0]
    assert action.tool == "_Exception" and observation == PARSING_ERROR_OBSERVATION
    assert out["output"] == STOPPED_OUTPUT


def test_parallel_tool_calls_run_concurrently_on_the_async_path():
    first = AIMessage(content="", tool_calls=[
        {"name": "slow", "args": {"text": "a"}, "id": "c1"},
        {"name": "slow", "args": {"text": "b"}, "id": "c2"}])
    executor = _executor([first, AIMessage(content="done")], [slow])
    started = time.perf_counter()
    out = asyncio.run(executor.ainvoke({"input": "go"}))
    assert time.perf_counter() - started < 0.35
    assert [obs for _a, obs in out["intermediate_steps"]] == ["slow:a", "slow:b"]
    assert out["output"] == "done"


def test_the_trail_is_written_back_as_messages():
    first = _call("echo", "hi", "c1")
    actions = parse_actions(first)
    assert [a.tool_call_id for a in actions] == ["c1"]
    messages = format_steps([(actions[0], "echo:hi")])
    assert messages[0] is first
    assert messages[1].tool_call_id == "c1" and messages[1].content == "echo:hi"
    assert messages[1].additional_kwargs["name"] == "echo"
    assert isinstance(parse_actions(AIMessage(content="done")), AgentFinish)
