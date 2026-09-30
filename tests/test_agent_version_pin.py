"""Pin an agent version on a run and on a scheduled job, and roll back.

Covers: agents/agent_launcher.py (``--definition-version`` flag),
runtime/agent_run.py (turning the flag into a ``create_agent`` override),
managers/runs/lifecycle.py (the run record's own ``agent_version``, for a
pinned, live, or A/B-experiment run), the task and scheduled-job fields
(tasks/service.py, tasks/storage.py, plans/models.py, plans/service.py),
the validation on the tasks and plan routes, and the run page's
``GET /api/runs/{run_id}/agent-version`` / ``POST /api/runs/{run_id}/rollback-agent``
(dashboard/backend/routes/messages.py).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from agents import prompt_assembly
from agents import versions as av
from agents.registry import AgentSpec, add_agent, replace_all_raw
from evals import experiments
from managers import run_manager as rm
from tasks import service as tasks_service

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


# -------------------- fixtures --------------------

@pytest.fixture(autouse=True)
def isolated_definitions(tmp_path, monkeypatch):
    """Keep generated definition markdown out of the real agents/definitions,
    the same way tests/test_agent_versions.py isolates it."""
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    return defs


@pytest.fixture(autouse=True)
def fresh_registry():
    """Start each test from an empty agents.json and no leftover experiment
    pin (the agent_versions/agent_experiments *rows* are reset per test by
    conftest's fresh_db already; the pin itself lives in a contextvar, which
    is not)."""
    replace_all_raw([])
    experiments.clear_pins()
    yield
    experiments.clear_pins()
    replace_all_raw([])


def _spec(agent_id: str, *, tools=None, temperature=None) -> AgentSpec:
    return AgentSpec(id=agent_id, name=agent_id, type="langchain",
                     entrypoint="agents.definitions.demo:build",
                     tools=list(tools or []), temperature=temperature)


@pytest.fixture
def two_versions():
    """``pin_agent`` with v1 (temperature 0.1) and v2 (temperature 0.9, the
    live definition)."""
    add_agent(_spec("pin_agent", temperature=0.1))
    v1 = av.ensure_current_version("pin_agent")
    add_agent(_spec("pin_agent", temperature=0.9))
    v2 = av.ensure_current_version("pin_agent")
    assert (v1, v2) == (1, 2)
    return v1, v2


# ==================== 1. agents/agent_launcher.py: the CLI flag ====================

def test_prepare_run_carries_the_task_pin_as_a_flag(monkeypatch):
    import agents.agent_launcher as launcher
    import agents.registry as registry

    monkeypatch.setattr(registry, "get_agent", lambda agent_id: object())
    t = tasks_service.create_task("pinned launch")
    tasks_service.update_task(t.id, agent_version=7)
    t = tasks_service.get_task(t.id)

    spec = launcher.prepare_run(str(t.id), "swe_agent", None)

    assert "--definition-version" in spec["cli_args"]
    idx = spec["cli_args"].index("--definition-version")
    assert spec["cli_args"][idx + 1] == "7"


def test_prepare_run_params_override_the_task_pin(monkeypatch):
    import agents.agent_launcher as launcher
    import agents.registry as registry

    monkeypatch.setattr(registry, "get_agent", lambda agent_id: object())
    t = tasks_service.create_task("pinned launch 2")
    tasks_service.update_task(t.id, agent_version=7)

    spec = launcher.prepare_run(str(t.id), "swe_agent", {"agent_version": 9})

    idx = spec["cli_args"].index("--definition-version")
    assert spec["cli_args"][idx + 1] == "9"


def test_prepare_run_omits_the_flag_when_not_pinned(monkeypatch):
    import agents.agent_launcher as launcher
    import agents.registry as registry

    monkeypatch.setattr(registry, "get_agent", lambda agent_id: object())
    t = tasks_service.create_task("unpinned launch")

    spec = launcher.prepare_run(str(t.id), "swe_agent", None)

    assert "--definition-version" not in spec["cli_args"]


# ==================== 2. runtime/agent_run.py: --definition-version ====================

def test_agent_run_cli_turns_the_flag_into_a_create_agent_override(monkeypatch, tmp_path):
    import runtime.agent_run as agent_run
    import agents.registry as registry

    saved_env = dict(os.environ)
    try:
        monkeypatch.setattr(registry, "get_agent", lambda agent_id: object())
        captured = {}

        def _fake_lifecycle(agent_id, ws, instruction, **kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(agent_run, "run_agent_lifecycle", _fake_lifecycle)
        monkeypatch.setattr(
            sys, "argv",
            ["agent_run.py", "swe_agent", "--workspace", str(tmp_path),
             "--definition-version", "3"],
        )

        agent_run.main()

        assert captured["overrides"].get("definition_version") == 3
    finally:
        os.environ.clear()
        os.environ.update(saved_env)


def test_agent_run_cli_omits_the_override_when_no_flag(monkeypatch, tmp_path):
    import runtime.agent_run as agent_run
    import agents.registry as registry

    saved_env = dict(os.environ)
    try:
        monkeypatch.setattr(registry, "get_agent", lambda agent_id: object())
        captured = {}

        def _fake_lifecycle(agent_id, ws, instruction, **kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(agent_run, "run_agent_lifecycle", _fake_lifecycle)
        monkeypatch.setattr(sys, "argv", ["agent_run.py", "swe_agent", "--workspace", str(tmp_path)])

        agent_run.main()

        assert "definition_version" not in captured["overrides"]
    finally:
        os.environ.clear()
        os.environ.update(saved_env)


# ==================== 3. managers/runs/lifecycle.py: the run's own agent_version ====================

def test_open_run_records_the_live_version_when_unpinned():
    add_agent(_spec("live_agent"))
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "live_agent", status="running", link_to_session=False)
    rec = rm.get_run_by_id(rid)
    assert rec["agent_version"] == av.ensure_current_version("live_agent")


def test_preopen_run_records_the_live_version_too():
    add_agent(_spec("preopen_pin_agent"))
    rid = rm.new_unique_run_id()
    rm.preopen_run(rid, "preopen_pin_agent", status="pending", link_to_session=False)
    rec = rm.get_run_by_id(rid)
    assert rec["agent_version"] == av.ensure_current_version("preopen_pin_agent")


def test_open_run_records_the_requested_pin(two_versions):
    v1, _v2 = two_versions
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "pin_agent", status="running", link_to_session=False, agent_version_pin=v1)
    rec = rm.get_run_by_id(rid)
    assert rec["agent_version"] == v1


def test_open_run_falls_back_when_the_pinned_version_is_gone(two_versions):
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "pin_agent", status="running", link_to_session=False, agent_version_pin=999)
    rec = rm.get_run_by_id(rid)
    assert rec["agent_version"] != 999
    assert rec["agent_version"] == av.ensure_current_version("pin_agent")


def test_open_run_records_the_experiment_arms_version(two_versions):
    v1, v2 = two_versions
    exp = experiments.put_experiment(
        "pin_agent", enabled=True, arms=[{"version": v1, "share": 0.5}, {"version": v2, "share": 0.5}])
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "pin_agent", status="running", link_to_session=False)
    expected = experiments.choose_arm(exp, f"run:{rid}")["version"]
    rec = rm.get_run_by_id(rid)
    assert rec["agent_version"] == expected


def test_explicit_pin_wins_over_an_open_experiment(two_versions):
    v1, v2 = two_versions
    experiments.put_experiment(
        "pin_agent", enabled=True, arms=[{"version": v1, "share": 0.5}, {"version": v2, "share": 0.5}])
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "pin_agent", status="running", link_to_session=False, agent_version_pin=v1)
    rec = rm.get_run_by_id(rid)
    assert rec["agent_version"] == v1


def test_version_for_hash_finds_the_matching_row(two_versions):
    v1, _v2 = two_versions
    h1 = av.get_version_row("pin_agent", v1)["hash"]
    assert av.version_for_hash("pin_agent", h1) == v1


def test_version_for_hash_returns_none_for_an_unknown_hash():
    assert av.version_for_hash("no_such_agent", "deadbeef") is None
    assert av.version_for_hash("no_such_agent", None) is None


# ==================== 4. tasks/service.py + tasks/storage.py fields ====================

def test_create_task_stores_agent_version_and_outcome():
    t = tasks_service.create_task(
        "with pin", agent_version=5, outcome={"rubric": "done", "max_iterations": 3},
    )
    assert t.agent_version == 5
    assert t.outcome == {"rubric": "done", "max_iterations": 3}


def test_validate_agent_version_is_a_noop_without_an_agent():
    tasks_service.validate_agent_version(None, 999)  # nothing assigned yet


def test_validate_agent_version_is_a_noop_for_a_null_pin():
    tasks_service.validate_agent_version("some_agent", None)  # a null pin always clears


def test_validate_agent_version_rejects_an_unknown_version(two_versions):
    with pytest.raises(ValueError, match="no version"):
        tasks_service.validate_agent_version("pin_agent", 999)


def test_validate_agent_version_accepts_a_real_version(two_versions):
    v1, _v2 = two_versions
    tasks_service.validate_agent_version("pin_agent", v1)  # must not raise


def test_add_subtask_does_not_inherit_the_parents_agent_version():
    parent = tasks_service.create_task("parent", agent_version=5)
    child = tasks_service.add_subtask(parent.id, "child")
    assert child.agent_version is None


def test_add_subtask_still_inherits_budget_and_environment_for_contrast():
    parent = tasks_service.create_task("parent2", budget_usd=3.0, environment_id="env-x")
    child = tasks_service.add_subtask(parent.id, "child2")
    assert child.budget_usd == 3.0
    assert child.environment_id == "env-x"


def test_add_subtask_accepts_an_explicit_agent_version():
    parent = tasks_service.create_task("parent3")
    child = tasks_service.add_subtask(parent.id, "child3", agent_version=2)
    assert child.agent_version == 2


# ==================== 5. dashboard/backend/routes/tasks.py validation ====================

@pytest.fixture
def tasks_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import tasks as tasks_routes
    app = FastAPI()
    app.include_router(tasks_routes.router)
    return TestClient(app)


def test_route_create_accepts_any_pin_with_no_agent_assigned(tasks_client, two_versions):
    v1, _v2 = two_versions
    resp = tasks_client.post("/api/tasks", json={"title": "t", "agent_version": v1})
    assert resp.status_code == 200
    assert resp.json()["agent_version"] == v1

    # Even an unknown version passes at creation: no agent yet to validate against.
    resp2 = tasks_client.post("/api/tasks", json={"title": "t2", "agent_version": 999})
    assert resp2.status_code == 200
    assert resp2.json()["agent_version"] == 999


def test_route_update_rejects_an_unknown_version_once_an_agent_is_assigned(tasks_client, two_versions):
    v1, _v2 = two_versions
    created = tasks_client.post("/api/tasks", json={"title": "t"}).json()
    tasks_service.assign_agent(UUID(created["id"]), "pin_agent", None)

    bad = tasks_client.patch(f"/api/tasks/{created['id']}", json={"agent_version": 999})
    assert bad.status_code == 400

    ok = tasks_client.patch(f"/api/tasks/{created['id']}", json={"agent_version": v1})
    assert ok.status_code == 200
    assert ok.json()["agent_version"] == v1


def test_route_update_clears_the_pin_with_null(tasks_client, two_versions):
    v1, _v2 = two_versions
    created = tasks_client.post("/api/tasks", json={"title": "t", "agent_version": v1}).json()
    resp = tasks_client.patch(f"/api/tasks/{created['id']}", json={"agent_version": None})
    assert resp.status_code == 200
    assert resp.json()["agent_version"] is None


# ==================== 6. plans/models.py + plans/service.py + routes/plan.py ====================

from plans import service as plan_service  # noqa: E402 - grouped with the section that uses it
from plans.models import JobKind, ScheduledJob  # noqa: E402
from plans.storage import PlanStore  # noqa: E402


@pytest.fixture
def plan_store(tmp_path, monkeypatch):
    """Isolated PlanStore wired in as plans.service's singleton (mirrors
    tests/test_plan_cron.py and tests/test_plan_fires.py)."""
    store = PlanStore(path=tmp_path / "plans.json")
    monkeypatch.setattr(plan_service, "plan_store", store)
    return store


def test_create_job_validates_agent_version_against_agent_id(plan_store, two_versions):
    v1, _v2 = two_versions
    with pytest.raises(ValueError, match="no version"):
        plan_service.create_job(
            kind=JobKind.agent_task, title="t", run_at=datetime.now(timezone.utc),
            agent_id="pin_agent", agent_version=999,
        )
    job = plan_service.create_job(
        kind=JobKind.agent_task, title="t", run_at=datetime.now(timezone.utc),
        agent_id="pin_agent", agent_version=v1,
    )
    assert job.agent_version == v1


def test_create_job_rejects_agent_version_without_agent_id(plan_store):
    with pytest.raises(ValueError, match="agent_id"):
        plan_service.create_job(
            kind=JobKind.notification, title="t", run_at=datetime.now(timezone.utc),
            agent_version=1,
        )


def test_update_job_validates_against_the_existing_agent_id(plan_store, two_versions):
    v1, _v2 = two_versions
    job = plan_store.add(ScheduledJob(
        kind=JobKind.agent_task, title="t", run_at=datetime.now(timezone.utc), agent_id="pin_agent",
    ))
    with pytest.raises(ValueError, match="no version"):
        plan_service.update_job(job.id, agent_version=999)
    updated = plan_service.update_job(job.id, agent_version=v1)
    assert updated.agent_version == v1


def test_fire_agent_task_passes_agent_version_when_agent_is_set(plan_store, monkeypatch):
    """Mirrors tests/test_plan_fires.py's own budget/environment coverage of
    the same function: create_task is stubbed so this stays a unit test of
    what _fire_agent_task copies, not of task creation itself."""
    captured = {}

    def _fake_create_task(**kw):
        captured.update(kw)

        class _T:
            id = "11111111-1111-1111-1111-111111111111"

        return _T()

    monkeypatch.setattr("tasks.service.create_task", _fake_create_task)
    monkeypatch.setattr("tasks.service.append_task_activity_log", lambda *a, **k: None)
    monkeypatch.setattr("tasks.service.update_task", lambda *a, **k: None)
    monkeypatch.setattr(plan_service, "create_notification", lambda **kw: None)
    from agents import agent_launcher
    monkeypatch.setattr(agent_launcher, "start_run", lambda *a, **k: ("run-1", "sess-1"))

    job = plan_store.add(ScheduledJob(
        kind=JobKind.agent_task, title="t",
        run_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        agent_id="pin_agent", agent_version=7,
    ))

    plan_service.fire_job(job)

    assert captured.get("agent_version") == 7


def test_fire_agent_task_omits_agent_version_without_a_preassigned_agent(plan_store, monkeypatch):
    captured = {}

    def _fake_create_task(**kw):
        captured.update(kw)

        class _T:
            id = "22222222-2222-2222-2222-222222222222"

        return _T()

    monkeypatch.setattr("tasks.service.create_task", _fake_create_task)
    monkeypatch.setattr("tasks.service.append_task_activity_log", lambda *a, **k: None)
    monkeypatch.setattr(plan_service, "create_notification", lambda **kw: None)

    job = plan_store.add(ScheduledJob(
        kind=JobKind.agent_task, title="t",
        run_at=datetime.now(timezone.utc) - timedelta(seconds=5),
    ))

    plan_service.fire_job(job)

    assert captured.get("agent_version") is None


@pytest.fixture
def plan_client(plan_store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import plan as plan_routes
    app = FastAPI()
    app.include_router(plan_routes.router)
    return TestClient(app)


def test_route_create_accepts_agent_version_with_agent_id(plan_client, two_versions):
    v1, _v2 = two_versions
    resp = plan_client.post("/api/plan/jobs", json={
        "kind": "agent_task", "title": "t", "delay_minutes": 5,
        "agent_id": "pin_agent", "agent_version": v1,
    })
    assert resp.status_code == 200
    assert resp.json()["agent_version"] == v1


def test_route_create_rejects_an_unknown_agent_version(plan_client, two_versions):
    resp = plan_client.post("/api/plan/jobs", json={
        "kind": "agent_task", "title": "t", "delay_minutes": 5,
        "agent_id": "pin_agent", "agent_version": 999,
    })
    assert resp.status_code == 400


# ==================== 7. dashboard/backend/routes/messages.py: agent-version + rollback ====================

@pytest.fixture
def runs_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import messages as messages_routes
    app = FastAPI()
    app.include_router(messages_routes.router)
    app.include_router(messages_routes.runs_router)
    return TestClient(app)


def test_get_agent_version_reports_a_pinned_run_against_the_live_one(runs_client, two_versions):
    v1, v2 = two_versions
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "pin_agent", status="running", link_to_session=False, agent_version_pin=v1)

    resp = runs_client.get(f"/api/runs/{rid}/agent-version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_id"] == "pin_agent"
    assert body["version"] == v1
    assert body["current_version"] == v2
    assert body["is_current"] is False
    assert body["pinned"] is False  # no task behind this run to carry the pin


def test_get_agent_version_reports_is_current_for_the_live_build(runs_client, two_versions):
    _v1, v2 = two_versions
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "pin_agent", status="running", link_to_session=False)

    body = runs_client.get(f"/api/runs/{rid}/agent-version").json()
    assert body["version"] == v2
    assert body["current_version"] == v2
    assert body["is_current"] is True


def test_get_agent_version_reports_pinned_when_the_task_carries_the_pin(runs_client, two_versions):
    v1, _v2 = two_versions
    t = tasks_service.create_task("t", agent_version=v1)
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "pin_agent", task_id=str(t.id), status="running",
               link_to_session=False, agent_version_pin=v1)

    body = runs_client.get(f"/api/runs/{rid}/agent-version").json()
    assert body["pinned"] is True


def test_agent_version_route_404s_for_an_unknown_run(runs_client):
    resp = runs_client.get("/api/runs/no-such-run/agent-version")
    assert resp.status_code == 404


def test_rollback_agent_restores_the_runs_version(runs_client, two_versions):
    v1, _v2 = two_versions
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "pin_agent", status="running", link_to_session=False, agent_version_pin=v1)

    resp = runs_client.post(f"/api/runs/{rid}/rollback-agent")
    assert resp.status_code == 200
    assert resp.json()["restored_to"] == v1

    from agents.registry import get_agent
    assert get_agent("pin_agent").temperature == 0.1  # v1's own temperature


def test_rollback_agent_400s_without_a_recorded_version(runs_client):
    add_agent(_spec("no_pin_agent"))
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "no_pin_agent", status="running", link_to_session=False)
    # Simulate a run that never resolved one (e.g. a very old record).
    rm.update_run(rid, {"agent_version": None})

    resp = runs_client.post(f"/api/runs/{rid}/rollback-agent")
    assert resp.status_code == 400


def test_rollback_agent_404s_for_an_unknown_run(runs_client):
    resp = runs_client.post("/api/runs/no-such-run/rollback-agent")
    assert resp.status_code == 404
