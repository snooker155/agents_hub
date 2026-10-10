"""The proactive profile and the pulse (proactive/, docs/proactive.md).

Profile validation and the schedule it amounts to; the heartbeat job the
profile owns in plans/; the gates a tick passes (quiet hours, busy, tick
limit, daily budget); the structured outcome written back onto the journal
when the tick's run finishes, with its notification rules and the run-error
counter; and the routes of the Pulse tab. plans.service's singletons are
pointed at throwaway stores as in test_plan_fires.py, and the task launch is
stubbed so these tests exercise only the bookkeeping this feature owns.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from agents.registry import AgentSpec, add_agent, get_agent, replace_all_raw
from plans import service as ps
from plans.models import FireRecord, JobKind, JobStatus, Notification
from plans.storage import FireStore, PlanStore
from proactive import profile as pp
from proactive import service as svc

UTC = timezone.utc
BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


@pytest.fixture(autouse=True)
def fresh_registry():
    replace_all_raw([])
    yield
    replace_all_raw([])


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = PlanStore(path=tmp_path / "plans.json")
    monkeypatch.setattr(ps, "plan_store", s)
    monkeypatch.setattr(ps, "fire_store", FireStore())
    return s


@pytest.fixture
def notifications(monkeypatch):
    sent = []

    def _fake(**kw):
        sent.append(kw)
        return Notification(title=kw.get("title", ""), body=kw.get("body", ""))

    monkeypatch.setattr(ps, "create_notification", _fake)
    return sent


@pytest.fixture
def agent():
    spec = AgentSpec(id="watcher", name="Watcher", type="langchain",
                     entrypoint="agents.definitions.demo:build", owner_workspace="default")
    add_agent(spec)
    return spec


@pytest.fixture
def launched(monkeypatch):
    """Stand in for the task creation + launch a tick does; records the
    prompt and the launch params, hands back a fresh task id."""
    calls = []

    def _fake(job, *, description=None, notify=True, launch_params=None, activity="scheduled_fire"):
        tid = str(uuid4())
        calls.append({"job": job, "description": description, "notify": notify,
                      "launch_params": launch_params, "activity": activity, "task_id": tid})
        return tid

    monkeypatch.setattr(ps, "_fire_agent_task", _fake)
    return calls


def _enable(agent_id="watcher", **extra):
    patch = {"enabled": True, "interval_minutes": 15, "brief": "watch the folder", **extra}
    return svc.save_profile(agent_id, patch)


# -------------------- profile --------------------

def test_defaults_and_normalization():
    p = pp.normalize_profile({"enabled": "yes", "interval_minutes": "30", "notify": ["Telegram", "dashboard"]})
    assert p["enabled"] is True
    assert p["interval_minutes"] == 30
    assert p["notify"] == ["telegram", "dashboard"]
    assert p["quiet_hours"] == {"from": "", "to": ""}
    assert p["job_id"] is None


@pytest.mark.parametrize("minutes,cron", [
    (5, "*/5 * * * *"), (30, "*/30 * * * *"), (60, "0 */1 * * *"),
    (180, "0 */3 * * *"), (1440, "0 0 * * *"),
])
def test_interval_becomes_cron(minutes, cron):
    assert pp.schedule_cron({"interval_minutes": minutes}) == cron


def test_explicit_cron_wins_and_is_validated():
    assert pp.schedule_cron({"cron": "0 9 * * 1-5", "interval_minutes": 5}) == "0 9 * * 1-5"
    with pytest.raises(ValueError, match="Invalid cron"):
        pp.validate_profile({"cron": "not a cron"})


@pytest.mark.parametrize("bad,match", [
    ({"interval_minutes": 7}, "interval_minutes"),
    ({"timezone": "Mars/Olympus"}, "timezone"),
    ({"quiet_hours": {"from": "22:00", "to": ""}}, "both"),
    ({"quiet_hours": {"from": "25:00", "to": "07:00"}}, "HH:MM"),
    ({"quiet_hours": {"from": "07:00", "to": "07:00"}}, "differ"),
    ({"daily_budget_usd": -1}, "daily_budget_usd"),
    ({"max_runs_per_day": -3}, "max_runs_per_day"),
    ({"notify": ["pigeon"]}, "notify"),
    ({"triggers": [{"kind": "carrier"}]}, "triggers"),
])
def test_validation_rejects(bad, match):
    with pytest.raises(ValueError, match=match):
        pp.validate_profile(bad)


def test_quiet_window_crossing_midnight():
    p = pp.normalize_profile({"quiet_hours": {"from": "22:00", "to": "07:00"}, "timezone": "Europe/Berlin"})
    # 23:30 Berlin (21:30 UTC in October): inside, ends 07:00 next morning.
    end = pp.quiet_window_end(p, datetime(2026, 10, 2, 21, 30, tzinfo=UTC))
    assert end == datetime(2026, 10, 3, 5, 0, tzinfo=UTC)
    # 03:00 Berlin: inside, ends the same morning.
    end = pp.quiet_window_end(p, datetime(2026, 10, 3, 1, 0, tzinfo=UTC))
    assert end == datetime(2026, 10, 3, 5, 0, tzinfo=UTC)
    # Noon: outside.
    assert pp.quiet_window_end(p, datetime(2026, 10, 2, 10, 0, tzinfo=UTC)) is None


def test_quiet_window_within_a_day():
    p = pp.normalize_profile({"quiet_hours": {"from": "12:00", "to": "13:00"}})
    assert pp.quiet_window_end(p, datetime(2026, 10, 2, 12, 30, tzinfo=UTC)) == datetime(2026, 10, 2, 13, 0, tzinfo=UTC)
    assert pp.quiet_window_end(p, datetime(2026, 10, 2, 13, 0, tzinfo=UTC)) is None


# -------------------- the job the profile owns --------------------

def test_enabling_creates_one_heartbeat_job(store, agent):
    profile = _enable()
    assert profile["job_id"]
    job = ps.get_job(profile["job_id"])
    assert job.kind == JobKind.heartbeat
    assert job.agent_id == "watcher"
    assert job.cron == "*/15 * * * *"
    assert job.status == JobStatus.scheduled
    assert job.created_by == "system"
    assert job.message == "watch the folder"
    assert get_agent("watcher").proactive["job_id"] == str(job.id)

    # A second save updates the same job rather than adding another.
    again = svc.save_profile("watcher", {"interval_minutes": 30, "brief": "watch harder"})
    assert again["job_id"] == profile["job_id"]
    assert len(ps.list_jobs()) == 1
    job = ps.get_job(profile["job_id"])
    assert job.cron == "*/30 * * * *"
    assert job.message == "watch harder"


def test_disabling_cancels_and_reenabling_revives(store, agent):
    profile = _enable()
    job_id = profile["job_id"]

    off = svc.save_profile("watcher", {"enabled": False})
    assert off["job_id"] == job_id  # kept, so the tick feed survives
    assert ps.get_job(job_id).status == JobStatus.cancelled

    on = svc.save_profile("watcher", {"enabled": True})
    assert on["job_id"] == job_id
    job = ps.get_job(job_id)
    assert job.status == JobStatus.scheduled
    assert job.run_at > datetime.now(UTC) - timedelta(seconds=5)


def test_profile_is_not_seed_owned():
    from common.bootstrap import _SEED_OWNED_FIELDS
    assert "proactive" not in _SEED_OWNED_FIELDS


def test_profile_round_trips_through_the_registry(agent):
    add_agent(AgentSpec(**{**agent.__dict__, "proactive": {"enabled": False, "brief": "b"}}))
    assert get_agent("watcher").to_dict()["proactive"]["brief"] == "b"
    assert "proactive" not in AgentSpec(id="x", name="x", type="langchain", entrypoint="a.b:c").to_dict()


# -------------------- the tick and its gates --------------------

def _due(job_id):
    ps.plan_store.update(job_id, run_at=datetime.now(UTC) - timedelta(seconds=5))
    return ps.get_job(job_id)


def test_a_tick_starts_a_task_with_the_prompt_and_the_schema(store, agent, launched, notifications):
    job = _due(_enable()["job_id"])
    result = ps.fire_job(job)

    assert result["ok"] is True
    assert result["task_id"] == launched[0]["task_id"]
    call = launched[0]
    assert call["notify"] is False
    assert call["activity"] == svc.TICK_ACTIVITY
    assert call["launch_params"] == {"output_schema": pp.TICK_SCHEMA}
    assert "watch the folder" in call["description"]
    assert "first tick" in call["description"]
    assert '"outcome"' in call["description"]
    assert notifications == []  # a tick makes no noise when it starts

    rows = ps.fire_store.list_for_job(job.id)
    assert len(rows) == 1 and rows[0].task_id == call["task_id"] and rows[0].outcome is None
    assert ps.get_job(job.id).created_task_ids == [call["task_id"]]


def _quiet_around_now():
    # A window that started an hour ago and ends in two. A fixed 00:00 to 23:59
    # failed near midnight UTC: after 23:45 the next 15 minute slot already
    # lies past the window's end, so the scheduler (rightly) kept that slot.
    now = datetime.now(UTC)
    return {"from": (now - timedelta(hours=1)).strftime("%H:%M"),
            "to": (now + timedelta(hours=2)).strftime("%H:%M")}


def test_quiet_hours_skip_and_move_the_next_run(store, agent, launched, monkeypatch):
    profile = _enable(quiet_hours=_quiet_around_now(), timezone="UTC")
    job = _due(profile["job_id"])
    result = ps.fire_job(job)

    assert result["ok"] is True
    assert result["skipped"] == "quiet_hours"
    assert launched == []
    row = ps.fire_store.list_for_job(job.id)[0]
    assert row.outcome == "quiet_hours" and row.task_id is None
    after = ps.get_job(job.id)
    expected_end = pp.quiet_window_end(profile, datetime.now(UTC))
    assert after.run_at == expected_end


def test_manual_wake_passes_quiet_hours(store, agent, launched):
    _enable(quiet_hours=_quiet_around_now(), timezone="UTC")
    result = svc.wake("watcher")
    assert result["ok"] is True
    assert result.get("task_id") == launched[0]["task_id"]
    assert ps.fire_store.list_for_job(result["job_id"])[0].trigger == "manual"


def test_busy_when_the_previous_tick_is_still_running(store, agent, launched, monkeypatch):
    job = _due(_enable()["job_id"])
    ps.fire_job(job)
    monkeypatch.setattr(svc, "_task_status", lambda tid: "in_progress")

    result = ps.fire_job(_due(job.id))
    assert result["skipped"] == "busy"
    assert len(launched) == 1
    outcomes = [r.outcome for r in ps.fire_store.list_for_job(job.id)]
    assert outcomes == ["busy", None]


def test_tick_limit_per_day(store, agent, launched, monkeypatch):
    job = _due(_enable(max_runs_per_day=2)["job_id"])
    monkeypatch.setattr(svc, "_task_status", lambda tid: "resolved")
    assert ps.fire_job(job).get("task_id")
    assert ps.fire_job(_due(job.id)).get("task_id")
    third = ps.fire_job(_due(job.id))
    assert third["skipped"] == "rate"
    assert len(launched) == 2


def test_daily_budget_from_the_journal(store, agent, launched, monkeypatch):
    job = _due(_enable(daily_budget_usd=1.0)["job_id"])
    monkeypatch.setattr(svc, "_task_status", lambda tid: "resolved")
    ps.fire_job(job)
    row = ps.fire_store.list_for_job(job.id)[0]
    ps.fire_store.update(row.id, outcome="acted", cost_usd=1.25)

    result = ps.fire_job(_due(job.id))
    assert result["skipped"] == "budget"
    usage = svc.daily_usage(ps.get_job(job.id), svc.get_profile("watcher"))
    assert usage["runs"] == 1 and usage["spent_usd"] == 1.25


def test_missing_agent_pauses_the_pulse(store, launched, notifications):
    job = ps.create_job(kind=JobKind.heartbeat, title="ghost", run_at=datetime.now(UTC) - timedelta(seconds=5),
                        recurrence="cron", cron="*/5 * * * *", agent_id="nobody", created_by="system")
    result = ps.fire_job(job)
    assert result["ok"] is False and result["error_type"] == "agent_missing"
    assert ps.get_job(job.id).status == JobStatus.paused
    assert ps.get_job(job.id).paused_reason == "target_missing"


def test_prompt_carries_the_last_tick(store, agent, launched, monkeypatch):
    job = _due(_enable()["job_id"])
    ps.fire_job(job)
    row = ps.fire_store.list_for_job(job.id)[0]
    ps.fire_store.update(row.id, outcome="quiet", summary="nothing new", next_check="look at row 12")
    monkeypatch.setattr(svc, "_task_status", lambda tid: "resolved")

    ps.fire_job(_due(job.id))
    prompt = launched[1]["description"]
    assert "outcome quiet" in prompt
    assert "nothing new" in prompt
    assert "look at row 12" in prompt


# -------------------- when the run finishes --------------------

@pytest.fixture
def finished(store, agent, launched, monkeypatch):
    """A tick that started; `done(answer, status)` finalizes it as if its run
    had ended with that task result."""
    job = _due(_enable()["job_id"])
    ps.fire_job(job)
    task_id = launched[0]["task_id"]
    state = {"result": None}

    monkeypatch.setattr(svc, "_tick_job_id", lambda tid: str(job.id) if tid == task_id else None)
    monkeypatch.setattr(svc, "_task_cost", lambda tid: 0.03)
    monkeypatch.setattr(svc, "_write_memory_block", lambda *a, **k: None)
    import tasks.service as ts
    monkeypatch.setattr(ts, "get_task_result", lambda tid: state["result"])

    def done(answer, status="completed", task=None):
        state["result"] = json.dumps(answer) if isinstance(answer, dict) else answer
        svc.on_task_run_finished(task or task_id, {"error": "boom" if status != "completed" else None}, status)
        return ps.fire_store.list_for_job(job.id)

    return {"job": job, "task_id": task_id, "done": done}


def test_acted_tick_is_recorded_and_delivered(finished, notifications):
    rows = finished["done"]({"outcome": "acted", "summary": "Drafted two answers", "next_check": "row 14"})
    row = rows[0]
    assert row.outcome == "acted"
    assert row.summary == "Drafted two answers"
    assert row.next_check == "row 14"
    assert row.cost_usd == 0.03
    assert len(notifications) == 1
    assert "Drafted two answers" in notifications[0]["title"]
    assert notifications[0]["source"]["heartbeat"] == "acted"
    assert notifications[0]["channels"] == ["dashboard"]


def test_quiet_tick_makes_no_noise(finished, notifications):
    rows = finished["done"]({"outcome": "quiet", "summary": "nothing new"})
    assert rows[0].outcome == "quiet"
    assert notifications == []


def test_outcome_is_written_once(finished, notifications):
    finished["done"]({"outcome": "acted", "summary": "first"})
    finished["done"]({"outcome": "acted", "summary": "second"})
    rows = ps.fire_store.list_for_job(finished["job"].id)
    assert rows[0].summary == "first"
    assert len(notifications) == 1


def test_prose_answer_reads_as_acted(finished, notifications):
    rows = finished["done"]("I moved the file and told nobody.")
    assert rows[0].outcome == "acted"
    assert rows[0].summary.startswith("I moved the file")


def test_blocked_is_delivered_once_per_reason(store, agent, launched, notifications, monkeypatch):
    job = _due(_enable()["job_id"])
    monkeypatch.setattr(svc, "_task_status", lambda tid: "resolved")
    monkeypatch.setattr(svc, "_task_cost", lambda tid: 0.0)
    monkeypatch.setattr(svc, "_write_memory_block", lambda *a, **k: None)
    import tasks.service as ts
    answer = {"outcome": "blocked", "summary": "need write access to /shared"}
    monkeypatch.setattr(ts, "get_task_result", lambda tid: json.dumps(answer))
    by_task = {}
    monkeypatch.setattr(svc, "_tick_job_id", lambda tid: by_task.get(tid))

    for _ in range(2):
        ps.fire_job(_due(job.id))
        tid = launched[-1]["task_id"]
        by_task[tid] = str(job.id)
        svc.on_task_run_finished(tid, {}, "completed")
    assert len(notifications) == 1  # the same reason twice: said once

    answer["summary"] = "need the supplier's email"
    ps.fire_job(_due(job.id))
    tid = launched[-1]["task_id"]
    by_task[tid] = str(job.id)
    svc.on_task_run_finished(tid, {}, "completed")
    assert len(notifications) == 2


def test_failed_runs_count_towards_auto_pause(store, agent, launched, notifications, monkeypatch):
    job = _due(_enable(auto_pause_after=2)["job_id"])
    monkeypatch.setattr(svc, "_task_status", lambda tid: "resolved")
    monkeypatch.setattr(svc, "_task_cost", lambda tid: 0.0)
    monkeypatch.setattr(svc, "_write_memory_block", lambda *a, **k: None)
    by_task = {}
    monkeypatch.setattr(svc, "_tick_job_id", lambda tid: by_task.get(tid))

    for _ in range(2):
        ps.fire_job(_due(job.id))
        tid = launched[-1]["task_id"]
        by_task[tid] = str(job.id)
        svc.on_task_run_finished(tid, {"error": "model exploded"}, "failed")

    after = ps.get_job(job.id)
    assert after.consecutive_errors == 2
    assert after.status == JobStatus.paused and after.paused_reason == "errors"
    assert ps.fire_store.list_for_job(job.id)[0].outcome == "error"
    assert any("paused" in n["title"] for n in notifications)


def test_a_good_run_resets_the_counter(store, agent, launched, monkeypatch):
    job = _due(_enable()["job_id"])
    ps.plan_store.update(job.id, consecutive_errors=1)
    ps.fire_job(job)
    tid = launched[0]["task_id"]
    monkeypatch.setattr(svc, "_tick_job_id", lambda t: str(job.id) if t == tid else None)
    monkeypatch.setattr(svc, "_task_cost", lambda t: 0.0)
    monkeypatch.setattr(svc, "_write_memory_block", lambda *a, **k: None)
    import tasks.service as ts
    monkeypatch.setattr(ts, "get_task_result", lambda t: json.dumps({"outcome": "quiet", "summary": "ok"}))
    # The firing did not reset it (a heartbeat's counter is fed by its runs).
    assert ps.get_job(job.id).consecutive_errors == 1
    svc.on_task_run_finished(tid, {}, "completed")
    assert ps.get_job(job.id).consecutive_errors == 0


def test_other_tasks_are_ignored(store, agent, monkeypatch):
    monkeypatch.setattr(svc, "_tick_job_id", lambda tid: None)
    svc.on_task_run_finished(str(uuid4()), {}, "completed")  # no raise, nothing written
    assert ps.fire_store.list_all() == []


def test_tick_job_id_reads_the_activity_log(store, agent, monkeypatch):
    import tasks.service as ts
    entries = [{"type": "created"}, {"type": svc.TICK_ACTIVITY, "job_id": "j-1"}]
    monkeypatch.setattr(ts, "get_task_activity_log", lambda tid: entries)
    assert svc._tick_job_id(str(uuid4())) == "j-1"
    monkeypatch.setattr(ts, "get_task_activity_log", lambda tid: [{"type": "scheduled_fire", "job_id": "j-2"}])
    assert svc._tick_job_id(str(uuid4())) is None


# -------------------- routes --------------------

@pytest.fixture
def client(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import agent_proactive as routes

    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_route_get_defaults(client, agent):
    resp = client.get("/api/agents/watcher/proactive")
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"]["enabled"] is False
    assert body["job"] is None
    assert body["ticks"] == []
    assert client.get("/api/agents/nobody/proactive").status_code == 404


def test_route_put_enables_and_lists_the_job(client, agent):
    resp = client.put("/api/agents/watcher/proactive", json={
        "enabled": True, "interval_minutes": 30, "brief": "check mail",
        "quiet_hours": {"from": "22:00", "to": "07:00"}, "timezone": "Europe/Berlin",
        "daily_budget_usd": 2, "notify": ["dashboard", "telegram"],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["profile"]["enabled"] is True
    assert body["profile"]["job_id"]
    assert body["job"]["kind"] == "heartbeat"
    assert body["job"]["cron"] == "*/30 * * * *"
    assert body["job"]["timezone"] == "Europe/Berlin"
    assert body["schedule"] == "every 30 minutes"
    assert body["usage"]["daily_budget_usd"] == 2.0


def test_route_put_rejects_bad_values(client, agent):
    resp = client.put("/api/agents/watcher/proactive", json={"interval_minutes": 7})
    assert resp.status_code == 400
    assert "interval_minutes" in resp.json()["detail"]
    resp = client.put("/api/agents/watcher/proactive", json={"job_id": "sneaky", "enabled": False})
    assert resp.status_code == 200 and resp.json()["profile"]["job_id"] is None


def test_route_pause_resume_wake(client, agent, launched):
    assert client.post("/api/agents/watcher/proactive/pause").status_code == 400  # no pulse yet
    client.put("/api/agents/watcher/proactive", json={"enabled": True})
    assert client.post("/api/agents/watcher/proactive/pause").json()["job"]["status"] == "paused"
    assert client.post("/api/agents/watcher/proactive/resume").json()["job"]["status"] == "scheduled"
    woke = client.post("/api/agents/watcher/proactive/wake")
    assert woke.status_code == 200, woke.text
    assert woke.json()["result"]["task_id"] == launched[0]["task_id"]
    assert woke.json()["ticks"][0]["trigger"] == "manual"


def test_fire_record_carries_the_tick_fields():
    row = FireRecord(job_id=uuid4(), outcome="acted", summary="s", next_check="n", cost_usd=0.1)
    data = ps.fire_to_dict(row)
    assert {"outcome", "summary", "next_check", "cost_usd"} <= set(data)
