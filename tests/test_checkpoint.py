"""Checkpoint and resume of the agent loop (agents/checkpoint.py)."""
from __future__ import annotations

import pytest

from agents import checkpoint as ck
from managers import run_manager as rm
from managers import run_watchdog
from tasks import service as ts


class _Sink:
    def __init__(self):
        self.saved = []

    def __call__(self, run_id, snapshot):
        self.saved.append((run_id, snapshot))


def test_callback_writes_after_every_tool_boundary():
    sink = _Sink()
    cb = ck.RunCheckpointCallback("run-1", sink, instruction="do the thing")
    cb.on_tool_start({"name": "read_file"}, '{"path": "a.py"}', run_id="call-1")
    assert sink.saved[-1][1]["pending"]["tool"] == "read_file"
    assert sink.saved[-1][1]["pending"]["args"] == {"path": "a.py"}
    cb.on_tool_end("print('hi')")
    snap = sink.saved[-1][1]
    assert snap["pending"] is None
    assert snap["step"] == 1
    assert snap["steps"][0]["output"] == "print('hi')"
    assert snap["instruction"] == "do the thing"
    cb.on_tool_start({"name": "run_shell"}, "ls", inputs={"command": "ls"})
    cb.on_tool_error(RuntimeError("nope"))
    assert sink.saved[-1][1]["steps"][1]["output"].startswith("ERROR")
    assert cb.writes == 4


def test_long_outputs_are_clipped():
    sink = _Sink()
    cb = ck.RunCheckpointCallback("run-1", sink)
    cb.on_tool_start({"name": "read_file"}, "x")
    cb.on_tool_end("y" * (ck.MAX_OUTPUT_CHARS + 500))
    out = sink.saved[-1][1]["steps"][0]["output"]
    assert len(out) < ck.MAX_OUTPUT_CHARS + 100
    assert "truncated" in out


def test_a_failing_save_never_breaks_the_run():
    def _boom(run_id, snap):
        raise RuntimeError("db gone")
    cb = ck.RunCheckpointCallback("run-1", _boom)
    cb.on_tool_start({"name": "read_file"}, "x")
    cb.on_tool_end("ok")
    assert cb.steps and cb.writes == 0


def test_resume_replays_completed_steps_as_tool_messages():
    snap = {
        "instruction": "fix the bug",
        "step": 2,
        "steps": [
            {"step": 1, "tool": "read_file", "args": {"path": "a.py"}, "call_id": "c1", "output": "code"},
            {"step": 2, "tool": "grep", "args": {"q": "x"}, "call_id": "c2", "output": "hits"},
        ],
        "pending": None,
    }
    msgs = ck.history_messages(snap)
    assert msgs[0].content == "fix the bug"
    assert msgs[1].tool_calls[0]["name"] == "read_file" and msgs[1].tool_calls[0]["id"] == "c1"
    assert msgs[2].tool_call_id == "c1" and msgs[2].content == "code"
    assert len(msgs) == 5
    assert "2 tool call(s)" in ck.resume_instruction(snap)


def test_an_interrupted_idempotent_call_is_dropped_and_a_side_effect_is_flagged():
    base = {"instruction": "x", "step": 1, "steps": []}
    dropped = ck.resume_steps({**base, "pending": {"step": 1, "tool": "read_file", "args": {}}})
    assert dropped == []
    flagged = ck.resume_steps({**base, "pending": {"step": 1, "tool": "run_shell", "args": {"command": "rm x"}}})
    assert flagged[0]["interrupted"] is True
    assert flagged[0]["output"] == ck.INTERRUPTED_NOTE
    msgs = ck.history_messages({**base, "pending": {"step": 1, "tool": "run_shell", "args": {}, "call_id": "c9"}})
    assert msgs[-1].content == ck.INTERRUPTED_NOTE
    assert "interrupted" in ck.resume_instruction(
        {**base, "pending": {"step": 1, "tool": "run_shell", "args": {}}})
    assert ck.is_idempotent("mcp__github__create_issue") is False
    assert ck.is_idempotent("read_file") is True


def test_checkpoint_persists_beside_the_payload_and_stamps_the_record():
    t = ts.create_task("cp")
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "swe_agent", task_id=str(t.id), link_to_session=False)
    ck.save_checkpoint(rid, {"step": 3, "steps": [{"tool": "x"}]})
    assert ck.load_checkpoint(rid)["step"] == 3
    rec = rm.get_run_by_id(rid)
    assert rec["checkpoint_step"] == 3 and rec["checkpoint_at"]
    # The heavy payload written at close keeps the checkpoint.
    rm.update_run(rid, {"process": {"tool_calls": [{"step": 1}], "response": {"text": "hi"}}})
    assert ck.load_checkpoint(rid)["step"] == 3
    ck.clear_checkpoint(rid)
    assert ck.load_checkpoint(rid) is None


# ── the watchdog resumes from it ─────────────────────────────────────────────

@pytest.fixture
def dead_run_with_checkpoint(monkeypatch):
    import agents.registry as registry
    monkeypatch.setattr(registry, "get_agent", lambda agent_id: object())
    t = ts.create_task("resume me")
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "swe_agent", task_id=str(t.id), status="running", link_to_session=False)
    rm.update_run(rid, {"pid": 999999999})
    ts.assign_agent(t.id, "swe_agent", {"description": "go"}, run_id=rid)
    ck.save_checkpoint(rid, {"instruction": "go", "step": 1,
                             "steps": [{"step": 1, "tool": "read_file", "args": {}, "output": "x"}]})
    monkeypatch.setattr(rm, "_pid_exists", lambda pid: False)
    return t, rid


def test_watchdog_resumes_a_dead_run_from_its_checkpoint(dead_run_with_checkpoint, monkeypatch):
    import agents.agent_launcher as launcher
    t, rid = dead_run_with_checkpoint
    calls = []

    def _start(task_id, agent_id, params=None, run_id=None):
        calls.append((task_id, agent_id, params, run_id))
        rm.update_run(run_id, {"status": "running", "pid": 1})
        return run_id, "s"
    monkeypatch.setattr(launcher, "start_run", _start)

    assert run_watchdog.sweep_once() >= 1
    assert calls and calls[0][3] == rid
    assert calls[0][2]["resume_checkpoint"] == rid
    rec = rm.get_run_by_id(rid)
    assert rec["status"] == "running" and rec["resume_attempts"] == 1


def test_watchdog_stops_resuming_after_the_cap(dead_run_with_checkpoint, monkeypatch):
    import agents.agent_launcher as launcher
    t, rid = dead_run_with_checkpoint
    rm.update_run(rid, {"resume_attempts": run_watchdog.MAX_AUTO_RESUMES})
    monkeypatch.setattr(launcher, "start_run", lambda *a, **k: pytest.fail("must not resume"))
    run_watchdog.sweep_once()
    assert rm.get_run_by_id(rid)["status"] == "failed"


def test_the_launcher_passes_the_resume_flag(monkeypatch):
    import agents.agent_launcher as launcher
    import agents.registry as registry
    monkeypatch.setattr(registry, "get_agent", lambda agent_id: object())
    monkeypatch.setenv("AGENTS_HUB_ROLE", "api")
    t = ts.create_task("resume flag")
    run_id, _ = launcher.start_run(str(t.id), "swe_agent", {"resume_checkpoint": "old-run"})
    from common import run_queue
    args = run_queue.get(run_id)["payload"]["cli_args"]
    assert args[args.index("--resume-checkpoint") + 1] == "old-run"
