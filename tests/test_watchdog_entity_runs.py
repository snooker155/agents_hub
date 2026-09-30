"""
The watchdog's one sweep over every entity kind (managers.run_watchdog._sweep_entity_runs).

tests/test_flow_resume.py and tests/test_loop_resume.py already pin the
flow and loop slices through the thin _sweep_flow_runs/_sweep_loop_runs
wrappers this module keeps for them. These tests exercise the generalised
sweep directly, against team and scenario runs, the two kinds whose own
launcher (teams.launcher, playground.launcher) is being written alongside
this change, so it is stubbed here rather than real (see _stub_module).
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from common import entity_runs
from managers import run_watchdog


def _stub_module(monkeypatch, name: str, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


def _stale_iso(hours: float = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _write_run(kind: str, run_id: str, *, entity_id: str = "def-1", task_id="t1", **extra):
    """A raw entity_runs record of one kind, written directly (these tests
    are about the watchdog's sweep, not about how a launcher would have
    prepared the record)."""
    id_field, entity_field = entity_runs.KIND_FIELDS[kind]
    rec = {
        "run_id": run_id, id_field: run_id, "kind": kind,
        entity_field: entity_id, "entity_id": entity_id,
        "task_id": task_id, "status": "running", "pid": 999999,
        "host": "", "heartbeat_at": _stale_iso(), "workspace": "ws",
        **extra,
    }
    entity_runs.upsert(rec, kind=kind, merge=False)
    return rec


# ── team ──────────────────────────────────────────────────────────────────

def test_a_dead_team_run_with_a_checkpoint_is_resumed(monkeypatch):
    _write_run("team", "team-run-1", checkpoint={"rounds_done": 1})
    calls = []
    # A real resumer relaunches the process, whose launch moves the record
    # back to running (runtime.entity_launch.launch_prepared); the stub does
    # that part by hand. Before the relaunch the watchdog closes the dead
    # run as failed, the only legal way from running back to running.
    def _resume(run_id, **kw):
        calls.append((run_id, kw))
        assert entity_runs.get(run_id)["status"] == "failed"
        entity_runs.mark_running(run_id, pid=1, host="h")
    _stub_module(monkeypatch, "teams.launcher", resume_team_run=_resume)

    assert run_watchdog._sweep_entity_runs(kinds=("team",)) == 1
    assert calls == [("team-run-1", {"auto": True})]
    # A resume that succeeded must not also fail the run underneath it.
    assert entity_runs.get("team-run-1")["status"] == "running"


def test_a_dead_team_run_without_a_checkpoint_is_failed_and_its_task_finalized(monkeypatch):
    _write_run("team", "team-run-2")
    finalized = []
    monkeypatch.setattr(
        "managers.run_manager.finalize_flow_task",
        lambda task_id, status, code, **kw: finalized.append((task_id, status, code)),
    )

    assert run_watchdog._sweep_entity_runs(kinds=("team",)) == 1
    rec = entity_runs.get("team-run-2")
    assert rec["status"] == "failed"
    assert "no checkpoint" in rec["error"]
    assert finalized == [("t1", "failed", 1)]


# ── scenario ─────────────────────────────────────────────────────────────

def test_a_dead_scenario_run_with_a_checkpoint_is_resumed(monkeypatch):
    _write_run("scenario", "sim-run-1", checkpoint={"ticks_done": 3})
    calls = []
    _stub_module(monkeypatch, "playground.launcher",
                 resume_scenario_run=lambda run_id, **kw: calls.append((run_id, kw)))

    assert run_watchdog._sweep_entity_runs(kinds=("scenario",)) == 1
    assert calls == [("sim-run-1", {"auto": True})]


def test_a_dead_scenario_run_without_a_checkpoint_is_failed(monkeypatch):
    _write_run("scenario", "sim-run-2")
    monkeypatch.setattr("managers.run_manager.finalize_flow_task", lambda *a, **k: None)

    assert run_watchdog._sweep_entity_runs(kinds=("scenario",)) == 1
    rec = entity_runs.get("sim-run-2")
    assert rec["status"] == "failed"
    assert "no checkpoint" in rec["error"]


# ── the resume budget ────────────────────────────────────────────────────

def test_attempts_beyond_the_cap_are_failed_not_resumed(monkeypatch):
    _write_run("team", "team-run-3", checkpoint={"rounds_done": 2},
              resume_attempts=run_watchdog.MAX_AUTO_RESUMES)
    monkeypatch.setattr("managers.run_manager.finalize_flow_task", lambda *a, **k: None)
    _stub_module(monkeypatch, "teams.launcher",
                 resume_team_run=lambda *a, **k: pytest.fail("should not resume past the cap"))

    assert run_watchdog._sweep_entity_runs(kinds=("team",)) == 1
    rec = entity_runs.get("team-run-3")
    assert rec["status"] == "failed"
    assert "2 attempt" in rec["error"]


# ── another host, no heartbeat ───────────────────────────────────────────

def test_a_run_on_another_host_with_no_heartbeat_is_left_alone(monkeypatch):
    _write_run("team", "team-run-4", heartbeat_at=None, host="some-other-host", pid=123456)
    _stub_module(monkeypatch, "teams.launcher",
                 resume_team_run=lambda *a, **k: pytest.fail("must not touch another host's run"))

    assert run_watchdog._sweep_entity_runs(kinds=("team",)) == 0
    assert entity_runs.get("team-run-4")["status"] == "running"


def test_a_run_on_this_host_with_no_heartbeat_and_a_dead_pid_is_failed(monkeypatch):
    """The no-heartbeat fallback (runtime.entity_launch.child_alive) does act
    when the record is this host's own: only a *different* host is left
    alone."""
    import socket
    _write_run("team", "team-run-5", heartbeat_at=None, host=socket.gethostname(), pid=999999)
    monkeypatch.setattr("managers.run_manager.finalize_flow_task", lambda *a, **k: None)

    assert run_watchdog._sweep_entity_runs(kinds=("team",)) == 1
    assert entity_runs.get("team-run-5")["status"] == "failed"


# ── pending runs ─────────────────────────────────────────────────────────

def test_a_pending_run_whose_queue_row_failed_is_failed():
    from common import run_queue

    _write_run("scenario", "sim-run-3", status="pending", heartbeat_at=None)
    run_queue.enqueue("sim-run-3", "scenario", {"kind": "scenario", "run_id": "sim-run-3"})
    for _ in range(run_queue.MAX_ATTEMPTS):
        run_queue.claim("w1")
        run_queue.requeue("sim-run-3", "boom")
    assert run_queue.get("sim-run-3")["status"] == run_queue.STATUS_FAILED

    assert run_watchdog._sweep_entity_runs(kinds=("scenario",)) == 1
    rec = entity_runs.get("sim-run-3")
    assert rec["status"] == "failed"
    assert "boom" in rec["error"]


def test_a_pending_run_with_a_live_queue_row_is_left_alone():
    from common import run_queue

    _write_run("scenario", "sim-run-4", status="pending", heartbeat_at=None)
    run_queue.enqueue("sim-run-4", "scenario", {"kind": "scenario", "run_id": "sim-run-4"})

    assert run_watchdog._sweep_entity_runs(kinds=("scenario",)) == 0
    assert entity_runs.get("sim-run-4")["status"] == "pending"
