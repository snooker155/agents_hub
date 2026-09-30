"""
Task.executor — an agent, a flow, a team or a loop, as one field.

Covers: an old-shaped task doc (only assigned_agent_type) loading with an
agent executor; assign_executor writing both fields consistently;
assign_executor_to_task's dispatch for flow/team/loop with their launchers
stubbed (teams.launcher and loops.runner are being written by other agents in
parallel — see the module docstrings this suite's task was scoped against —
so their real launch machinery is never exercised here, only the seam);
the /assign route accepting both the legacy agent_id body and the new
executor body; the retry and review-cycle generalisation in
managers.runs.task_finalize.finalize_task; and agent_state reading a
non-agent executor's run from common.entity_runs.
"""
from types import SimpleNamespace
from uuid import UUID

import pytest

from tasks import service as ts
from tasks.models import AgentState, Executor, TaskStatus
from tasks.assign import assign_executor_to_task
from tasks.serialize import task_to_dict


# ── Old-shaped doc → agent executor ─────────────────────────────────────────

def test_old_shaped_doc_loads_with_agent_executor():
    """A task written by the previous build carried only assigned_agent_type.
    Reading it back today must synthesize an equivalent agent executor —
    tasks.storage._sync_executor, exercised through a real read."""
    import json
    from common import db

    t = ts.create_task("legacy task")
    with db.transaction() as conn:
        row = conn.execute("SELECT doc FROM tasks WHERE id = ?", (str(t.id),)).fetchone()
        doc = json.loads(row["doc"])
        doc.pop("executor", None)
        doc["assigned_agent_type"] = "swe_agent"
        conn.execute("UPDATE tasks SET doc = ? WHERE id = ?", (json.dumps(doc), str(t.id)))

    loaded = ts.get_task(t.id)
    assert loaded.executor is not None
    assert loaded.executor.kind == "agent"
    assert loaded.executor.id == "swe_agent"
    assert loaded.assigned_agent_type == "swe_agent"


# ── assign_executor / clear_agent keep both fields in agreement ────────────

def test_assign_executor_writes_both_fields_for_agent():
    t = ts.create_task("work")
    ts.assign_executor(t.id, Executor(kind="agent", id="swe_agent"), {"x": 1}, run_id="r1")

    loaded = ts.get_task(t.id)
    assert loaded.executor.kind == "agent"
    assert loaded.executor.id == "swe_agent"
    assert loaded.assigned_agent_type == "swe_agent"
    assert loaded.assigned_agent_run_id == "r1"
    assert loaded.assigned_agent_params == {"x": 1}


def test_assign_executor_writes_both_fields_for_team():
    t = ts.create_task("work")
    ts.assign_executor(t.id, {"kind": "team", "id": "team-x"}, run_id="tr-1")

    loaded = ts.get_task(t.id)
    assert loaded.executor.kind == "team"
    assert loaded.executor.id == "team-x"
    assert loaded.assigned_agent_type == "team:team-x"


def test_assign_agent_is_a_thin_kind_agent_wrapper():
    """assign_agent (the original signature, called from a dozen places
    outside this stage's edit scope) must produce exactly what assign_executor
    would for kind='agent'."""
    t = ts.create_task("work")
    ts.assign_agent(t.id, "orchestrator", {"y": 2}, run_id="r9")

    loaded = ts.get_task(t.id)
    assert loaded.executor == Executor(kind="agent", id="orchestrator")
    assert loaded.assigned_agent_type == "orchestrator"


def test_clear_agent_clears_executor_too():
    t = ts.create_task("work")
    ts.assign_executor(t.id, Executor(kind="loop", id="loop-a"), run_id="lr-1")
    ts.clear_agent(t.id)

    loaded = ts.get_task(t.id)
    assert loaded.executor is None
    assert loaded.assigned_agent_type is None
    assert loaded.assigned_agent_run_id is None


def test_clear_executor_is_the_same_function():
    assert ts.clear_executor is ts.clear_agent


# ── assign_executor_to_task dispatch ────────────────────────────────────────

def test_assign_executor_to_task_dispatches_flow(monkeypatch):
    calls = []

    def fake_start_flow_run(task_id, flow_id, params):
        calls.append((task_id, flow_id, params))
        return ("flow-run-1", "sess-1")

    import flow.launcher as flow_launcher
    monkeypatch.setattr(flow_launcher, "start_flow_run", fake_start_flow_run)

    t = ts.create_task("work")
    result = assign_executor_to_task(t.id, {"kind": "flow", "id": "flow-a"}, {"desc": "go"},
                                     task_to_dict=task_to_dict)

    assert result["run_id"] == "flow-run-1"
    assert calls == [(str(t.id), "flow-a", {"desc": "go"})]
    updated = ts.get_task(t.id)
    assert updated.executor == Executor(kind="flow", id="flow-a")
    assert updated.assigned_agent_run_id == "flow-run-1"
    assert updated.status == TaskStatus.in_progress


def test_assign_executor_to_task_dispatches_team(monkeypatch):
    """teams.launcher.start_team_run is expected to claim the task itself
    (kind='team') the way teams.runner._claim_task does; this fake mirrors
    that so the dispatch path is exercised without teams' own subprocess
    machinery."""
    calls = []

    def fake_start_team_run(team_id, goal, *, workspace=None, task_id=None, **kw):
        calls.append((team_id, task_id))
        ts.assign_executor(UUID(task_id), Executor(kind="team", id=team_id), run_id="team-run-1")
        return SimpleNamespace(team_run_id="team-run-1")

    import teams.launcher as team_launcher
    monkeypatch.setattr(team_launcher, "start_team_run", fake_start_team_run)

    t = ts.create_task("work", description="do the thing")
    result = assign_executor_to_task(t.id, Executor(kind="team", id="team-a"), None,
                                     task_to_dict=task_to_dict)

    assert result["run_id"] == "team-run-1"
    assert calls == [("team-a", str(t.id))]
    updated = ts.get_task(t.id)
    assert updated.executor == Executor(kind="team", id="team-a")


def test_assign_executor_to_task_fixes_up_a_stale_agent_claim_for_team(monkeypatch):
    """Today teams.runner._claim_task still assigns kind='agent' (a display
    name). Until that lands, assign_executor_to_task must correct the
    executor it leaves behind rather than reporting the task as assigned to
    an agent named after the team."""
    def fake_start_team_run(team_id, goal, *, workspace=None, task_id=None, **kw):
        ts.assign_agent(UUID(task_id), f"Team: {team_id}", run_id="team-run-2")
        return SimpleNamespace(team_run_id="team-run-2")

    import teams.launcher as team_launcher
    monkeypatch.setattr(team_launcher, "start_team_run", fake_start_team_run)

    t = ts.create_task("work")
    assign_executor_to_task(t.id, Executor(kind="team", id="team-a"), None, task_to_dict=task_to_dict)

    updated = ts.get_task(t.id)
    assert updated.executor == Executor(kind="team", id="team-a")


def test_assign_executor_to_task_dispatches_loop(monkeypatch):
    started = []

    def fake_start_loop_run(loop_id, goal="", *, workspace=None, task_id=None, **kw):
        started.append((loop_id, task_id))
        return SimpleNamespace(loop_run_id="loop-run-1", task_id=task_id)

    import loops.launcher as loops_launcher
    monkeypatch.setattr(loops_launcher, "start_loop_run", fake_start_loop_run)

    t = ts.create_task("work")
    result = assign_executor_to_task(t.id, Executor(kind="loop", id="loop-a"), None,
                                     task_to_dict=task_to_dict)

    assert started == [("loop-a", str(t.id))]
    assert result["run_id"] == "loop-run-1"
    updated = ts.get_task(t.id)
    assert updated.executor == Executor(kind="loop", id="loop-a")
    assert updated.assigned_agent_run_id == "loop-run-1"
    assert updated.status == TaskStatus.in_progress


def test_assign_executor_to_task_refuses_a_stopped_task(monkeypatch):
    from tasks.assign import AssignError

    t = ts.create_task("work", status=TaskStatus.stopped)
    with pytest.raises(AssignError):
        assign_executor_to_task(t.id, Executor(kind="flow", id="flow-a"), None,
                                task_to_dict=task_to_dict)


# ── The /assign route accepts both bodies ───────────────────────────────────

def _client(*routers):
    import sys
    from pathlib import Path
    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    for r in routers:
        app.include_router(r)
    return TestClient(app)


def test_assign_route_accepts_legacy_agent_id_body(monkeypatch):
    from dashboard.backend.routes.tasks import router as tasks_router

    calls = []

    def fake_assign_agent_to_task(task_id, agent_id, params, *, task_to_dict):
        calls.append((str(task_id), agent_id))
        return {"task": {"id": str(task_id)}, "run_id": "r1", "pending_approval": False}

    monkeypatch.setattr("dashboard.backend.routes.tasks.assign_agent_to_task", fake_assign_agent_to_task)

    t = ts.create_task("work")
    resp = _client(tasks_router).post(f"/api/tasks/{t.id}/assign", json={"agent_id": "swe_agent"})

    assert resp.status_code == 200
    assert calls == [(str(t.id), "swe_agent")]


def test_assign_route_accepts_executor_body(monkeypatch):
    from dashboard.backend.routes.tasks import router as tasks_router

    calls = []

    def fake_assign_executor_to_task(task_id, executor, params, *, task_to_dict):
        calls.append((str(task_id), executor))
        return {"task": {"id": str(task_id)}, "run_id": "r2", "pending_approval": False}

    monkeypatch.setattr("dashboard.backend.routes.tasks.assign_executor_to_task", fake_assign_executor_to_task)

    t = ts.create_task("work")
    resp = _client(tasks_router).post(
        f"/api/tasks/{t.id}/assign",
        json={"executor": {"kind": "flow", "id": "flow-a"}, "params": {"desc": "go"}},
    )

    assert resp.status_code == 200
    assert calls == [(str(t.id), {"kind": "flow", "id": "flow-a"})]


def test_assign_route_refuses_a_body_with_neither():
    from dashboard.backend.routes.tasks import router as tasks_router

    t = ts.create_task("work")
    resp = _client(tasks_router).post(f"/api/tasks/{t.id}/assign", json={})
    assert resp.status_code == 400


# ── finalize_task: retry through the executor, review cycle ────────────────

def _set_max_retries(monkeypatch, n):
    import workspace
    monkeypatch.setattr(workspace, "get_workspace_metadata",
                        lambda ws: {"orchestrator": {"max_retries": n}})


def test_finalize_task_retries_a_failed_team_run_then_blocks_after_budget(monkeypatch):
    _set_max_retries(monkeypatch, 1)

    calls = []

    def fake_start_team_run(team_id, goal, *, workspace=None, task_id=None, **kw):
        calls.append(team_id)
        tid = UUID(task_id)
        ts.assign_executor(tid, Executor(kind="team", id=team_id), run_id="team-run-retry")
        ts.update_task(tid, status=TaskStatus.in_progress)
        return SimpleNamespace(team_run_id="team-run-retry")

    import teams.launcher as team_launcher
    monkeypatch.setattr(team_launcher, "start_team_run", fake_start_team_run)

    t = ts.create_task("work")
    ts.assign_executor(t.id, Executor(kind="team", id="team-a"), run_id="orig-run")

    from managers import run_manager as rm

    rm.finalize_task(str(t.id), "failed", 1, error="team boom")
    task = ts.get_task(t.id)
    assert task.status == TaskStatus.in_progress
    assert task.retry_count == 1
    assert calls == ["team-a"]

    # The retry budget (max_retries=1) is now spent: the next failure blocks.
    rm.finalize_task(str(t.id), "failed", 1, error="team boom again")
    task = ts.get_task(t.id)
    assert task.status == TaskStatus.blocked
    assert calls == ["team-a"]  # no second retry dispatched


def test_finalize_task_does_not_retry_a_stopped_run():
    t = ts.create_task("work")
    ts.assign_executor(t.id, Executor(kind="loop", id="loop-a"), run_id="lr-1")

    from managers import run_manager as rm
    rm.finalize_task(str(t.id), "stopped", 1)

    assert ts.get_task(t.id).status == TaskStatus.blocked


@pytest.fixture
def reviewer_available(monkeypatch):
    import agents.registry as registry

    def _get_agent(agent_id):
        return SimpleNamespace(id=agent_id) if agent_id == "code_reviewer" else None

    monkeypatch.setattr(registry, "get_agent", _get_agent)


def test_finalize_task_starts_review_cycle_for_a_completed_flow(reviewer_available, monkeypatch):
    review_calls = []

    def fake_start_run(task_id, agent_id, params=None, run_id=None):
        review_calls.append((str(task_id), agent_id))
        return (run_id or "review-run-1", "sess")

    import agents.agent_launcher as launcher
    monkeypatch.setattr(launcher, "start_run", fake_start_run)

    t = ts.create_task("work", status=TaskStatus.in_progress)
    ts.assign_executor(t.id, Executor(kind="flow", id="flow-a"), run_id="flow-run-9")

    from managers import run_manager as rm
    rm.finalize_task(str(t.id), "completed", 0)

    task = ts.get_task(t.id)
    assert task.status == TaskStatus.reviewing
    assert task.review_cycles == 1
    assert review_calls == [(str(t.id), "code_reviewer")]


def test_finalize_flow_task_is_a_thin_wrapper_around_finalize_task(reviewer_available, monkeypatch):
    """finalize_flow_task (what teams/runner.py and loops/runner.py call)
    must show the same retry/review behaviour finalize_task provides, since
    it now only forwards to it."""
    monkeypatch.setattr("agents.agent_launcher.start_run",
                        lambda task_id, agent_id, params=None, run_id=None: (run_id or "rr", "sess"))

    t = ts.create_task("work", status=TaskStatus.in_progress)
    ts.assign_executor(t.id, Executor(kind="team", id="team-b"), run_id="team-run-5")

    from managers import run_manager as rm
    rm.finalize_flow_task(str(t.id), "completed", 0)

    task = ts.get_task(t.id)
    assert task.status == TaskStatus.reviewing


# ── agent_state for a non-agent executor ────────────────────────────────────

def test_agent_state_for_team_executor_reads_the_entity_run():
    from common import entity_runs

    t = ts.create_task("work")
    ts.assign_executor(t.id, Executor(kind="team", id="team-a"), run_id="team-run-42")
    entity_runs.upsert({
        "run_id": "team-run-42", "kind": "team", "entity_id": "team-a",
        "status": "running", "workspace": t.workspace, "task_id": str(t.id),
    })

    assert ts.get_task(t.id).agent_state == AgentState.running


def test_agent_state_for_team_executor_with_no_run_yet_is_assigned():
    t = ts.create_task("work")
    ts.assign_executor(t.id, Executor(kind="team", id="team-a"))  # no run_id yet

    assert ts.get_task(t.id).agent_state == AgentState.assigned


def test_agent_state_for_team_executor_maps_completed():
    from common import entity_runs

    t = ts.create_task("work")
    ts.assign_executor(t.id, Executor(kind="team", id="team-a"), run_id="team-run-43")
    entity_runs.upsert({
        "run_id": "team-run-43", "kind": "team", "entity_id": "team-a",
        "status": "completed", "workspace": t.workspace, "task_id": str(t.id),
    })

    assert ts.get_task(t.id).agent_state == AgentState.completed


def test_assign_executor_to_task_dispatches_scenario(monkeypatch):
    started = []

    def fake_start_scenario_run(scenario_id, *, workspace=None, task_id=None, **kw):
        started.append((scenario_id, task_id))
        return SimpleNamespace(sim_run_id="sim-run-1", task_id=task_id)

    import playground.launcher as playground_launcher
    monkeypatch.setattr(playground_launcher, "start_scenario_run", fake_start_scenario_run)

    t = ts.create_task("simulate")
    result = assign_executor_to_task(t.id, Executor(kind="scenario", id="scn-a"), None,
                                     task_to_dict=task_to_dict)

    assert started == [("scn-a", str(t.id))]
    assert result["run_id"] == "sim-run-1"
    updated = ts.get_task(t.id)
    assert updated.executor == Executor(kind="scenario", id="scn-a")
    assert updated.assigned_agent_run_id == "sim-run-1"
    assert updated.status == TaskStatus.in_progress


def test_assign_scenario_configuration_refusal_is_a_400(monkeypatch):
    from tasks.assign import AssignError
    import playground.launcher as playground_launcher

    def refuse(scenario_id, **kw):
        raise ValueError("agents mode needs docker execution")

    monkeypatch.setattr(playground_launcher, "start_scenario_run", refuse)
    t = ts.create_task("simulate")
    with pytest.raises(AssignError) as exc:
        assign_executor_to_task(t.id, Executor(kind="scenario", id="scn-a"), None,
                                task_to_dict=task_to_dict)
    assert exc.value.status == 400
