"""
loops.launcher: the database half of a loop run, and the shared envelope it
hands off to (runtime/entity_launch.py).

A loop run used to be a daemon thread of the API process: gone with every
restart and impossible to hand to a worker. What is worth pinning here is
the seam: a pending record, a claimed task and a log path exist before any
process does, the spec names the loop entrypoint and carries ``--resume``
only on a resume, the ``api`` role queues rather than spawns, a resume
refuses when there is nothing sensible to resume, and the runner's own
in-process door still works and now beats a heartbeat.
"""
from __future__ import annotations

import pytest

from common import entity_runs
from loops import store
from loops.launcher import LoopResumeError, resume_loop_run, start_loop_run, stop_loop_run
from loops.models import Loop

_FLOW = {"id": "f", "name": "F", "nodes": [{"id": "n1", "data": {"agent_id": "a", "category": "agent"}}],
         "edges": []}


@pytest.fixture(autouse=True)
def flow_exists(monkeypatch):
    monkeypatch.setattr("flow.store.get_flow", lambda fid: _FLOW if fid == "f" else None)


@pytest.fixture(autouse=True)
def no_real_spawn(monkeypatch):
    """Every test here drives the database half only; nothing may spawn a
    real subprocess."""
    calls: list = []
    monkeypatch.setattr("loops.launcher.launch_prepared", lambda spec: calls.append(spec))
    return calls


def _loop(**kw):
    kw.setdefault("name", "Polish")
    kw.setdefault("flow_id", "f")
    kw.setdefault("exit_criterion", "reads well")
    return store.save_loop(Loop(**kw))


def test_start_loop_run_creates_a_pending_record_and_dispatches_a_loop_spec(no_real_spawn):
    loop = _loop()
    run = start_loop_run(loop.loop_id, "write it", seed={"topic": "x"})

    assert run.status == "pending"
    assert run.loop_id == loop.loop_id
    assert run.goal == "write it"
    assert run.task_id and run.session_id

    rec = store.RUNS.read(run.loop_run_id)
    assert rec["log_file"] and "loop_run_" in rec["log_file"]
    assert rec["seed"] == {"topic": "x"}

    # The task names the loop as its executor, with this run's id.
    from tasks import service as ts
    task = ts.get_task(run.task_id)
    assert task.executor.kind == "loop" and task.executor.id == loop.loop_id
    assert task.assigned_agent_run_id == run.loop_run_id

    assert len(no_real_spawn) == 1
    spec = no_real_spawn[0]
    assert spec["kind"] == "loop"
    assert spec["entity_id"] == loop.loop_id
    assert spec["entrypoint"] == "loop_run"
    assert spec["cli_args"] == ["--run-id", run.loop_run_id]
    assert spec["execution_mode"] == "local"
    assert spec["resume"] is False


def test_start_loop_run_refuses_an_unknown_loop_or_a_missing_flow(no_real_spawn):
    with pytest.raises(ValueError):
        start_loop_run("loop_missing", "x")
    loop = _loop(flow_id="gone")
    with pytest.raises(ValueError):
        start_loop_run(loop.loop_id, "x")
    assert no_real_spawn == []


def test_the_api_role_enqueues_the_launch_instead_of_spawning(monkeypatch):
    from common import run_queue

    monkeypatch.setattr("common.config.hub_role", lambda: "api")
    loop = _loop()
    run = start_loop_run(loop.loop_id, "write it")

    queued = run_queue.get(run.loop_run_id)
    assert queued is not None
    assert queued["kind"] == "loop"
    assert queued["payload"]["entrypoint"] == "loop_run"
    assert store.get_run(run.loop_run_id).status == "pending"


def test_the_worker_dispatches_a_loop_launch_to_the_loop_launcher(monkeypatch):
    from runtime.entity_launch import launcher_for
    import loops.launcher as launcher
    seen = []
    monkeypatch.setattr(launcher, "launch_prepared", lambda spec: seen.append(spec))
    launcher_for("loop")({"kind": "loop", "run_id": "x"})
    assert seen == [{"kind": "loop", "run_id": "x"}]


# ── Resume ───────────────────────────────────────────────────────────────────

def _finished(run_id: str, status: str, iterations_done: int) -> None:
    entity_runs.mark_running(run_id, pid=1, host="h")
    if iterations_done:
        store.save_position(run_id, {"iterations_done": iterations_done, "spend": 0.1,
                                     "history": [{"iteration": 1, "score": 40}]})
    entity_runs.close(run_id, status=status, exit_code=1)


def test_resume_relaunches_a_stopped_run_from_its_position(no_real_spawn):
    loop = _loop()
    run = start_loop_run(loop.loop_id, "write it")
    _finished(run.loop_run_id, "stopped", 2)

    resumed = resume_loop_run(run.loop_run_id)
    assert resumed.loop_run_id == run.loop_run_id
    assert len(no_real_spawn) == 2
    spec = no_real_spawn[-1]
    assert spec["resume"] is True
    assert spec["cli_args"] == ["--run-id", run.loop_run_id, "--resume"]
    assert spec["header"] == "Loop run resumed" and spec["mode"] == "a"


def test_resume_refuses_when_there_is_nothing_to_resume(no_real_spawn):
    loop = _loop()
    with pytest.raises(LoopResumeError):
        resume_loop_run("lrun_missing")
    live = start_loop_run(loop.loop_id, "write it")
    with pytest.raises(LoopResumeError):
        resume_loop_run(live.loop_run_id)            # still pending
    run = start_loop_run(loop.loop_id, "write it")
    _finished(run.loop_run_id, "failed", 0)
    with pytest.raises(LoopResumeError):
        resume_loop_run(run.loop_run_id)             # no iteration finished
    done = start_loop_run(loop.loop_id, "write it")
    entity_runs.mark_running(done.loop_run_id, pid=1, host="h")
    entity_runs.close(done.loop_run_id, status="completed", exit_code=0)
    with pytest.raises(LoopResumeError):
        resume_loop_run(done.loop_run_id)


def test_an_auto_resume_counts_an_attempt_a_manual_one_does_not(no_real_spawn):
    loop = _loop()
    run = start_loop_run(loop.loop_id, "write it")
    _finished(run.loop_run_id, "failed", 1)

    resume_loop_run(run.loop_run_id, auto=False)
    assert store.get_run(run.loop_run_id).resume_attempts == 0
    _finished(run.loop_run_id, "failed", 1)
    resume_loop_run(run.loop_run_id, auto=True)
    assert store.get_run(run.loop_run_id).resume_attempts == 1
    assert store.get_position(run.loop_run_id)["resume_attempts"] == 1


def test_the_watchdog_resumes_a_dead_loop_through_the_launcher(monkeypatch, no_real_spawn):
    from datetime import datetime, timedelta, timezone
    from managers import run_watchdog

    loop = _loop()
    run = start_loop_run(loop.loop_id, "write it")
    entity_runs.mark_running(run.loop_run_id, pid=999999, host="h")
    store.save_position(run.loop_run_id, {"iterations_done": 1})
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    entity_runs.touch_heartbeat(run.loop_run_id, stale)

    assert run_watchdog._sweep_loop_runs() == 1
    assert no_real_spawn[-1]["resume"] is True
    assert store.get_run(run.loop_run_id).resume_attempts == 1


# ── Stop ─────────────────────────────────────────────────────────────────────

def test_stop_loop_run_marks_the_durable_status(no_real_spawn):
    loop = _loop()
    run = start_loop_run(loop.loop_id, "write it")
    assert stop_loop_run(run.loop_run_id) is True
    assert store.get_run(run.loop_run_id).status == "stopping"
    assert store.stop_requested(run.loop_run_id)
    assert stop_loop_run(run.loop_run_id) is False


# ── The in-process door ──────────────────────────────────────────────────────

def test_run_loop_without_a_record_marks_itself_running_with_a_heartbeat(monkeypatch):
    import os
    from loops.models import Verdict
    from loops import runner

    loop = _loop(max_iterations=1, target_score=None, min_iterations=1)
    monkeypatch.setattr(runner, "_run_flow_once",
                        lambda **kw: ({"combined_output": "d", "node_outputs": {}, "state": {}}, {}))
    monkeypatch.setattr(runner, "evaluate",
                        lambda *a, **k: (Verdict(score=95, verdict="stop", reason="ok"), 0.0))
    monkeypatch.setattr(runner, "_publish", lambda *a, **k: None)
    seen = {}
    real_mark = entity_runs.mark_running

    def _mark(run_id, **fields):
        seen.update(fields)
        return real_mark(run_id, **fields)
    monkeypatch.setattr(entity_runs, "mark_running", _mark)

    run = runner.run_loop(loop.loop_id, goal="write it")
    assert run.status == "completed"
    assert seen["pid"] == os.getpid() and seen["host"]
    assert store.get_run(run.loop_run_id).heartbeat_at


# ── The HTTP surface ─────────────────────────────────────────────────────────

def _client():
    import sys
    from pathlib import Path
    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from dashboard.backend.routes.loops import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_the_run_route_returns_the_record(no_real_spawn):
    loop = _loop()
    resp = _client().post(f"/api/loops/{loop.loop_id}/run", json={"goal": "write it"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["loop_id"] == loop.loop_id and body["status"] == "pending"
    assert len(no_real_spawn) == 1


def test_the_resume_route_relaunches_and_400s_without_a_position(no_real_spawn):
    loop = _loop()
    run = start_loop_run(loop.loop_id, "write it")
    _finished(run.loop_run_id, "stopped", 0)
    resp = _client().post(f"/api/loops/runs/{run.loop_run_id}/resume")
    assert resp.status_code == 400
    _finished(run.loop_run_id, "stopped", 2)
    resp = _client().post(f"/api/loops/runs/{run.loop_run_id}/resume")
    assert resp.status_code == 200, resp.text
    assert resp.json()["loop_run_id"] == run.loop_run_id
