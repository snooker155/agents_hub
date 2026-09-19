"""Which tool drove which LLM call.

An agent loop is call → tool(s) → call, and the run record's LLM-invocation
list used to say only "kind: tool" — that *some* tool had run by then. Eight
identical rows saying "tool-driven LLM call" tell a reader nothing; the names
of the tools whose output the model was handed tell them the loop. These pin
the bookkeeping that makes that possible.
"""
import asyncio

import pytest

from agents.callbacks.chat_stream import ChatStreamCallback


class _Gen:
    def __init__(self, text):
        self.text = text


class _Response:
    """The shape on_llm_end reads: generations + (absent) usage metadata."""

    def __init__(self, text=""):
        self.generations = [[_Gen(text)]]
        self.llm_output = {}


@pytest.fixture
def callback(tmp_path):
    loop = asyncio.new_event_loop()
    try:
        yield ChatStreamCallback(loop, asyncio.Queue(), [], tmp_path / "run.log")
    finally:
        loop.close()


def _call(cb, text="ok"):
    cb.on_llm_start({}, ["prompt"])
    cb.on_llm_end(_Response(text))


def _tool(cb, name):
    cb.on_tool_start({"name": name}, "{}")
    cb.on_tool_end("{}")


def test_the_first_call_has_no_tools_behind_it(callback):
    _call(callback)
    assert [i["tools"] for i in callback.llm_invocations] == [[]]
    assert callback.llm_invocations[0]["kind"] == "llm"


def test_each_call_names_the_tools_that_ran_before_it(callback):
    _call(callback)
    _tool(callback, "list_environments_tool")
    _call(callback)
    _tool(callback, "create_scenario_tool")
    _call(callback)

    invocations = callback.llm_invocations
    assert [i["tools"] for i in invocations] == [
        [], ["list_environments_tool"], ["create_scenario_tool"],
    ]
    # Everything after the first tool is a tool-driven call, as before.
    assert [i["kind"] for i in invocations] == ["llm", "tool", "tool"]


def test_parallel_tool_calls_all_land_on_the_call_they_fed(callback):
    """Two tools between one pair of calls belong to the same follow-up."""
    _call(callback)
    _tool(callback, "validate_scenario_tool")
    _tool(callback, "create_scenario_tool")
    _call(callback)

    assert callback.llm_invocations[1]["tools"] == [
        "validate_scenario_tool", "create_scenario_tool",
    ]


def test_a_tool_is_never_charged_to_two_calls(callback):
    """The list is claimed at llm_start, so it cannot repeat on the next one."""
    _call(callback)
    _tool(callback, "think")
    _call(callback)
    _call(callback)

    assert [i["tools"] for i in callback.llm_invocations] == [[], ["think"], []]
