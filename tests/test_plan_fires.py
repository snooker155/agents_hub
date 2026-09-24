"""The scheduler firing journal, auto pause, and the environment/budget fields
copied from a ScheduledJob onto the task(s) it creates.

Mirrors the fixture style of test_plan_scheduler.py: plans.service's module
singletons (plan_store, fire_store) are pointed at throwaway stores per test,
and side effects that reach outside plans/ (notifications, task creation) are
stubbed so these tests exercise only the bookkeeping this module owns.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from plans import service as ps
from plans.models import JobKind, JobStatus, Notification, Recurrence, ScheduledJob
from plans.storage import FireStore, PlanStore

UTC = timezone.utc
BERLIN = ZoneInfo("Europe/Berlin")


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Isolated PlanStore + FireStore wired in as plans.service's singletons."""
    s = PlanStore(path=tmp_path / "plans.json")
    monkeypatch.setattr(ps, "plan_store", s)
    monkeypatch.setattr(ps, "fire_store", FireStore())
    return s


@pytest.fixture
def no_side_effects(monkeypatch):
    """Stand in for create_notification (used for both the ordinary firing
    notice and the auto-pause alert) and record which job ids it fired for."""
    fired = []

    def _fake_create_notification(**kw):
        fired.append(kw.get("source", {}).get("job_id"))
        return Notification(title=kw.get("title", ""), body=kw.get("body", ""))

    monkeypatch.setattr(ps, "create_notification", _fake_create_notification)
    return fired


def _job(**kw):
    defaults = dict(kind=JobKind.notification, title="t", run_at=datetime.now(UTC))
    defaults.update(kw)
    return ScheduledJob(**defaults)


# -------------------- journal: one row per attempt --------------------

def test_journal_records_a_successful_fire(store, no_side_effects):
    job = store.add(_job(run_at=datetime.now(UTC) - timedelta(seconds=5)))

    ps.fire_job(job)

    rows = ps.fire_store.list_for_job(job.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.ok is True
    assert row.error_type is None
    assert row.error is None
    assert row.trigger == "schedule"
    assert row.notification_id is not None
    assert row.duration_ms is not None and row.duration_ms >= 0


def test_journal_records_a_failed_fire(store, no_side_effects, monkeypatch):
    monkeypatch.setattr(ps, "_fire_agent_task", lambda job: (_ for _ in ()).throw(RuntimeError("boom")))
    job = store.add(_job(kind=JobKind.agent_task, run_at=datetime.now(UTC) - timedelta(seconds=5)))

    result = ps.fire_job(job)

    assert result["ok"] is False
    rows = ps.fire_store.list_for_job(job.id)
    assert len(rows) == 1
    assert rows[0].ok is False
    assert rows[0].error == "boom"
    assert rows[0].error_type == "other"


def test_journal_records_the_skipped_slot_case(store, no_side_effects):
    now = datetime.now(UTC)
    job = store.add(_job(run_at=now - timedelta(seconds=5), recurrence=Recurrence.none))
    store.update(
        job.id, last_fired_slot=now - timedelta(seconds=5),
        lease_owner="crashed-owner", lease_until=now - timedelta(seconds=1),
    )
    claimed = store.claim_due_jobs(datetime.now(UTC), "owner-b", lease_seconds=60)[0]

    result = ps.fire_job(claimed)

    assert result.get("skipped") == "already_fired_this_slot"
    rows = ps.fire_store.list_for_job(job.id)
    assert len(rows) == 1
    assert rows[0].ok is True
    assert rows[0].error_type == "skipped_slot"


def test_run_now_records_trigger_manual(store, no_side_effects):
    job = store.add(_job(run_at=datetime.now(UTC) - timedelta(seconds=5)))

    ps.fire_job(job, trigger="manual")

    rows = ps.fire_store.list_for_job(job.id)
    assert rows[0].trigger == "manual"


def test_fire_job_default_trigger_is_schedule(store, no_side_effects):
    job = store.add(_job(run_at=datetime.now(UTC) - timedelta(seconds=5)))

    ps.fire_job(job)

    assert ps.fire_store.list_for_job(job.id)[0].trigger == "schedule"


# -------------------- classify_fire_error --------------------

def test_classify_agent_missing():
    assert ps.classify_fire_error(ValueError("Unknown agent_id: ghost")) == "agent_missing"


def test_classify_flow_missing():
    assert ps.classify_fire_error(ValueError("Flow not found: xyz")) == "flow_missing"


def test_classify_loop_missing():
    assert ps.classify_fire_error(ValueError("Loop not found: xyz")) == "loop_missing"


def test_classify_loop_busy():
    err = RuntimeError("skipped: run abc123 of loop 'nightly' is still running")
    assert ps.classify_fire_error(err) == "loop_busy"


def test_classify_budget_exceeded_by_type():
    from common.budget import BudgetExceededError
    err = BudgetExceededError("default", 5.0, 3.0, "monthly")
    assert ps.classify_fire_error(err) == "budget_exceeded"


def test_classify_capacity():
    err = RuntimeError("job is locked by another firing, or is not in a fireable status")
    assert ps.classify_fire_error(err) == "capacity"


def test_classify_workspace_missing():
    err = FileNotFoundError("Workspace 'ghost-ws' does not exist")
    assert ps.classify_fire_error(err) == "workspace_missing"


def test_classify_other_for_unrecognized_error():
    assert ps.classify_fire_error(RuntimeError("something unexpected happened")) == "other"


def test_classify_accepts_a_plain_string():
    assert ps.classify_fire_error("Flow not found: xyz") == "flow_missing"


# -------------------- auto pause --------------------

def _fire_next_slot(store, job_id):
    """Re-fetch the job (run_at advances every fire) and fire it again."""
    job = store.get(job_id)
    return ps.fire_job(job)


def test_auto_pause_after_default_threshold(store, no_side_effects, monkeypatch):
    monkeypatch.setattr(ps, "_fire_agent_task", lambda job: (_ for _ in ()).throw(RuntimeError("boom")))
    job = store.add(_job(
        kind=JobKind.agent_task, run_at=datetime.now(UTC) - timedelta(seconds=5),
        recurrence=Recurrence.hourly,
    ))
    assert job.auto_pause_after == 3

    _fire_next_slot(store, job.id)
    after_1 = store.get(job.id)
    assert after_1.status == JobStatus.scheduled
    assert after_1.consecutive_errors == 1

    _fire_next_slot(store, job.id)
    after_2 = store.get(job.id)
    assert after_2.status == JobStatus.scheduled
    assert after_2.consecutive_errors == 2

    _fire_next_slot(store, job.id)
    after_3 = store.get(job.id)
    assert after_3.status == JobStatus.paused
    assert after_3.paused_reason == "errors"
    assert after_3.consecutive_errors == 3


def test_auto_pause_disabled_when_zero(store, no_side_effects, monkeypatch):
    monkeypatch.setattr(ps, "_fire_agent_task", lambda job: (_ for _ in ()).throw(RuntimeError("boom")))
    job = store.add(_job(
        kind=JobKind.agent_task, run_at=datetime.now(UTC) - timedelta(seconds=5),
        recurrence=Recurrence.hourly, auto_pause_after=0,
    ))

    for _ in range(5):
        _fire_next_slot(store, job.id)

    final = store.get(job.id)
    assert final.status == JobStatus.scheduled
    assert final.consecutive_errors == 5


def test_immediate_pause_on_agent_missing(store, no_side_effects, monkeypatch):
    monkeypatch.setattr(
        ps, "_fire_agent_task",
        lambda job: (_ for _ in ()).throw(ValueError("Unknown agent_id: ghost")),
    )
    job = store.add(_job(
        kind=JobKind.agent_task, run_at=datetime.now(UTC) - timedelta(seconds=5),
        recurrence=Recurrence.hourly,
    ))

    ps.fire_job(job)

    after = store.get(job.id)
    assert after.status == JobStatus.paused
    assert after.paused_reason == "target_missing"
    assert after.consecutive_errors == 1


def test_one_off_job_does_not_auto_pause_on_error(store, no_side_effects, monkeypatch):
    """recurrence=none is already terminal (failed) on its single firing, so
    auto-pause (which only makes sense for a job that would fire again) never
    applies to it."""
    monkeypatch.setattr(ps, "_fire_agent_task", lambda job: (_ for _ in ()).throw(RuntimeError("boom")))
    job = store.add(_job(
        kind=JobKind.agent_task, run_at=datetime.now(UTC) - timedelta(seconds=5),
        recurrence=Recurrence.none,
    ))

    ps.fire_job(job)

    after = store.get(job.id)
    assert after.status == JobStatus.failed
    assert after.paused_reason is None


def test_success_resets_consecutive_errors(store, no_side_effects, monkeypatch):
    calls = {"n": 0}

    def _flaky(job):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("boom")
        return "task-ok"

    monkeypatch.setattr(ps, "_fire_agent_task", _flaky)
    job = store.add(_job(
        kind=JobKind.agent_task, run_at=datetime.now(UTC) - timedelta(seconds=5),
        recurrence=Recurrence.hourly,
    ))

    _fire_next_slot(store, job.id)
    _fire_next_slot(store, job.id)
    assert store.get(job.id).consecutive_errors == 2

    _fire_next_slot(store, job.id)
    after = store.get(job.id)
    assert after.consecutive_errors == 0
    assert after.status == JobStatus.scheduled


def test_resume_clears_paused_reason_and_consecutive_errors(store, no_side_effects, monkeypatch):
    monkeypatch.setattr(ps, "_fire_agent_task", lambda job: (_ for _ in ()).throw(RuntimeError("boom")))
    job = store.add(_job(
        kind=JobKind.agent_task, run_at=datetime.now(UTC) - timedelta(seconds=5),
        recurrence=Recurrence.hourly, auto_pause_after=1,
    ))
    ps.fire_job(job)
    paused = store.get(job.id)
    assert paused.status == JobStatus.paused

    resumed = ps.resume_job(job.id)

    assert resumed.status == JobStatus.scheduled
    assert resumed.paused_reason is None
    assert resumed.consecutive_errors == 0


def test_pause_job_sets_manual_reason(store):
    job = store.add(_job(run_at=datetime.now(UTC) + timedelta(hours=1)))
    paused = ps.pause_job(job.id)
    assert paused.status == JobStatus.paused
    assert paused.paused_reason == "manual"


# -------------------- upcoming_runs_at --------------------

def test_upcoming_runs_at_for_cron():
    run_at_local = datetime(2026, 9, 23, 9, 0, tzinfo=BERLIN)
    job = ScheduledJob(
        kind=JobKind.notification, title="t", run_at=run_at_local.astimezone(UTC),
        recurrence=Recurrence.cron, cron="0 9 * * *", timezone="Europe/Berlin",
    )
    data = ps.job_to_dict(job)
    upcoming = data["upcoming_runs_at"]
    assert len(upcoming) == 3
    days = [datetime.fromisoformat(u).astimezone(BERLIN).date() for u in upcoming]
    assert days == [datetime(2026, 9, 23).date(), datetime(2026, 9, 24).date(), datetime(2026, 9, 25).date()]


def test_upcoming_runs_at_for_daily():
    run_at_local = datetime(2026, 9, 23, 9, 0, tzinfo=BERLIN)
    job = ScheduledJob(
        kind=JobKind.notification, title="t", run_at=run_at_local.astimezone(UTC),
        recurrence=Recurrence.daily, timezone="Europe/Berlin",
    )
    data = ps.job_to_dict(job)
    upcoming = data["upcoming_runs_at"]
    assert len(upcoming) == 3
    hours = [datetime.fromisoformat(u).astimezone(BERLIN).hour for u in upcoming]
    assert hours == [9, 9, 9]


def test_upcoming_runs_at_empty_for_one_off():
    job = ScheduledJob(kind=JobKind.notification, title="t", run_at=datetime.now(UTC))
    assert ps.job_to_dict(job)["upcoming_runs_at"] == []


def test_upcoming_runs_at_empty_when_paused():
    job = ScheduledJob(
        kind=JobKind.notification, title="t", run_at=datetime.now(UTC),
        recurrence=Recurrence.daily, status=JobStatus.paused,
    )
    assert ps.job_to_dict(job)["upcoming_runs_at"] == []


# -------------------- job_to_dict: last_fire --------------------

def test_job_to_dict_last_fire_is_none_before_any_fire(store):
    job = store.add(_job(run_at=datetime.now(UTC) + timedelta(hours=1)))
    assert ps.job_to_dict(job)["last_fire"] is None


def test_job_to_dict_last_fire_reflects_the_newest_journal_row(store, no_side_effects):
    job = store.add(_job(run_at=datetime.now(UTC) - timedelta(seconds=5)))
    ps.fire_job(job)
    updated = store.get(job.id)
    data = ps.job_to_dict(updated)
    assert data["last_fire"]["ok"] is True
    assert data["last_fire"]["job_id"] == str(job.id)


# -------------------- budget/environment copied onto the task --------------------

def test_fire_agent_task_passes_budget_and_environment_to_create_task(store, no_side_effects, monkeypatch):
    captured = {}

    def _fake_create_task(**kw):
        captured.update(kw)
        class _T:
            id = "11111111-1111-1111-1111-111111111111"
        return _T()

    monkeypatch.setattr("tasks.service.create_task", _fake_create_task)
    monkeypatch.setattr("tasks.service.append_task_activity_log", lambda *a, **k: None)
    monkeypatch.setattr("tasks.service.update_task", lambda *a, **k: None)

    job = store.add(_job(
        kind=JobKind.agent_task, run_at=datetime.now(UTC) - timedelta(seconds=5),
        budget_usd=12.5, environment_id="env-1",
    ))

    ps.fire_job(job)

    assert captured.get("budget_usd") == 12.5
    assert captured.get("environment_id") == "env-1"


# -------------------- environment_id validation --------------------

def test_create_job_accepts_a_valid_environment_id(store):
    from environments import service as env_service

    env = env_service.create_environment({"name": "sandbox"})

    job = ps.create_job(
        kind=JobKind.notification, title="t", run_at=datetime.now(UTC),
        environment_id=env.id,
    )

    assert job.environment_id == env.id


def test_create_job_rejects_an_archived_environment_id(store):
    from environments import service as env_service

    env = env_service.create_environment({"name": "sandbox"})
    env_service.archive_environment(env.id)

    with pytest.raises(ValueError, match="archived"):
        ps.create_job(
            kind=JobKind.notification, title="t", run_at=datetime.now(UTC),
            environment_id=env.id,
        )


# -------------------- routes --------------------

@pytest.fixture
def plan_client(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))
    from routes import plan as plan_routes
    app = FastAPI()
    app.include_router(plan_routes.router)
    return TestClient(app)


def test_route_job_fires_lists_newest_first(plan_client, no_side_effects):
    created = plan_client.post("/api/plan/jobs", json={
        "kind": "notification", "title": "t", "delay_minutes": 5,
    }).json()
    job_id = created["id"]

    resp = plan_client.post(f"/api/plan/jobs/{job_id}/run-now")
    assert resp.status_code == 200

    fires_resp = plan_client.get(f"/api/plan/jobs/{job_id}/fires")
    assert fires_resp.status_code == 200
    rows = fires_resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["job_id"] == job_id
    assert row["trigger"] == "manual"
    assert row["ok"] is True
    assert set(row) >= {
        "id", "job_id", "at", "slot", "trigger", "ok", "error_type", "error",
        "task_id", "notification_id", "loop_run_id", "duration_ms",
    }


def test_route_cross_job_fires(plan_client, no_side_effects):
    created = plan_client.post("/api/plan/jobs", json={
        "kind": "notification", "title": "t", "delay_minutes": 5,
    }).json()
    plan_client.post(f"/api/plan/jobs/{created['id']}/run-now")

    resp = plan_client.get("/api/plan/fires")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_route_run_now_allowed_while_paused(plan_client, no_side_effects):
    created = plan_client.post("/api/plan/jobs", json={
        "kind": "notification", "title": "t", "delay_minutes": 5,
    }).json()
    pause_resp = plan_client.post(f"/api/plan/jobs/{created['id']}/pause")
    assert pause_resp.status_code == 200
    assert pause_resp.json()["status"] == "paused"

    run_resp = plan_client.post(f"/api/plan/jobs/{created['id']}/run-now")
    assert run_resp.status_code == 200
    assert run_resp.json()["result"]["ok"] is True


def test_route_create_accepts_environment_budget_and_auto_pause(plan_client):
    from environments import service as env_service
    env = env_service.create_environment({"name": "sandbox"})

    resp = plan_client.post("/api/plan/jobs", json={
        "kind": "notification", "title": "t", "delay_minutes": 5,
        "environment_id": env.id, "budget_usd": 4.5, "auto_pause_after": 1,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["environment_id"] == env.id
    assert body["budget_usd"] == 4.5
    assert body["auto_pause_after"] == 1


def test_route_create_rejects_an_unknown_environment_id(plan_client):
    resp = plan_client.post("/api/plan/jobs", json={
        "kind": "notification", "title": "t", "delay_minutes": 5,
        "environment_id": "no-such-env",
    })
    assert resp.status_code == 400


def test_route_jobs_filters_by_kinds(plan_client):
    plan_client.post("/api/plan/jobs", json={
        "kind": "notification", "title": "reminder", "delay_minutes": 5,
    })
    resp = plan_client.post("/api/plan/jobs", json={
        "kind": "agent_task", "title": "task", "delay_minutes": 5,
    })
    assert resp.status_code == 200

    only_tasks = plan_client.get("/api/plan/jobs", params={"kinds": "agent_task,flow,loop"}).json()
    assert len(only_tasks) == 1
    assert only_tasks[0]["kind"] == "agent_task"

    everything = plan_client.get("/api/plan/jobs").json()
    assert len(everything) == 2


def test_route_job_dict_includes_new_fields(plan_client):
    created = plan_client.post("/api/plan/jobs", json={
        "kind": "notification", "title": "t", "delay_minutes": 5,
    }).json()
    assert "upcoming_runs_at" in created
    assert "last_fire" in created
    assert created["last_fire"] is None
    assert created["consecutive_errors"] == 0
    assert created["paused_reason"] is None
    assert created["auto_pause_after"] == 3


# -------------------- maintenance: journal retention --------------------

def test_prune_old_fires_removes_only_old_rows(store, no_side_effects):
    from common import maintenance
    from plans.models import FireRecord

    old = FireRecord(job_id=store.add(_job()).id, at=datetime.now(UTC) - timedelta(days=200), ok=True)
    recent = FireRecord(job_id=store.add(_job()).id, at=datetime.now(UTC) - timedelta(days=1), ok=True)
    ps.fire_store.add(old)
    ps.fire_store.add(recent)

    removed = maintenance.prune_old_fires(90)

    assert removed == 1
    remaining_ids = {str(f.id) for f in ps.fire_store.list()}
    assert str(old.id) not in remaining_ids
    assert str(recent.id) in remaining_ids


def test_prune_old_fires_disabled_when_zero(store):
    from common import maintenance
    from plans.models import FireRecord

    ps.fire_store.add(FireRecord(job_id=store.add(_job()).id, at=datetime.now(UTC) - timedelta(days=999)))
    assert maintenance.prune_old_fires(0) == 0
    assert len(ps.fire_store.list()) == 1
