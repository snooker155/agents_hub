"""Every finished tool call says how it went: ``status`` ok or error, on the
live event, in the stored history and in what the log parser rebuilds."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException, tool

from agents.callbacks.chat_stream import ChatStreamCallback
from agents.callbacks.run_statistics import StatsCollectorCallback, tool_status
from chat.entity_chat import settle_tool_step
from dashboard.backend.routes.sessions import _attach_tool_verdicts


@tool("lookup")
def fake_lookup(key: str) -> str:
    """Look a key up."""
    if key == "missing":
        return json.dumps({"ok": False, "error": "no such key"})
    if key == "boom":
        raise ValueError("exploded")
    return json.dumps({"ok": True, "value": key})


@tool("handled")
def fake_handled(key: str) -> str:
    """Fail through LangChain's own error handling."""
    raise ToolException("bad key")


fake_handled.handle_tool_error = True


@pytest.mark.parametrize("output,expected", [
    ('{"ok": true}', "ok"),
    ('{"ok": false, "error": "x"}', "error"),
    ("  ERROR: boom", "error"),
    ("Error: boom", "error"),
    ("the error was fixed", "ok"),
    ("{not json", "ok"),
    (ToolMessage(content="fine", tool_call_id="1", status="error"), "error"),
    (ToolMessage(content='{"ok": false}', tool_call_id="1"), "error"),
    (ToolMessage(content="fine", tool_call_id="1"), "ok"),
])
def test_tool_status(output, expected):
    assert tool_status(output) == expected


def _chat(events: list, tmp_path) -> ChatStreamCallback:
    class _Chat(ChatStreamCallback):
        def _emit(self, payload: dict):
            events.append(payload)

        def _abort_if_cancelled(self):
            return None

    return _Chat(None, None, [], tmp_path / "chat.log", run_id="status-run")


def _call(t, args, callbacks):
    try:
        return t.invoke(args, config={"callbacks": callbacks})
    except Exception as exc:  # noqa: BLE001  (the raise is the case under test)
        return exc


def test_events_and_history_carry_the_status(tmp_path):
    events: list = []
    chat = _chat(events, tmp_path)
    stats = StatsCollectorCallback()
    for key in ("found", "missing", "boom"):
        _call(fake_lookup, {"key": key}, [stats, chat])
    # The agent calls a tool with a tool call, so a handled failure comes back
    # as a ToolMessage marked status="error".
    _call(fake_handled, {"name": "handled", "args": {"key": "x"}, "id": "c1", "type": "tool_call"},
          [stats, chat])

    ends = [e for e in events if e["type"] in ("tool_end", "tool_error")]
    assert [(e["type"], e["status"]) for e in ends] == [
        ("tool_end", "ok"), ("tool_end", "error"), ("tool_error", "error"), ("tool_end", "error"),
    ]
    assert [c["status"] for c in stats.tool_history] == ["ok", "error", "error", "error"]
    assert [c["status"] for c in chat.tool_history] == ["ok", "error", "error", "error"]
    # The consolidated marker says so too, so a log alone tells a failed call.
    marks = [m for m in chat.thinking_history if m.startswith("[tool_call]")]
    assert ["status=error" in m for m in marks] == [False, True, True, True]


def test_log_parser_closes_a_raised_call_and_reads_the_marker():
    tools = [
        {"step": 1, "tool": "lookup", "output": "{}", "running": False},
        {"step": 2, "tool": "lookup", "output": '{"ok": false}', "running": False},
    ]
    log = "[tool_call] 10:00:00 step=2 tool=lookup duration_ms=3 status=error\n"
    _attach_tool_verdicts(tools, log)
    assert "status" not in tools[0]
    assert tools[1]["status"] == "error"


def test_stored_trace_settles_its_tool_step():
    items = [{"k": "tool", "tool": "lookup", "status": "running"}]
    assert settle_tool_step(items, {"type": "tool_end", "tool": "lookup", "status": "ok"})
    assert items[0]["status"] == "done"

    items = [{"k": "tool", "tool": "lookup", "status": "done"}]
    assert settle_tool_step(items, {"type": "tool_end", "tool": "lookup", "status": "error"})
    assert items[0]["status"] == "error"

    items = [{"k": "tool", "tool": "lookup", "status": "done"}]
    assert settle_tool_step(items, {"type": "tool_error", "tool": "lookup", "error": "exploded"})
    assert items[0] == {"k": "tool", "tool": "lookup", "status": "error", "error": "exploded"}

    # A call the trace left out never settles the step before it.
    items = [{"k": "tool", "tool": "lookup", "status": "done"}]
    assert not settle_tool_step(items, {"type": "tool_end", "tool": "add_graph_node", "status": "error"})
    assert items[0]["status"] == "done"
