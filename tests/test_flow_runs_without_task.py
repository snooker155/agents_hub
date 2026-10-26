"""
A flow run is a run, not a task: started by the API, an agent, a schedule, a
trigger or another flow, it makes no task. A task is used only when one is
given (a person assigned the flow to it).
"""
from __future__ import annotations

import pytest

from flow import launcher, run_store

_FLOW = {"id": "f1", "name": "Digest", "description": "d", "workspace": None,
         "nodes": [{"id": "n1", "data": {"agent_id": "writer"}}], "edges": []}


@pytest.fixture()
def no_tasks(monkeypatch):
    """Fail any task creation; capture the launch spec instead of spawning."""
    from tasks import service as ts
    monkeypatch.setattr(ts, "create_task", lambda **kw: pytest.fail("a flow run must not create a task"))
    monkeypatch.setattr("flow.store.get_flow", lambda fid: _FLOW if fid == "f1" else None)
    specs: list = []
    monkeypatch.setattr(launcher, "_dispatch", lambda spec: specs.append(spec))
    return specs


def test_a_run_without_a_task_launches_with_no_task(no_tasks):
    run_id, session_id = launcher.start_flow_run(None, "f1", {"workspace": "ws", "description": "go"})
    rec = run_store.get_flow_run(run_id)
    assert rec["task_id"] is None and rec["workspace"] == "ws"
    assert rec["title"] == "Flow: Digest" and session_id
    spec = no_tasks[0]
    assert "--task-id" not in spec["cli_args"] and spec["task_id"] is None


def test_an_agent_started_flow_makes_no_task(no_tasks, monkeypatch):
    from tools import langchain_tools as lt
    monkeypatch.setattr(lt, "svc_assign_agent", lambda *a, **k: pytest.fail("no task to assign"))
    payload = lt.launch_flow_run("f1", _FLOW, "ws", "check the article")
    assert "task_id" not in payload
    assert run_store.get_flow_run(payload["run_id"])["task_id"] is None


def test_a_flow_node_runs_its_flow_with_no_task(no_tasks, monkeypatch):
    from flow.entities.containers import run_flow, _nested
    captured = {}

    def _start(task_id, flow_id, params, parent_run_id=None):
        captured.update(task_id=task_id, flow_id=flow_id, params=params)
        return "child", "sess"

    monkeypatch.setattr(launcher, "start_flow_run", _start)
    # Launch the child the way the nested runner does, and stop there.
    monkeypatch.setattr(_nested, "run_nested", lambda child, ctx, cfg: {"child": child.launch()})
    ctx = type("Ctx", (), {"flow_id": "parent", "workspace": "ws", "run_id": "p1", "node_id": "n"})()
    out = run_flow.run({}, {"flow_id": "f1", "goal": "sum it up"}, ctx)
    assert out == {"child": "child"}
    assert captured["task_id"] is None and captured["flow_id"] == "f1"


def test_a_rerun_of_a_run_without_a_task_stays_without_one(no_tasks, monkeypatch):
    run_store.open_flow_run("old", "f1", workspace="ws", title="Flow: Digest", status="completed")
    run_store.update_flow_run("old", {"checkpoint": {"done": [{"node_id": "n1", "output": "x", "ok": True}]}})
    monkeypatch.setattr("flow.rerun.rerun_checkpoint", lambda flow, source, node_id, mode: {"done": []})
    out = launcher.rerun_flow_run("f1", "old", "n1", "single")
    assert out["task_id"] is None
    assert run_store.get_flow_run(out["run_id"])["task_id"] is None
