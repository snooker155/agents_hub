"""Waking a proactive agent besides the clock (proactive/events.py, docs/proactive.md
"Triggers"), the capability guard's view of a profile, the tool policy a tick
runs under, the journal compaction and the Dashboard summary.

Same fixtures as test_proactive.py: plans.service's stores are throwaway,
the task launch is stubbed, the registry is reset per test.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from agents.registry import AgentSpec, add_agent, get_agent, replace_all_raw
from plans import service as ps
from plans.models import FireRecord, JobStatus, Notification
from plans.storage import FireStore, PlanStore
from proactive import events as ev
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
def launched(monkeypatch):
    calls = []

    def _fake(job, *, description=None, notify=True, launch_params=None, activity="scheduled_fire"):
        tid = str(uuid4())
        calls.append({"job": job, "description": description, "launch_params": launch_params, "task_id": tid})
        return tid

    monkeypatch.setattr(ps, "_fire_agent_task", _fake)
    return calls


def _agent(agent_id="watcher", tools=(), workspace="default", **extra):
    spec = AgentSpec(id=agent_id, name=agent_id.title(), type="langchain",
                     entrypoint="agents.definitions.demo:build", owner_workspace=workspace,
                     tools=list(tools), **extra)
    add_agent(spec)
    return spec


def _enable(agent_id="watcher", **extra):
    patch = {"enabled": True, "interval_minutes": 60, "brief": "watch", **extra}
    return svc.save_profile(agent_id, patch)


def _job(profile):
    return ps.get_job(profile["job_id"])


# -------------------- wake_agent: events and batching --------------------

def test_wake_pulls_the_next_tick_into_the_window_and_keeps_the_event(store):
    _agent()
    profile = _enable(cron="0 0 1 1 *")  # once a year: the next slot is far away
    before = _job(profile).run_at
    assert before > datetime.now(UTC) + timedelta(days=1)

    result = svc.wake_agent("watcher", ev.make_event("agent", "look now"), window_seconds=30)

    assert result["ok"] is True and result["pending"] == 1
    job = _job(profile)
    assert job.run_at <= datetime.now(UTC) + timedelta(seconds=31)
    assert job.run_at < before
    assert job.pending_events[0]["kind"] == "agent"
    assert job.pending_events[0]["summary"] == "look now"


def test_several_wakes_within_the_window_batch_into_one_tick(store, launched):
    _agent()
    profile = _enable()
    svc.wake_agent("watcher", ev.make_event("file", "one"), window_seconds=30)
    first_run_at = _job(profile).run_at
    svc.wake_agent("watcher", ev.make_event("file", "two"), window_seconds=30)
    svc.wake_agent("watcher", ev.make_event("file", "three"), window_seconds=30)
    job = _job(profile)
    assert job.run_at == first_run_at  # not pushed later by the second and third wake
    assert len(job.pending_events) == 3

    ps.plan_store.update(job.id, run_at=datetime.now(UTC) - timedelta(seconds=1))
    result = ps.fire_job(ps.get_job(job.id))
    assert result["task_id"] == launched[0]["task_id"]
    prompt = launched[0]["description"]
    assert "What woke you" in prompt
    assert "- file: one" in prompt and "- file: three" in prompt
    assert "schedule (" not in prompt.split("What woke you")[1][:50]
    assert _job(profile).pending_events == []  # handed to the tick


def test_pending_events_are_capped(store):
    _agent()
    profile = _enable()
    for i in range(svc.MAX_PENDING_EVENTS + 5):
        svc.wake_agent("watcher", ev.make_event("file", f"e{i}"), window_seconds=30)
    events = _job(profile).pending_events
    assert len(events) == svc.MAX_PENDING_EVENTS
    assert events[0]["summary"] == "e5" and events[-1]["summary"] == f"e{svc.MAX_PENDING_EVENTS + 4}"


def test_wake_of_a_paused_pulse_keeps_the_event_without_moving_it(store):
    _agent()
    profile = _enable()
    svc.pause("watcher")
    before = _job(profile).run_at
    result = svc.wake_agent("watcher", ev.make_event("agent", "x"), window_seconds=30)
    assert result["ok"] is True and result["paused"] is True
    job = _job(profile)
    assert job.run_at == before and len(job.pending_events) == 1


def test_wake_of_a_disabled_pulse_is_refused_quietly(store):
    _agent()
    assert svc.wake_agent("watcher", ev.make_event("agent", "x"))["reason"] == "disabled"
    assert svc.wake_agent("nobody", ev.make_event("agent", "x"))["reason"] == "unknown_agent"


def test_quiet_hours_keep_the_events_for_the_window_end(store, launched):
    _agent()
    profile = _enable(quiet_hours={"from": "00:00", "to": "23:59"}, timezone="UTC")
    svc.wake_agent("watcher", ev.make_event("file", "one"), window_seconds=0)
    result = ps.fire_job(_job(profile))
    assert result["skipped"] == "quiet_hours"
    assert launched == []
    assert len(_job(profile).pending_events) == 1


# -------------------- sources --------------------

def test_file_change_wakes_matching_profiles_in_the_workspace(store):
    _agent("md_watcher", workspace="default")
    _agent("csv_watcher", workspace="default")
    _agent("elsewhere", workspace="other")
    _enable("md_watcher", triggers=[{"kind": "file", "pattern": "*.md"}])
    _enable("csv_watcher", triggers=[{"kind": "file", "pattern": "*.csv", "source": "upload"}])
    _enable("elsewhere", triggers=[{"kind": "file"}])

    record = {"file_id": "f1", "name": "notes.md", "source": "upload", "meta": {"path": "docs/notes.md"}}
    assert ev.file_changed("default", record, "updated") == ["md_watcher"]
    assert ev.file_changed("default", record, "unchanged") == []
    csv = {"file_id": "f2", "name": "data.csv", "source": "agent", "meta": {}}
    assert ev.file_changed("default", csv, "added") == []  # wrong source
    csv["source"] = "upload"
    assert ev.file_changed("default", csv, "added") == ["csv_watcher"]
    event = _job(svc.get_profile("md_watcher")).pending_events[0]
    assert event["kind"] == "file" and event["data"]["path"] == "docs/notes.md"


def test_task_status_change_wakes_but_not_for_the_agents_own_ticks(store, launched):
    from tasks.models import CreatedBy, TaskStatus
    from tasks.service import create_task, update_task

    _agent()
    profile = _enable(triggers=[{"kind": "task", "statuses": ["blocked"]}])
    task = create_task("Needs eyes", "x", created_by=CreatedBy.user, workspace="default")

    update_task(task.id, status=TaskStatus.in_progress)
    assert _job(profile).pending_events == []  # not a listed status
    update_task(task.id, status=TaskStatus.blocked, blocked_reason="waiting on the supplier")
    events = _job(profile).pending_events
    assert len(events) == 1 and events[0]["data"]["status"] == "blocked"
    assert "waiting on the supplier" in json.dumps(events[0])

    # The agent's own tick task moving to blocked wakes nobody.
    ps.plan_store.update(_job(profile).id, pending_events=[],
                         created_task_ids=[str(task.id)])
    update_task(task.id, status=TaskStatus.todo)
    update_task(task.id, status=TaskStatus.blocked, blocked_reason="again")
    assert _job(profile).pending_events == []


def test_eval_failure_wakes_only_on_failures(store):
    _agent()
    profile = _enable(triggers=[{"kind": "eval", "eval_set_id": "set-a"}])

    class Run:
        eval_run_id = "evrun-1"
        eval_set_id = "set-a"
        workspace = "default"
        status = "completed"
        summary = {"gpt": {"passed": 3, "total": 4}}

    assert ev.eval_finished(Run()) == ["watcher"]
    assert "3/4 passed" in _job(profile).pending_events[0]["summary"]
    Run.summary = {"gpt": {"passed": 4, "total": 4}}
    assert ev.eval_finished(Run()) == []
    Run.summary = {"gpt": {"passed": 0, "total": 4}}
    Run.eval_set_id = "set-b"
    assert ev.eval_finished(Run()) == []


def test_unanswered_telegram_message_wakes_listeners(store):
    _agent("ws_agent", workspace="default")
    _agent("other_ws", workspace="other")
    _enable("ws_agent", triggers=[{"kind": "telegram"}])
    _enable("other_ws", triggers=[{"kind": "telegram"}])
    assert ev.telegram_unanswered(42, "default", "hello?") == ["ws_agent"]
    assert set(ev.telegram_unanswered(42, None, "anyone?")) == {"ws_agent", "other_ws"}


def test_agent_wake_respects_the_from_list(store):
    _agent("target")
    _enable("target", triggers=[{"kind": "agent", "from": ["friend"]}])
    with pytest.raises(PermissionError):
        ev.agent_wake("stranger", "target", "psst")
    assert ev.agent_wake("friend", "target", "psst")["ok"] is True
    with pytest.raises(LookupError):
        ev.agent_wake("friend", "ghost", "psst")


def test_wake_agent_tool(store, monkeypatch):
    from common.agent_context import current_agent_id
    from tools.schedule_management import wake_agent as tool

    _agent("target")
    _enable("target", triggers=[{"kind": "agent"}])
    token = current_agent_id.set("caller")
    try:
        body = json.loads(tool.invoke({"agent_id": "target", "message": "the report landed"}))
        assert body["ok"] is True and body["pending_events"] == 1
        body = json.loads(tool.invoke({"agent_id": "nobody", "message": "x"}))
        assert body["ok"] is False
    finally:
        current_agent_id.reset(token)
    assert _job(svc.get_profile("target")).pending_events[0]["data"]["from_agent"] == "caller"


def test_wake_agent_tool_is_in_the_catalog_and_grants_nothing():
    from tools.capabilities import grants_of
    from tools.registry import TOOL_CATALOG
    assert any(t.id == "wake_agent" for t in TOOL_CATALOG)
    assert grants_of("wake_agent") == frozenset()


# -------------------- the signed webhook --------------------

@pytest.fixture
def notify_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import notify as notify_routes

    app = FastAPI()
    app.include_router(notify_routes.router)
    return TestClient(app)


def _signed(payload, secret):
    from notify.inbound import sign_payload
    body = json.dumps(payload).encode("utf-8")
    return body, {
        "Content-Type": "application/json",
        "X-AgentsHub-Signature": sign_payload(secret, body),
        "X-AgentsHub-Timestamp": str(int(time.time())),
        "X-AgentsHub-Delivery": str(uuid4()),
    }


def test_webhook_wake(store, notify_client):
    from notify import store as notify_store
    from workspace import create_workspace_folder

    ws = "pulse-webhook-ws"
    create_workspace_folder(ws)
    notify_store.set_inbound_secret(ws, "s3cret")
    _agent("hooked", workspace=ws)
    _agent("deaf", workspace=ws)
    profile = _enable("hooked", triggers=[{"kind": "webhook", "name": "crm"}])
    _enable("deaf")

    body, headers = _signed({"workspace": ws, "name": "crm", "summary": "lead #7 replied", "data": {"lead": 7}}, "s3cret")
    resp = notify_client.post("/api/webhooks/agents/hooked/wake", content=body, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["pending"] == 1
    event = _job(profile).pending_events[0]
    assert event["kind"] == "webhook" and event["data"]["lead"] == 7

    body, headers = _signed({"workspace": ws, "name": "other", "summary": "x"}, "s3cret")
    assert notify_client.post("/api/webhooks/agents/hooked/wake", content=body, headers=headers).status_code == 403
    body, headers = _signed({"workspace": ws, "summary": "x"}, "s3cret")
    assert notify_client.post("/api/webhooks/agents/deaf/wake", content=body, headers=headers).status_code == 403
    body, headers = _signed({"workspace": ws, "summary": "x"}, "s3cret")  # a fresh delivery id
    assert notify_client.post("/api/webhooks/agents/ghost/wake", content=body, headers=headers).status_code == 404
    body, headers = _signed({"workspace": ws, "summary": "x"}, "wrong")
    assert notify_client.post("/api/webhooks/agents/hooked/wake", content=body, headers=headers).status_code == 401
    body, headers = _signed({"workspace": "no-secret-ws", "summary": "x"}, "s3cret")
    assert notify_client.post("/api/webhooks/agents/hooked/wake", content=body, headers=headers).status_code == 401


# -------------------- the guard and the tick's tool policy --------------------

def test_untrusted_trigger_closes_the_trifecta(store, monkeypatch):
    from agents import capability_guard
    monkeypatch.setattr(capability_guard, "guard_mode", lambda: "block")
    # read_file reads private data; a webhook brings untrusted text in; a
    # Telegram delivery carries the summary out: the trifecta, by profile.
    _agent(tools=["read_file"])
    with pytest.raises(ValueError, match="capability guard"):
        svc.save_profile("watcher", {"enabled": True, "triggers": [{"kind": "webhook"}],
                                     "notify": ["dashboard", "telegram"]})
    # The inbox alone is not an outbound channel.
    profile = svc.save_profile("watcher", {"enabled": True, "triggers": [{"kind": "webhook"}],
                                           "notify": ["dashboard"]})
    assert profile["enabled"] is True


def test_override_and_warn_mode_let_the_profile_through(store, monkeypatch):
    from agents import capability_guard
    monkeypatch.setattr(capability_guard, "guard_mode", lambda: "warn")
    _agent(tools=["read_file"])
    profile = svc.save_profile("watcher", {"enabled": True, "triggers": [{"kind": "telegram"}],
                                           "notify": ["telegram"]})
    assert profile["enabled"] is True


def test_untrusted_profile_puts_outbound_tools_on_ask(store, launched):
    # Outbound tools without a private read: the guard lets the profile
    # through (no trifecta), and the tick makes those tools ask first.
    _agent(tools=["calculator", "notify_user", "git_publish"])
    profile = _enable(triggers=[{"kind": "file"}])
    ps.plan_store.update(_job(profile).id, run_at=datetime.now(UTC) - timedelta(seconds=1))
    ps.fire_job(_job(profile))
    params = launched[0]["launch_params"]
    assert params["tool_policy"] == {"notify_user": "always_ask", "git_publish": "always_ask"}
    assert "output_schema" in params


def test_trusted_profile_leaves_the_tool_policy_alone(store, launched):
    _agent(tools=["read_file", "notify_user"])
    profile = _enable(triggers=[{"kind": "task"}])
    ps.plan_store.update(_job(profile).id, run_at=datetime.now(UTC) - timedelta(seconds=1))
    ps.fire_job(_job(profile))
    assert "tool_policy" not in launched[0]["launch_params"]


# -------------------- compaction and summary --------------------

def _row(job_id, days_ago, outcome, **kw):
    at = datetime.now(UTC) - timedelta(days=days_ago)
    return ps.fire_store.add(FireRecord(job_id=job_id, at=at, slot=at, outcome=outcome,
                                        task_id=kw.pop("task_id", "t"), **kw))


def test_compaction_folds_old_quiet_ticks_and_keeps_acted_ones(store):
    _agent()
    job = _job(_enable())
    for _ in range(5):
        _row(job.id, 10, "quiet", cost_usd=0.01)
    _row(job.id, 10, "acted", summary="did a thing")
    _row(job.id, 10, "budget", task_id=None)
    _row(job.id, 10, "budget", task_id=None)
    for _ in range(3):
        _row(job.id, 1, "quiet")  # recent: untouched

    removed = svc.compact_journal(7)
    assert removed == 4 + 1
    rows = ps.fire_store.list_for_job(job.id, limit=100)
    old_quiet = [r for r in rows if r.outcome == "quiet" and r.count > 1]
    assert len(old_quiet) == 1 and old_quiet[0].count == 5 and old_quiet[0].cost_usd == 0.05
    assert old_quiet[0].task_id is None
    assert len([r for r in rows if r.outcome == "acted"]) == 1
    assert [r.count for r in rows if r.outcome == "budget"] == [2]
    assert len([r for r in rows if r.outcome == "quiet" and r.count == 1]) == 3
    assert svc.compact_journal(0) == 0


def test_summary_counts_the_last_day(store):
    _agent("a")
    _agent("b", workspace="other")
    job_a = _job(_enable("a"))
    job_b = _job(_enable("b"))
    _row(job_a.id, 0.1, "acted")
    _row(job_a.id, 0.2, "quiet")
    _row(job_a.id, 0.3, "quiet", count=4)
    _row(job_a.id, 0.4, "budget", task_id=None)
    _row(job_a.id, 2, "acted")  # too old
    _row(job_a.id, 0.05, None)  # still running
    _row(job_b.id, 0.1, "blocked")
    svc.pause("b")

    body = svc.summary()
    assert body["totals"] == {"agents": 2, "acted": 1, "quiet": 5, "blocked": 1, "error": 0,
                              "skipped": 1, "budget": 1, "running": 1, "paused": 1}
    by_id = {a["agent_id"]: a for a in body["agents"]}
    assert by_id["a"]["status"] == "scheduled" and by_id["a"]["next_run_at"]
    assert by_id["b"]["status"] == "paused" and by_id["b"]["next_run_at"] is None
    only_other = svc.summary("other")
    assert [a["agent_id"] for a in only_other["agents"]] == ["b"]


def test_summary_route(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import agent_proactive as routes

    _agent()
    _enable()
    client = TestClient(FastAPI().__class__())
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    resp = client.get("/api/proactive/summary", params={"workspace": "default"})
    assert resp.status_code == 200
    assert resp.json()["totals"]["agents"] == 1
    assert resp.json()["agents"][0]["agent_id"] == "watcher"


def test_maintenance_runs_the_compaction(store, monkeypatch):
    from common import maintenance
    monkeypatch.setenv("AGENTS_HUB_HEARTBEAT_COMPACT_DAYS", "3")
    _agent()
    job = _job(_enable())
    _row(job.id, 5, "quiet")
    _row(job.id, 5, "quiet")
    summary = maintenance.run_maintenance(force=True)
    assert summary["compacted_ticks"] == 1
    assert get_agent("watcher") is not None
    assert ps.get_job(job.id).status == JobStatus.scheduled
