"""
GET /api/loops/runs/{id}/log: the work of each pass, read from the flow run
every iteration opens, so the loop page shows the log above the judgement.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from loops import store
from loops.models import Iteration, Loop, LoopRun


@pytest.fixture()
def logs_dir(tmp_path, monkeypatch):
    from flow import run_store
    monkeypatch.setattr(run_store, "FLOW_LOGS_DIR", tmp_path / "flow_logs")
    monkeypatch.setattr(run_store, "get_flow_run", lambda _id: None)
    return run_store


def _client():
    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from dashboard.backend.routes.loops import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _run_with_two_passes(run_store):
    loop = store.save_loop(Loop(name="Polish", flow_id="f", exit_criterion="reads well"))
    run = LoopRun(loop_id=loop.loop_id, goal="write it")
    store.save_run(run)
    for n in (1, 2):
        store.save_iteration(Iteration(loop_run_id=run.loop_run_id, iteration=n, flow_run_id=f"fr{n}"))
        run_store.append_flow_log("f", f"fr{n}", {"type": "agent_start", "node_id": "n1", "run_group": f"fr{n}"})
    run_store.append_flow_log("f", "fr2", {"type": "agent_finish", "node_id": "n1", "run_group": "fr2"})
    return run


def test_every_pass_comes_with_its_own_flow_log(logs_dir):
    run = _run_with_two_passes(logs_dir)
    resp = _client().get(f"/api/loops/runs/{run.loop_run_id}/log")
    assert resp.status_code == 200, resp.text
    passes = resp.json()["iterations"]
    assert [p["iteration"] for p in passes] == [1, 2]
    assert [len(p["events"]) for p in passes] == [1, 2]
    assert passes[1]["flow_run_id"] == "fr2"


def test_one_pass_alone_and_an_unknown_run(logs_dir):
    run = _run_with_two_passes(logs_dir)
    resp = _client().get(f"/api/loops/runs/{run.loop_run_id}/log", params={"iteration": 2})
    passes = resp.json()["iterations"]
    assert [p["iteration"] for p in passes] == [2]
    assert _client().get("/api/loops/runs/nope/log").status_code == 404


def test_the_list_carries_each_loops_last_run(logs_dir):
    run = _run_with_two_passes(logs_dir)
    loops = _client().get("/api/loops").json()["loops"]
    row = next(l for l in loops if l["loop_id"] == run.loop_id)
    assert row["last_run"]["loop_run_id"] == run.loop_run_id


def test_a_pass_that_ended_without_a_last_line_reads_as_ended_in_the_flow_history(tmp_path, monkeypatch):
    """A loop pass written before the loop logged its end has no flow_finish:
    the flow page's History and live view must still see it as over, by its
    execution record, or picking it drops the pick for a "live" run."""
    import asyncio
    from flow import run_store
    monkeypatch.setattr(run_store, "FLOW_LOGS_DIR", tmp_path / "flow_logs")
    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from dashboard.backend.routes.flows import get_flow_runs

    run_store.open_flow_run("fr-done", "f", status="running")
    run_store.log_flow_event("f", "fr-done", {"type": "flow_start", "timestamp": "t1"}, kind="task")
    run_store.log_flow_event("f", "fr-done", {"type": "agent_finish", "node_id": "n1", "timestamp": "t2"}, kind="task")
    run_store.close_flow_run("fr-done", status="completed")
    run_store.open_flow_run("fr-live", "f", status="running")
    run_store.log_flow_event("f", "fr-live", {"type": "flow_start", "timestamp": "t3"}, kind="task")

    records = {r["run_group"]: r for r in asyncio.run(get_flow_runs("f"))}
    assert records["fr-done"]["status"] == "completed"
    assert records["fr-done"]["events"][-1]["type"] == "flow_finish"
    # A run still going keeps going.
    assert records["fr-live"]["status"] == "running"


def test_a_pass_writes_its_last_line_to_the_flows_log(tmp_path, monkeypatch):
    """Each pass ends its flow log as a flow run does, so the flow page's
    History reads it as finished."""
    import types
    from flow import run_store
    import loops.runner as runner
    monkeypatch.setattr(run_store, "FLOW_LOGS_DIR", tmp_path / "flow_logs")

    async def _engine(*a, **k):
        yield {"type": "flow_finish", "combined_output": "done"}

    monkeypatch.setattr("flow.engine.run_flow_engine", _engine)
    monkeypatch.setattr("flow.task_driver.build_task_driver",
                        lambda **k: types.SimpleNamespace(should_stop=lambda: False))
    runner._run_flow_once(
        flow={"name": "F", "nodes": [], "edges": []}, flow_id="f", flow_run_id="fr-1",
        loop_run_id="lrun-1", iteration=1, workspace_path=str(tmp_path), workspace_name="ws",
        task_id=None, session_id="s", title="pass 1", shared_context="", seed_state={},
        should_stop=lambda: False,
    )
    events = run_store.read_run_group_events("f", "fr-1")
    assert events[-1]["type"] == "flow_finish"
    assert run_store.get_flow_run("fr-1")["status"] == "completed"


def test_the_step_note_reaches_only_agents_working_from_another_node(tmp_path, monkeypatch):
    import types
    from flow import run_store
    import loops.runner as runner
    monkeypatch.setattr(run_store, "FLOW_LOGS_DIR", tmp_path / "flow_logs")
    prompts = {}

    async def _engine(flow, *, driver, **k):
        preds = {"writer": ["researcher"]}
        prompts["researcher"] = driver.build_agent_prompt("brief", {}, "researcher", preds, {}, None, "")
        prompts["writer"] = driver.build_agent_prompt("brief", {}, "writer", preds, {"researcher": "notes"}, None, "")
        yield {"type": "flow_finish"}

    monkeypatch.setattr("flow.engine.run_flow_engine", _engine)
    monkeypatch.setattr("flow.task_driver.build_task_driver", lambda **k: types.SimpleNamespace(
        should_stop=lambda: False, build_agent_prompt=lambda shared, *a: f"prompt({shared})"))
    runner._run_flow_once(
        flow={"name": "F"}, flow_id="f", flow_run_id="fr-2", loop_run_id="l", iteration=2,
        workspace_path=str(tmp_path), workspace_name="ws", task_id=None, session_id="s",
        title="pass 2", shared_context="brief", seed_state={}, should_stop=lambda: False,
        step_note="\nLOOP ATTEMPT 2",
    )
    assert prompts["researcher"] == "prompt(brief)"
    assert prompts["writer"] == "prompt(brief)\nLOOP ATTEMPT 2"
