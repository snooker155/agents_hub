"""Cron recurrence and timezone handling for scheduled jobs.

Covers: validation at create/update time (service + route + tool), the cron
next_run computation, and the DST-safe hourly/daily/weekly computation that
keeps a job at the same local wall-clock time across a DST change.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from plans import service as ps
from plans.models import JobKind, Recurrence, ScheduledJob
from plans.storage import PlanStore

BERLIN = ZoneInfo("Europe/Berlin")


@pytest.fixture(autouse=True)
def isolated_plan_store(tmp_path, monkeypatch):
    """Give every test its own throwaway plans.json.

    plans.service.plan_store is a module-level singleton pointed at the
    shared test state root; without this, jobs created by one test (e.g. via
    create_job) would sit in that same file and get picked up by an unrelated
    test's claim_due_jobs()/run-now call later in the session.
    """
    monkeypatch.setattr(ps, "plan_store", PlanStore(path=tmp_path / "plans.json"))


def _job(run_at_local, recurrence, *, cron=None, tz="Europe/Berlin"):
    run_at_utc = run_at_local.astimezone(timezone.utc)
    return ScheduledJob(
        kind=JobKind.notification,
        title="job",
        run_at=run_at_utc,
        recurrence=recurrence,
        cron=cron,
        timezone=tz,
    )


# -------------------- validation --------------------

def test_create_job_defaults_timezone_to_utc():
    job = ps.create_job(kind=JobKind.notification, title="t", run_at=datetime.now(timezone.utc))
    assert job.timezone == "UTC"
    assert job.cron is None


def test_create_job_rejects_bad_timezone():
    with pytest.raises(ValueError, match="timezone"):
        ps.create_job(
            kind=JobKind.notification, title="t", run_at=datetime.now(timezone.utc),
            timezone="Not/AZone",
        )


def test_create_job_cron_requires_expression():
    with pytest.raises(ValueError, match="cron"):
        ps.create_job(
            kind=JobKind.notification, title="t", run_at=datetime.now(timezone.utc),
            recurrence=Recurrence.cron,
        )


def test_create_job_rejects_bad_cron_expression():
    with pytest.raises(ValueError, match="cron"):
        ps.create_job(
            kind=JobKind.notification, title="t", run_at=datetime.now(timezone.utc),
            recurrence=Recurrence.cron, cron="not a cron",
        )


def test_create_job_accepts_valid_cron_and_timezone():
    job = ps.create_job(
        kind=JobKind.notification, title="t", run_at=datetime.now(timezone.utc),
        recurrence=Recurrence.cron, cron="0 9 * * 1-5", timezone="Europe/Berlin",
    )
    assert job.cron == "0 9 * * 1-5"
    assert job.timezone == "Europe/Berlin"


def test_update_job_validates_timezone():
    job = ps.create_job(kind=JobKind.notification, title="t", run_at=datetime.now(timezone.utc))
    with pytest.raises(ValueError, match="timezone"):
        ps.update_job(job.id, timezone="Nowhere/Fake")


def test_update_job_switching_to_cron_requires_expression():
    job = ps.create_job(kind=JobKind.notification, title="t", run_at=datetime.now(timezone.utc))
    with pytest.raises(ValueError, match="cron"):
        ps.update_job(job.id, recurrence=Recurrence.cron)


def test_update_job_switching_to_cron_with_expression_ok():
    job = ps.create_job(kind=JobKind.notification, title="t", run_at=datetime.now(timezone.utc))
    updated = ps.update_job(job.id, recurrence=Recurrence.cron, cron="*/5 * * * *")
    assert updated.recurrence == Recurrence.cron
    assert updated.cron == "*/5 * * * *"


# -------------------- cron next_run --------------------

def test_next_run_cron_is_next_weekday_9am_in_tz():
    # A Tuesday at 10:00 Berlin time; next weekday-9am slot is tomorrow.
    now_local = datetime(2026, 9, 22, 10, 0, tzinfo=BERLIN)
    job = _job(now_local, Recurrence.cron, cron="0 9 * * 1-5")
    nxt = ps._next_run(job, now_local.astimezone(timezone.utc))
    nxt_local = nxt.astimezone(BERLIN)
    assert nxt_local.hour == 9 and nxt_local.minute == 0
    assert nxt_local.date() == datetime(2026, 9, 23).date()


def test_next_run_cron_rolls_forward_past_missed_occurrences():
    # run_at is far in the past (backend was down); next_run anchors on "now",
    # not on run_at, so it jumps straight to the next future slot.
    run_at_local = datetime(2026, 1, 1, 9, 0, tzinfo=BERLIN)
    job = _job(run_at_local, Recurrence.cron, cron="0 9 * * *")
    now = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)
    nxt = ps._next_run(job, now)
    assert nxt > now
    nxt_local = nxt.astimezone(BERLIN)
    assert nxt_local.date() == datetime(2026, 9, 23).date()


# -------------------- DST: daily job stays at the same local time --------------------

def test_daily_job_stays_at_local_time_across_spring_forward():
    # Last Sunday of March 2026 = March 29 (Berlin springs forward at 2am->3am).
    run_at_local = datetime(2026, 3, 28, 9, 0, tzinfo=BERLIN)  # CET, +01:00
    job = _job(run_at_local, Recurrence.daily)
    now = run_at_local.astimezone(timezone.utc)
    nxt = ps._next_run(job, now)
    nxt_local = nxt.astimezone(BERLIN)

    assert nxt_local.date() == datetime(2026, 3, 29).date()
    assert (nxt_local.hour, nxt_local.minute) == (9, 0)
    assert nxt_local.utcoffset().total_seconds() == 2 * 3600  # now CEST
    # Wall time is unchanged, but only 23 real hours elapsed (lost to DST).
    assert nxt - now == timedelta(hours=23)


def test_daily_job_stays_at_local_time_across_fall_back():
    # Last Sunday of October 2026 = October 25 (Berlin falls back at 3am->2am).
    run_at_local = datetime(2026, 10, 24, 9, 0, tzinfo=BERLIN)  # CEST, +02:00
    job = _job(run_at_local, Recurrence.daily)
    now = run_at_local.astimezone(timezone.utc)
    nxt = ps._next_run(job, now)
    nxt_local = nxt.astimezone(BERLIN)

    assert nxt_local.date() == datetime(2026, 10, 25).date()
    assert (nxt_local.hour, nxt_local.minute) == (9, 0)
    assert nxt_local.utcoffset().total_seconds() == 1 * 3600  # now CET
    # Wall time is unchanged, but 25 real hours elapsed (gained from DST).
    assert nxt - now == timedelta(hours=25)


def test_weekly_job_also_uses_local_wall_clock():
    run_at_local = datetime(2026, 3, 22, 9, 0, tzinfo=BERLIN)  # Sunday before the switch
    job = _job(run_at_local, Recurrence.weekly)
    now = run_at_local.astimezone(timezone.utc)
    nxt = ps._next_run(job, now)
    nxt_local = nxt.astimezone(BERLIN)
    assert nxt_local.date() == datetime(2026, 3, 29).date()
    assert (nxt_local.hour, nxt_local.minute) == (9, 0)


def test_hourly_job_defaults_to_utc_when_no_timezone_set():
    now = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)
    job = ScheduledJob(kind=JobKind.notification, title="t", run_at=now, recurrence=Recurrence.hourly)
    nxt = ps._next_run(job, now)
    assert nxt == now.replace(hour=11)


# -------------------- route-level 400s --------------------

@pytest.fixture
def plan_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))
    from routes import plan as plan_routes
    app = FastAPI()
    app.include_router(plan_routes.router)
    return TestClient(app)


def test_route_rejects_bad_timezone_with_400(plan_client):
    resp = plan_client.post("/api/plan/jobs", json={
        "kind": "notification", "title": "t", "delay_minutes": 5, "timezone": "Bogus/Zone",
    })
    assert resp.status_code == 400
    assert "timezone" in resp.json()["detail"].lower()


def test_route_rejects_cron_without_expression_with_400(plan_client):
    resp = plan_client.post("/api/plan/jobs", json={
        "kind": "notification", "title": "t", "delay_minutes": 5, "recurrence": "cron",
    })
    assert resp.status_code == 400
    assert "cron" in resp.json()["detail"].lower()


def test_route_accepts_valid_cron_job(plan_client):
    resp = plan_client.post("/api/plan/jobs", json={
        "kind": "notification", "title": "t", "delay_minutes": 5,
        "recurrence": "cron", "cron": "0 9 * * *", "timezone": "Europe/Berlin",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["cron"] == "0 9 * * *"
    assert body["timezone"] == "Europe/Berlin"


def test_route_update_rejects_bad_cron_with_400(plan_client):
    created = plan_client.post("/api/plan/jobs", json={
        "kind": "notification", "title": "t", "delay_minutes": 5,
    }).json()
    resp = plan_client.patch(f"/api/plan/jobs/{created['id']}", json={
        "recurrence": "cron", "cron": "garbage",
    })
    assert resp.status_code == 400


# -------------------- tool-level JSON errors --------------------

def test_schedule_task_tool_reports_bad_cron_as_json_error():
    from tools.schedule_management import schedule_task
    import json as _json

    out = schedule_task.invoke({
        "title": "t", "delay_minutes": 5, "recurrence": "cron", "cron": "nope",
    })
    body = _json.loads(out)
    assert body["ok"] is False
    assert "cron" in body["error"].lower()


def test_schedule_task_tool_accepts_cron_and_timezone():
    from tools.schedule_management import schedule_task
    import json as _json

    out = schedule_task.invoke({
        "title": "t", "delay_minutes": 5, "recurrence": "cron",
        "cron": "0 9 * * *", "timezone": "Europe/Berlin",
    })
    body = _json.loads(out)
    assert body["ok"] is True
    assert body["job"]["cron"] == "0 9 * * *"
    assert body["job"]["timezone"] == "Europe/Berlin"
