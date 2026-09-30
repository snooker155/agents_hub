"""
DelegationStreamCallback: a delegated worker's steps on the parent's stream.

Tool calls were forwarded already; what the worker's model says at each step
(its native reasoning and the text it writes between tool calls) is forwarded
too, so the chat's nested card shows the worker's whole process.
"""
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from agents.callbacks.chat_stream import DelegationStreamCallback


def _result(content):
    return LLMResult(generations=[[ChatGeneration(message=AIMessage(content=content))]])


def _callback():
    events = []
    cb = DelegationStreamCallback(events.append, run_id="child", agent_id="visualizer", depth=1)
    return cb, events


def test_forwards_the_text_a_worker_writes_between_steps():
    cb, events = _callback()
    cb.on_llm_end(_result("Scene created, adding the cube."))
    assert events == [{
        "delegation": True, "run_id": "child", "agent_id": "visualizer", "depth": 1,
        "type": "text", "content": "Scene created, adding the cube.",
    }]


def test_splits_inline_reasoning_from_the_text():
    cb, events = _callback()
    cb.on_llm_end(_result("<think>A cube first.</think>Building it now."))
    assert [e["type"] for e in events] == ["think", "text"]
    assert events[0]["content"] == "A cube first." and events[0]["native"] is True
    assert events[1]["content"] == "Building it now."


def test_a_tool_call_only_step_sends_nothing():
    cb, events = _callback()
    cb.on_llm_end(_result(""))
    assert events == []
