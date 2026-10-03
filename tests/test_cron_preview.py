"""The cron preview helper and route: a dry-run of a cron expression's next
fire times, with no job created, used by the frontend's CronHint while a
user is still typing into a cron field (Deployments/Plan job forms, the
agent's heartbeat schedule).

Covers: plans.service.upcoming_runs (reuses _next_run, so it can never
disagree with what the scheduler itself would do), describe_cron, and the
GET /api/plan/cron/preview route.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from plans import service as ps

BERLIN = ZoneInfo("Europe/Berlin")


# -------------------- upcoming_runs --------------------

def test_upcoming_runs_returns_requested_count():
    runs = ps.upcoming_runs("0 9 * * *", "UTC", 5, start=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert len(runs) == 5


def test_upcoming_runs_are_strictly_increasing_and_after_start():
    start = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    runs = [datetime.fromisoformat(r) for r in ps.upcoming_runs("0 9 * * *", "UTC", 4, start=start)]
    assert runs == sorted(runs)
    assert all(r > start for r in runs)


def test_upcoming_runs_matches_next_run_in_timezone():
    # Weekday 09:00 Berlin time, same expression tests/test_plan_cron.py uses
    # for a real job's _next_run; the preview must land on the same instants.
    start = datetime(2026, 9, 22, 10, 0, tzinfo=BERLIN).astimezone(timezone.utc)
    runs = [datetime.fromisoformat(r) for r in ps.upcoming_runs("0 9 * * 1-5", "Europe/Berlin", 1, start=start)]
    assert runs[0].astimezone(BERLIN).hour == 9
    assert runs[0].astimezone(BERLIN).date() == datetime(2026, 9, 23).date()


def test_upcoming_runs_defaults_start_to_now():
    before = datetime.now(timezone.utc)
    runs = [datetime.fromisoformat(r) for r in ps.upcoming_runs("*/5 * * * *", "UTC", 1)]
    assert runs[0] > before


def test_upcoming_runs_rejects_bad_cron():
    with pytest.raises(ValueError, match="cron"):
        ps.upcoming_runs("not a cron", "UTC", 3)


def test_upcoming_runs_rejects_bad_timezone():
    with pytest.raises(ValueError, match="timezone"):
        ps.upcoming_runs("0 9 * * *", "Nowhere/Fake", 3)


# -------------------- describe_cron --------------------

@pytest.mark.parametrize("cron,expected", [
    ("*/15 * * * *", "every 15 minutes"),
    ("0 */2 * * *", "every 2 hours"),
    ("0 0 * * *", "daily at 00:00"),
    ("30 9 * * *", "daily at 09:30"),
    ("0 9 * * 1", "weekly on Monday at 09:00"),
])
def test_describe_cron_recognized_shapes(cron, expected):
    assert ps.describe_cron(cron) == expected


def test_describe_cron_unrecognized_shape_returns_none():
    assert ps.describe_cron("0 9 * * 1-5") is None
    assert ps.describe_cron("0 9 1 * *") is None
    assert ps.describe_cron("garbage") is None


def test_upcoming_runs_daily_keeps_local_hour_across_dst():
    """Daily at 02:30 Berlin time, starting before the October DST change: the
    local hour stays, the UTC hour moves, as the scheduler does it."""
    start = datetime(2030, 10, 25, 0, 30, tzinfo=timezone.utc)  # 02:30 CEST
    runs = ps.upcoming_runs(None, "Europe/Berlin", 4, start=start, recurrence=ps.Recurrence.daily)
    local = [datetime.fromisoformat(r).astimezone(ZoneInfo("Europe/Berlin")) for r in runs]
    assert [d.hour for d in local] == [2, 2, 2, 2]
    assert [d.minute for d in local] == [30, 30, 30, 30]
    assert runs[0] == start.isoformat()


def test_upcoming_runs_weekly_from_a_past_start_rolls_past_now():
    start = datetime(2020, 1, 6, 9, 0, tzinfo=timezone.utc)  # a Monday
    runs = ps.upcoming_runs(None, "UTC", 3, start=start, recurrence="weekly")
    times = [datetime.fromisoformat(r) for r in runs]
    assert times[0] > datetime.now(timezone.utc)
    assert all(t.weekday() == 0 and t.hour == 9 for t in times)
    assert times[1] - times[0] == times[2] - times[1]


def test_upcoming_runs_interval_needs_start():
    with pytest.raises(ValueError):
        ps.upcoming_runs(None, "UTC", 3, recurrence="hourly")
    with pytest.raises(ValueError):
        ps.upcoming_runs("0 9 * * *", "UTC", 3, recurrence="none")


# -------------------- route --------------------

@pytest.fixture
def plan_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))
    from routes import plan as plan_routes
    app = FastAPI()
    app.include_router(plan_routes.router)
    return TestClient(app)


def test_cron_preview_route_valid(plan_client):
    resp = plan_client.get("/api/plan/cron/preview", params={
        "cron": "0 9 * * *", "timezone": "Europe/Berlin", "count": 3,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["error"] is None
    assert len(body["upcoming_runs_at"]) == 3
    assert body["description"] == "daily at 09:00"


def test_cron_preview_route_invalid_cron_is_still_200(plan_client):
    resp = plan_client.get("/api/plan/cron/preview", params={"cron": "garbage"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert "cron" in body["error"].lower()
    assert body["upcoming_runs_at"] == []


def test_cron_preview_route_invalid_timezone_is_still_200(plan_client):
    resp = plan_client.get("/api/plan/cron/preview", params={"cron": "0 9 * * *", "timezone": "Bogus/Zone"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert "timezone" in body["error"].lower()


def test_cron_preview_route_missing_cron_is_invalid_not_422(plan_client):
    resp = plan_client.get("/api/plan/cron/preview")
    assert resp.status_code == 200
    assert resp.json()["valid"] is False


def test_cron_preview_route_count_is_capped(plan_client):
    resp = plan_client.get("/api/plan/cron/preview", params={"cron": "* * * * *", "count": 500})
    assert resp.status_code == 200
    assert len(resp.json()["upcoming_runs_at"]) <= 20


def test_cron_preview_route_honors_start(plan_client):
    resp = plan_client.get("/api/plan/cron/preview", params={
        "cron": "0 9 * * *", "start": "2026-01-01T00:00:00Z", "count": 1,
    })
    body = resp.json()
    assert body["upcoming_runs_at"][0].startswith("2026-01-01T09:00:00")


def test_cron_preview_route_hourly_from_start(plan_client):
    start = "2030-01-01T10:15:00+00:00"
    resp = plan_client.get("/api/plan/cron/preview", params={
        "recurrence": "hourly", "start": start, "count": 3,
    })
    body = resp.json()
    assert body["valid"] is True
    assert body["upcoming_runs_at"][0].startswith("2030-01-01T10:15")
    assert body["upcoming_runs_at"][2].startswith("2030-01-01T12:15")
    assert body["description"] is None
