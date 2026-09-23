"""The worker role (runtime/worker.py) and the ``api`` role of the launcher.

No process is ever spawned: the launch step is stubbed, and the run records
it would have written are written by hand, so the tests pin the contract
between backend, queue and worker rather than what a child does.
"""
from __future__ import annotations

import pytest

from common import run_queue
from managers import run_manager as rm
from runtime import worker as worker_mod
from tasks import service as ts


@pytest.fixture(autouse=True)
def _fake_agent_registry(monkeypatch):
    import agents.registry as registry
    monkeypatch.setattr(registry, "get_agent", lambda agent_id: object())


@pytest.fixture
def launches(monkeypatch):
    """Record what the worker would spawn instead of spawning it."""
    calls = []

    def _launch(spec):
        calls.append(spec)
        # What launch_prepared does once the child exists.
        rm.update_run(spec["run_id"], {"status": "running", "pid": 4242})

    monkeypatch.setattr(worker_mod, "_launch", _launch)
    return calls


def _queued_task_run(run_id: str = "r1"):
    t = ts.create_task("queued work")
    rm.preopen_run(run_id, "swe_agent", task_id=str(t.id), status="queued", link_to_session=False)
    run_queue.enqueue(run_id, "task", {"kind": "task", "run_id": run_id, "agent_id": "swe_agent",
                                      "task_id": str(t.id)})
    return t


# ── the api role hands launches to the queue ─────────────────────────────────

def test_api_role_enqueues_instead_of_spawning(monkeypatch):
    import agents.agent_launcher as launcher

    monkeypatch.setenv("AGENTS_HUB_ROLE", "api")

    def _boom(*a, **k):
        raise AssertionError("the api role must not spawn")
    monkeypatch.setattr(launcher.subprocess, "Popen", _boom)

    t = ts.create_task("api-role run")
    run_id, session_id = launcher.start_run(str(t.id), "swe_agent", {"description": "hi"})

    rec = rm.get_run_by_id(run_id)
    assert rec["status"] == "queued"
    assert session_id
    row = run_queue.get(run_id)
    assert row["kind"] == "task" and row["status"] == run_queue.STATUS_QUEUED
    spec = row["payload"]
    assert spec["agent_id"] == "swe_agent" and spec["task_id"] == str(t.id)
    assert "--run-id" in spec["cli_args"] and "--desc" in spec["cli_args"]
    # Nothing secret rides on the queue: the worker builds the env itself.
    assert "env" not in spec and "OPENAI_API_KEY" not in str(spec)


def test_default_role_spawns_right_here(monkeypatch):
    import agents.agent_launcher as launcher

    monkeypatch.setenv("AGENTS_HUB_ROLE", "all")
    spawned = []

    class _Proc:
        pid = 777

    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: spawned.append(a) or _Proc())
    t = ts.create_task("inline run")
    run_id, _ = launcher.start_run(str(t.id), "swe_agent", None)
    assert spawned
    rec = rm.get_run_by_id(run_id)
    assert rec["status"] == "running" and rec["pid"] == 777
    assert rec["host"]
    assert run_queue.get(run_id) is None


def test_launch_prepared_spawns_from_a_spec(monkeypatch):
    """A worker on another host gets only the spec and rebuilds the rest."""
    import agents.agent_launcher as launcher

    monkeypatch.setenv("AGENTS_HUB_ROLE", "api")
    t = ts.create_task("worker-side launch")
    run_id, _ = launcher.start_run(str(t.id), "swe_agent", None)
    spec = run_queue.get(run_id)["payload"]

    calls = []

    class _Proc:
        pid = 9001

    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: calls.append((a, k)) or _Proc())
    launcher.launch_prepared(spec)
    args, kwargs = calls[0]
    assert args[0][-1] == run_id or run_id in args[0]
    assert "AGENT_WORKSPACE" in kwargs["env"]
    assert kwargs["env"]["AGENT_SESSION_ID"] == spec["session_id"]
    rec = rm.get_run_by_id(run_id)
    assert rec["status"] == "running" and rec["pid"] == 9001


# ── the worker loop ──────────────────────────────────────────────────────────

def test_worker_claims_launches_and_tracks_the_child(launches):
    _queued_task_run("r1")
    w = worker_mod.Worker(concurrency=2, modes=["local"], owner="w1")
    assert w.tick() == 1
    assert launches[0]["run_id"] == "r1"
    assert "r1" in w.tracked
    assert run_queue.get("r1")["status"] == run_queue.STATUS_RUNNING
    assert run_queue.get("r1")["lease_owner"] == "w1"


def test_worker_respects_concurrency(launches):
    for i in range(3):
        _queued_task_run(f"r{i}")
    w = worker_mod.Worker(concurrency=2, modes=["local"], owner="w1")
    assert w.tick() == 2
    assert run_queue.stats()["queued"] == 1


def test_worker_reaps_finished_children(launches, monkeypatch):
    _queued_task_run("r1")
    w = worker_mod.Worker(concurrency=2, modes=["local"], owner="w1")
    w.tick()
    monkeypatch.setattr(worker_mod, "_child_alive", lambda spec: True)
    w.tick()
    assert "r1" in w.tracked
    monkeypatch.setattr(worker_mod, "_child_alive", lambda spec: False)
    w.tick()
    assert "r1" not in w.tracked
    assert run_queue.get("r1")["status"] == run_queue.STATUS_DONE


def test_a_failing_launch_goes_back_to_the_queue(monkeypatch):
    _queued_task_run("r1")

    def _boom(spec):
        raise RuntimeError("no interpreter here")
    monkeypatch.setattr(worker_mod, "_launch", _boom)

    w = worker_mod.Worker(concurrency=2, modes=["local"], owner="w1")
    assert w.tick() == 0
    assert w.failed == 1
    row = run_queue.get("r1")
    assert row["status"] == run_queue.STATUS_QUEUED
    assert "no interpreter here" in row["last_error"]


def test_a_stopping_worker_claims_nothing_but_keeps_renewing(launches, monkeypatch):
    _queued_task_run("r1")
    _queued_task_run("r2")
    w = worker_mod.Worker(concurrency=1, modes=["local"], owner="w1")
    w.tick()
    monkeypatch.setattr(worker_mod, "_child_alive", lambda spec: True)
    w.request_stop()
    before = run_queue.get("r1")["lease_until"]
    assert w.tick() == 0
    assert run_queue.get("r1")["lease_until"] >= before
    assert run_queue.get("r2")["status"] == run_queue.STATUS_QUEUED


def test_worker_only_claims_modes_it_can_run(launches):
    t = ts.create_task("docker work")
    rm.preopen_run("d1", "swe_agent", task_id=str(t.id), status="queued", link_to_session=False)
    run_queue.enqueue("d1", "task", {"kind": "task", "run_id": "d1"}, execution_mode="docker")
    w = worker_mod.Worker(concurrency=2, modes=["local"], owner="w1")
    assert w.tick() == 0
    assert run_queue.get("d1")["status"] == run_queue.STATUS_QUEUED


def test_watchdog_fails_a_queued_run_whose_launch_nobody_completed():
    from managers import run_watchdog

    _queued_task_run("r1")
    for _ in range(run_queue.MAX_ATTEMPTS):
        run_queue.claim("w1")
        run_queue.requeue("r1", "boom")
    assert run_queue.get("r1")["status"] == run_queue.STATUS_FAILED
    closed = run_watchdog.sweep_once()
    assert closed >= 1
    rec = rm.get_run_by_id("r1")
    assert rec["status"] == "failed" and "boom" in rec["error"]


def test_flow_launch_goes_through_the_queue_in_the_api_role(monkeypatch):
    from flow import launcher as flow_launcher

    monkeypatch.setenv("AGENTS_HUB_ROLE", "api")
    spawned = []
    monkeypatch.setattr(flow_launcher, "_spawn_flow_process", lambda *a, **k: spawned.append(a) or 1)
    flow_launcher._dispatch({"kind": "flow", "run_id": "f1", "flow_id": "fl", "task_id": "",
                             "session_id": "s", "workspace": "default", "args": ["x"],
                             "log_file": "/tmp/x.log", "header": "h", "mode": "w"})
    assert not spawned
    assert run_queue.get("f1")["kind"] == "flow"


def test_main_once_runs_a_single_tick(monkeypatch, capsys):
    monkeypatch.setattr(worker_mod.Worker, "tick", lambda self: 0)
    monkeypatch.setattr("common.bootstrap.ensure_initial_state", lambda: {})
    assert worker_mod.main(["--once", "--modes", "local"]) == 0
    assert "worker tick" in capsys.readouterr().out
