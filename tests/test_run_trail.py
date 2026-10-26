"""build_run_trail: the chat-shaped timeline, files and stats of one run."""
import asyncio
import json

import pytest
from fastapi import HTTPException

from dashboard.backend.routes import messages
from chat.entity_chat import build_turn_items, settle_tool_step
from chat.flow_driver import _node_files, _node_step_stats


def _chat_log(path):
    path.write_text(
        "=== Message at 2026-01-01T00:00:00 id=m1 ===\n"
        "Agent: a1\n--- User message ---\nhi\n--- Agent response (stream) ---\n"
        f"[reasoning] step=1 content={json.dumps('plan it')}\n"
        "[tool_start] step=1 tool=read_file input={\"p\": 1}\n"
        "[tool_end] output=hello\n"
        "[tool_start] step=2 tool=run_shell input=ls\n"
        "[tool_end] output=ERROR: nope\n"
        "[tool_start] step=3 tool=think input=x\n"
        "[tool_end] output=ok\n"
        "final answer\n"
        "Finished: now\nStatus  : completed\n", encoding="utf-8")


def _patch(monkeypatch, run, artifacts=()):
    monkeypatch.setattr(messages.run_manager, "get_run_by_id", lambda rid: run if rid == run["run_id"] else None)
    monkeypatch.setattr(messages.run_manager, "get_run_process", lambda rid: {"artifacts": list(artifacts)})


def test_chat_log_trail(tmp_path, monkeypatch):
    log = tmp_path / "r.log"
    _chat_log(log)
    _patch(monkeypatch, {"run_id": "r1", "log_file": str(log), "output": ""},
           [{"op": "write", "path": "a.txt", "additions": 1, "deletions": 0}])
    trail = messages.build_run_trail("r1")
    tl = trail["timeline"]
    assert tl[0] == {"type": "reasoning", "kind": "think", "step": 1, "content": "plan it"}
    assert [(e["type"], e.get("tool")) for e in tl[1:4]] == [
        ("tool", "read_file"), ("tool", "run_shell"), ("tool", "think")]
    assert tl[1]["status"] == "ok" and tl[1]["output"] == "hello"
    assert tl[2]["status"] == "error"
    assert tl[-1] == {"type": "text", "text": "final answer"}
    # A change no call names comes after every step, before the answer.
    assert tl[-2] == {"type": "artifact", "op": "write", "path": "a.txt", "additions": 1, "deletions": 0}
    assert trail["stats"] == {"tools": 2, "errors": 1, "thoughts": 1, "files": 1}
    assert trail["files"] == [{"op": "write", "path": "a.txt", "additions": 1, "deletions": 0, "run_id": "r1"}]


def test_file_step_follows_the_call_that_names_it():
    timeline = [
        {"type": "tool", "tool": "write_file", "input": '{"path": "out/r.md"}'},
        {"type": "tool", "tool": "read_file", "input": '{"path": "x"}'},
    ]
    messages._place_file_steps(timeline, [
        {"op": "add", "path": "out/r.md", "diff": "+a", "run_id": "r1"},
        {"op": "add", "path": "other.md", "run_id": "r1"},
    ])
    assert [(e["type"], e.get("path")) for e in timeline] == [
        ("tool", None), ("artifact", "out/r.md"), ("tool", None), ("artifact", "other.md")]
    assert timeline[1]["diff"] == "+a" and "run_id" not in timeline[1]


def test_worker_log_and_record_output(tmp_path, monkeypatch):
    log = tmp_path / "w.log"
    log.write_text("[tool_start] step=1 tool=t input=i\n[tool_end] output={\"ok\": false}\n", encoding="utf-8")
    _patch(monkeypatch, {"run_id": "w1", "log_file": str(log), "output": "from record"})
    tl = messages.build_run_trail("w1")["timeline"]
    assert tl[0]["status"] == "error"
    assert tl[-1] == {"type": "text", "text": "from record"}


def test_clipping_and_missing_log(tmp_path, monkeypatch):
    log = tmp_path / "c.log"
    log.write_text("[tool_start] step=1 tool=t input=" + "x" * 3000 + "\n[tool_end] output=" + "y" * 3000 + "\n",
                   encoding="utf-8")
    _patch(monkeypatch, {"run_id": "c1", "log_file": str(log), "output": ""})
    tool = messages.build_run_trail("c1")["timeline"][0]
    assert len(tool["input"]) == 2000 + len("… (truncated)") and tool["output"].endswith("(truncated)")
    _patch(monkeypatch, {"run_id": "c2", "log_file": str(tmp_path / "gone.log"), "output": ""})
    assert messages.build_run_trail("c2") == {"run_id": "c2", "timeline": [], "files": [],
                                              "stats": {"tools": 0, "errors": 0, "thoughts": 0, "files": 0}}


def test_route_404(monkeypatch):
    monkeypatch.setattr(messages.run_manager, "get_run_by_id", lambda rid: None)
    assert messages.build_run_trail("zz") is None
    with pytest.raises(HTTPException) as exc:
        asyncio.run(messages.get_message_trail("zz"))
    assert exc.value.status_code == 404


def test_node_step_stats():
    class CB:
        tool_history = [{"tool": "a", "status": "ok"}, {"tool": "b", "status": "error"},
                        {"tool": "think", "status": "ok"}, {"tool": "plan"}]
    assert _node_step_stats(CB(), ["[reasoning] step=1 content=\"x\"", "other"]) == \
        {"tools": 2, "errors": 1, "thoughts": 1, "files": 0}


def test_node_files():
    """A node's files on its flow-log line: one per path, the last change
    wins, the name and link without the diff."""
    class CB:
        artifact_history = [
            {"op": "add", "path": "r.md", "diff": "+a", "additions": 1, "url": "/artifacts?file=f1"},
            {"op": "modify", "path": "r.md", "diff": "+b", "additions": 1, "url": "/artifacts?file=f1"},
            {"op": "delete", "path": "old.md", "diff": "-x", "deletions": 1},
        ]
    assert _node_files(CB()) == [
        {"op": "modify", "path": "r.md", "url": "/artifacts?file=f1"},
        {"op": "delete", "path": "old.md"},
    ]
    assert _node_step_stats(CB(), [])["files"] == 2
    assert _node_files(object()) == []


def test_entity_trace_timeline():
    ev = [
        {"type": "think", "step": 1, "content": "hm"},
        {"type": "token", "token": "Let me "}, {"type": "token", "token": "look."},
        {"type": "tool_start", "tool": "t", "input": "i"},
        {"type": "tool_end", "tool": "t", "status": "ok", "output": "o" * 2500},
        {"type": "artifact", "op": "write", "path": "f", "additions": 2, "deletions": 1, "diff": "zzz"},
        {"type": "token", "token": "  "},
        {"type": "token", "token": "Done"},
        {"type": "message", "content": "Done!", "run_id": "r9"},
    ]
    items = build_turn_items("q", ev)
    assert items[1] == {"k": "thinking", "text": "hm", "step": 1}
    assert items[2] == {"k": "text", "text": "Let me look."}
    assert items[3]["k"] == "tool" and items[3]["status"] == "done"
    assert items[3]["output"].endswith("… (truncated)") and len(items[3]["output"]) < 2100
    assert items[4] == {"k": "artifact", "op": "write", "path": "f", "additions": 2, "deletions": 1}
    assert items[5] == {"k": "assistant", "text": "Done!", "run_id": "r9"}
    assert len(items) == 6


def test_tool_error_keeps_error():
    items = [{"k": "tool", "tool": "t", "status": "running"}]
    settle_tool_step(items, {"type": "tool_error", "tool": "t", "error": "bad"})
    assert items[0]["status"] == "error" and items[0]["error"] == "bad"


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params):
        return _Rows([r for r in self.rows if r["task_id"] in params])


def test_old_flow_log_lines_find_their_node_run(monkeypatch):
    """A node line written before it carried run_id gets its run on read."""
    from common import db
    from dashboard.backend.routes import flows
    rows = [
        {"run_id": "r-a", "task_id": "conv-1", "finished_at": "T1", "extra": json.dumps({"flow_node_id": "n1"})},
        {"run_id": "r-b", "task_id": "conv-1", "finished_at": "T2", "extra": json.dumps({"flow_node_id": "n1"})},
        {"run_id": "r-c", "task_id": "conv-1", "finished_at": "T3", "extra": {"flow_node_id": "n2"}},
    ]
    monkeypatch.setattr(db, "get_conn", lambda: _Conn(rows))
    events = [
        {"type": "agent_finish", "node_id": "n1", "timestamp": "T2", "run_group": "conv-1", "kind": "chat"},
        {"type": "agent_error", "node_id": "n2", "timestamp": "T9", "run_group": "conv-1", "kind": "chat"},
        {"type": "agent_finish", "node_id": "n1", "timestamp": "T1", "run_group": "conv-1", "kind": "chat",
         "run_id": "kept"},
        {"type": "agent_start", "node_id": "n1", "timestamp": "T0", "run_group": "conv-1", "kind": "chat"},
    ]
    out = flows._attach_node_runs(events)
    assert out[0]["run_id"] == "r-b"          # exact finish time
    assert out[1]["run_id"] == "r-c"          # the node's only run
    assert out[2]["run_id"] == "kept"         # already linked
    assert "run_id" not in out[3]             # a start line is not a node's end


def test_a_flow_node_run_without_recorded_changes_lists_the_files_its_tools_wrote(tmp_path, monkeypatch):
    """A flow node run from before the task driver caught file changes: the
    files come from its log's tool lines, shown as they are now."""
    log = tmp_path / "node.log"
    log.write_text(
        "--- Node run started at now ---\nAgent: writer\n\n=== EXECUTION ===\n"
        "[tool_start] tool=read_file input={'path': 'notes.md'}\n[tool_end] output=x\n"
        "[tool_start] tool=create_file input={'path': 'out/a.md', 'content': 'x'}\n[tool_end] output=ok\n"
        "[tool_start] tool=write_file input={'path': 'out/a.md', 'content': 'y'}\n[tool_end] output=ok\n"
        "[tool_start] tool=write_file input={'path': 'out/b.md', 'content': 'z'}\n[tool_end] output=ok\n",
        encoding="utf-8")
    _patch(monkeypatch, {"run_id": "n1", "log_file": str(log), "output": "done"})
    files = messages.build_run_trail("n1")["files"]
    assert files == [{"op": "add", "path": "out/a.md", "run_id": "n1"},
                     {"op": "modify", "path": "out/b.md", "run_id": "n1"}]


def test_a_background_flow_node_is_recorded_as_a_chat_node_is(monkeypatch, tmp_path):
    """flow/task_driver.py watches a node with the chat callback: its log is a
    chat log (read by the same parser), its record holds the tool calls and
    the files its tools changed, and its flow-log line gets the step counts."""
    import argparse
    from common import artifact_sink
    from flow import task_driver
    from agents.callbacks import ChatStreamCallback

    closed = {}
    log_path = tmp_path / "node.log"
    monkeypatch.setattr(task_driver, "open_run", lambda *a, **k: None)
    monkeypatch.setattr(task_driver, "update_run", lambda *a, **k: None)
    monkeypatch.setattr(task_driver, "run_log_path", lambda rid: log_path)
    monkeypatch.setattr(task_driver, "close_run", lambda rid, **kw: closed.update(kw))

    class _Inv:
        duration_ms = 5
        process = {}

    def _lifecycle(agent_id, ws, prompt, *, on_success, extra_callbacks, **kw):
        cb = next(c for c in extra_callbacks if isinstance(c, ChatStreamCallback))
        cb.on_tool_start({"name": "write_file"}, "{'path': 'out/a.md'}")
        artifact_sink.record_artifact("add", "out/a.md", None, "hello\n")
        cb.on_tool_end("ok")
        return on_success(type("R", (), {"agent_output": "done"})(), _Inv())

    monkeypatch.setattr(task_driver, "run_agent_lifecycle", _lifecycle)
    monkeypatch.setattr(task_driver._SessionPublishCallback, "_post", lambda self, payload: None)
    monkeypatch.setattr(task_driver._SessionPublishCallback, "publish_done", lambda self, *a: None)
    args = argparse.Namespace(flow_id="f", run_id="fr", task_id=None, session_id="s", workspace=str(tmp_path))
    result = task_driver._run_agent_node(
        {"id": "n1", "data": {"agent_id": "writer", "label": "Writer"}}, "n1", "write it",
        args=args, flow_state=None, agent_overrides={}, port=0,
    )
    assert result.ok and result.extras["stats"]["tools"] == 1
    assert result.extras["files"] == [{"op": "add", "path": "out/a.md"}]
    process = closed["process"]
    assert [c["tool"] for c in process["tool_calls"]] == ["write_file"]
    assert [a["path"] for a in process["artifacts"]] == ["out/a.md"]
    # Outside the node the sink is gone again.
    assert artifact_sink._artifact_recorder.get() is None

    # The log is a chat log: the chat parser reads its steps.
    text = log_path.read_text(encoding="utf-8")
    assert "=== Message at " in text and "--- Node run started at" not in text
    run = {"run_id": "n1", "log_file": str(log_path), "output": "done", "input": "write it"}
    monkeypatch.setattr(messages.run_manager, "get_run_by_id", lambda rid: run)
    monkeypatch.setattr(messages.run_manager, "get_run_process", lambda rid: process)
    trail = messages.build_run_trail("n1")
    assert [e.get("tool") for e in trail["timeline"] if e["type"] == "tool"] == ["write_file"]
    assert trail["files"][0]["path"] == "out/a.md"


def test_a_flow_node_run_shows_its_thoughts_tool_calls_and_input(tmp_path, monkeypatch):
    """A flow node run's own log (flow/task_driver.py) has no step numbers:
    its thoughts and tool calls are read in order, each tool line matched to
    the run's recorded call, and the run's input rides on the trail."""
    log = tmp_path / "node.log"
    log.write_text(
        "--- Node run started at now ---\nAgent: researcher\n\n=== EXECUTION ===\n"
        "[llm_start] model=M\n"
        f"[reasoning] content={json.dumps('look for sources first')}\n[llm_end]\n"
        "[tool_start] tool=web_search input={'query': 'coffee'}\n[tool_end] output=found\n"
        "[tool_start] tool=read_file input={'path': 'a.md'}\n[tool_end] output=text\n",
        encoding="utf-8")
    run = {"run_id": "n2", "log_file": str(log), "output": "the notes", "input": "find coffee trends"}
    monkeypatch.setattr(messages.run_manager, "get_run_by_id", lambda rid: run if rid == "n2" else None)
    monkeypatch.setattr(messages.run_manager, "get_run_process", lambda rid: {"tool_calls": [
        {"tool": "web_search", "input": "{'query': 'coffee'}", "output": "found it all", "status": "ok", "step": 1},
    ]})
    trail = messages.build_run_trail("n2")
    kinds = [(e["type"], e.get("tool")) for e in trail["timeline"]]
    assert kinds == [("reasoning", None), ("tool", "web_search"), ("tool", "read_file"), ("text", None)]
    assert trail["timeline"][0]["content"] == "look for sources first"
    # The recorded call carries the whole output; a line past them keeps the log's.
    assert trail["timeline"][1]["output"] == "found it all"
    assert trail["timeline"][2]["input"] == "{'path': 'a.md'}"
    assert trail["input"] == "find coffee trends"
    assert trail["stats"]["tools"] == 2 and trail["stats"]["thoughts"] == 1
