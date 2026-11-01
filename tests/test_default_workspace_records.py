"""Records made for work in the default workspace name it.

A record with no workspace is an anomaly (an old row or a bug): it belongs to
``default`` and only an administrator sees it. So the code that creates a
record for work happening in ``default`` stamps ``default`` itself, and the
code that knows a workspace passes it on. These tests pin the creators that
used to leave it empty: flow node instances and runs, a task's approval
placeholder run, the instance carrier's task sweeps, tasks and subtasks, and
views. Rows already stored without a workspace are left as they are.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from instances import registry, store as instance_store
from managers import run_manager
from tasks import service as tasks_service
from tasks import TaskStatus


def _run_for_task(task_id) -> dict:
    runs = [r for r in run_manager.load_runs() if str(r.get("task_id")) == str(task_id)]
    assert runs, "the task has no run record"
    return runs[-1]


# ── instances ────────────────────────────────────────────────────────────────

def test_a_new_instance_without_a_workspace_is_in_default():
    inst = registry.ensure_instance("writer", kind="task", state="starting")
    assert inst["workspace"] == "default"
    assert instance_store.get(inst["instance_id"])["workspace"] == "default"


def test_a_new_instance_keeps_the_workspace_it_is_given():
    inst = registry.ensure_instance("writer", kind="task", workspace="acme", state="starting")
    assert instance_store.get(inst["instance_id"])["workspace"] == "acme"


def test_an_instance_stored_without_a_workspace_is_not_backfilled():
    legacy = instance_store.create("writer", kind="task", workspace=None)
    registry.ensure_instance("writer", instance_id=legacy["instance_id"], state="active")
    assert not instance_store.get(legacy["instance_id"])["workspace"]


# ── flow nodes ───────────────────────────────────────────────────────────────

def _run_node(monkeypatch, tmp_path, **kw):
    from flow import task_driver

    monkeypatch.setattr(task_driver, "run_agent_lifecycle",
                        lambda *a, **k: SimpleNamespace(ok=True))
    monkeypatch.setattr(task_driver, "run_log_path", lambda rid: tmp_path / f"{rid}.log")
    args = SimpleNamespace(flow_id="flow-ws", run_id=f"fr-{uuid4().hex[:8]}", task_id=None,
                           session_id=None, workspace=str(tmp_path))
    outcome = SimpleNamespace(run_id=None, stop_event=SimpleNamespace(is_set=lambda: False))
    task_driver._run_agent_node(
        {"node_id": "n1", "agent_id": "writer", "label": "Writer"}, "n1", "write it",
        args=args, flow_state=None, agent_overrides={}, port=0, outcome=outcome, **kw)
    return args, outcome


@pytest.mark.parametrize("given,expected", [(None, "default"), ("acme", "acme")])
def test_a_flow_node_instance_and_run_carry_the_run_workspace(monkeypatch, tmp_path, given, expected):
    args, outcome = _run_node(monkeypatch, tmp_path, workspace=given)
    run = run_manager.get_run_by_id(outcome.run_id)
    assert run["workspace"] == expected
    inst = instance_store.get(run["instance_id"])
    assert inst["kind"] == "flow_node" and inst["workspace"] == expected


def test_the_run_workspace_is_the_folder_workspace_else_the_flows_own(tmp_path):
    from flow.task_driver import run_workspace_name
    from workspace import create_workspace_folder

    folder = create_workspace_folder("flowws")
    assert run_workspace_name(str(folder), {"workspace": "other"}) == "flowws"
    assert run_workspace_name(str(tmp_path / "nowhere"), {"workspace": "other"}) == "other"
    assert run_workspace_name(str(tmp_path / "nowhere"), {}) is None


def test_the_driver_hands_its_nodes_the_run_workspace(monkeypatch):
    from flow import task_driver
    from workspace import create_workspace_folder

    folder = create_workspace_folder("driverws")
    seen: dict = {}

    def fake_node(node, node_id, prompt, **kw):
        seen["workspace"] = kw["workspace"]
        return task_driver.DispatchResult(ok=True, output="")

    monkeypatch.setattr(task_driver, "_run_agent_node", fake_node)
    args = SimpleNamespace(flow_id="f", run_id="fr", task_id=None, session_id=None,
                           workspace=str(folder), resume_from="")
    driver = task_driver.build_task_driver(
        args=args, flow={"name": "F", "workspace": "other"}, flow_id="f", run_id="fr",
        task_id="", session_id="", task_title="", node_run_ids={}, port=0)
    import asyncio

    async def _go():
        out = SimpleNamespace(run_id=None, ok=None, output=None, text=None, written=None,
                              goto=None, error=None, duration_ms=None)
        async for _ in driver.run_agent_node({"node_id": "n"}, "n", "L", "p", out, None):
            pass

    asyncio.run(_go())
    assert seen["workspace"] == "driverws"


# ── a task's runs ────────────────────────────────────────────────────────────

def test_a_run_waiting_for_approval_carries_its_tasks_workspace():
    from agents.agent_launcher import preregister_run

    task = tasks_service.create_task("needs a yes", workspace="acme")
    run_id = preregister_run(str(task.id), "writer")
    assert run_manager.get_run_by_id(run_id)["workspace"] == "acme"
    # The caller's own workspace wins.
    other = preregister_run(str(task.id), "writer", workspace="elsewhere")
    assert run_manager.get_run_by_id(other)["workspace"] == "elsewhere"


def test_a_run_waiting_for_approval_in_default_names_default():
    from agents.agent_launcher import preregister_run

    task = tasks_service.create_task("in default")
    run_id = preregister_run(str(task.id), "writer", session_id=None)
    run = run_manager.get_run_by_id(run_id)
    assert run["workspace"] == "default" and run["status"] == "awaiting_approval"


def test_the_delegation_placeholder_run_names_the_subtasks_workspace(monkeypatch):
    from agents import agent_launcher
    from tasks.delegate import launch_delegation

    from agents.registry import AgentSpec

    spec = AgentSpec(id="worker", name="Worker", type="langchain", entrypoint="x")
    monkeypatch.setattr("agents.registry.get_agent", lambda aid: spec if aid == "worker" else None)
    parent = tasks_service.create_task("whole job", workspace="acme")
    monkeypatch.setattr(agent_launcher, "start_run",
                        lambda task_id, agent_id, params=None, run_id=None: (run_id, None))
    res = launch_delegation(str(parent.id), {"agent_id": "worker", "input": "write the intro",
                                             "env": {}})
    assert res["ok"], res
    assert _run_for_task(res["task"]["id"])["workspace"] == "acme"


def _sweep_runs(monkeypatch, scope, task_workspace):
    from runtime import instance_run

    task = tasks_service.create_task("route me", status=TaskStatus.ready, workspace=task_workspace)
    monkeypatch.setattr("agents.agent_factory.create_agent",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no model in tests")))
    instance_run.sweep_orchestrator_tasks("inst_sweep", None, scope)
    return _run_for_task(task.id)


def test_an_orchestrator_sweep_run_carries_the_tasks_workspace(monkeypatch):
    run = _sweep_runs(monkeypatch, "acme", "acme")
    assert run["agent_id"] == "orchestrator" and run["workspace"] == "acme"


def test_an_orchestrator_sweep_run_of_a_default_task_names_default(monkeypatch):
    assert _sweep_runs(monkeypatch, "default", "default")["workspace"] == "default"


# ── tasks ────────────────────────────────────────────────────────────────────

def test_a_task_created_without_a_workspace_is_in_default():
    assert tasks_service.create_task("no workspace").workspace == "default"
    assert tasks_service.create_task("empty workspace", workspace="").workspace == "default"
    assert tasks_service.create_task("named", workspace="acme").workspace == "acme"


def test_a_subtask_takes_its_parents_workspace_else_default():
    parent = tasks_service.create_task("parent", workspace="acme")
    assert tasks_service.add_subtask(parent.id, "child").workspace == "acme"
    assert tasks_service.add_subtask(uuid4(), "orphan").workspace == "default"


# ── views ────────────────────────────────────────────────────────────────────

def test_a_view_created_without_a_workspace_is_in_default():
    from views.store import create_view, list_views

    env = create_view("markdown", "A", {"markdown": "a"}, summary="a")
    row = [r for r in list_views() if r["view_id"] == env.view_id][0]
    assert row["workspace"] == "default"
    named = create_view("markdown", "B", {"markdown": "b"}, summary="b", workspace="acme")
    assert [r for r in list_views() if r["view_id"] == named.view_id][0]["workspace"] == "acme"
