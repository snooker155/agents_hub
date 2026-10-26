"""The Planner owns schedules and deployments; the main agent does not.

Covers the seed (which agent holds which tools), the one-time migration that
moves the tools on an existing install, the capability model knowing every
new tool, and the new tools themselves: flow and consolidation jobs, a
deployment's fields on schedule_task and update_scheduled, the journal view,
pause and resume, and run-now going through the hub.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from plans import service as ps
from plans.storage import PlanStore

ROOT = Path(__file__).resolve().parents[1]

MOVED = ("schedule_notification", "schedule_task", "list_scheduled",
         "update_scheduled", "cancel_scheduled")
NEW = ("schedule_flow", "schedule_loop", "schedule_memory_consolidation", "get_scheduled",
       "pause_scheduled", "resume_scheduled", "run_scheduled_now")


@pytest.fixture(autouse=True)
def isolated_plan_store(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "plan_store", PlanStore(path=tmp_path / "plans.json"))


def _seed(agent_id: str) -> dict:
    data = json.loads((ROOT / "bootstrap" / "agents.json").read_text(encoding="utf-8"))
    return next(a for a in data["agents"] if a["id"] == agent_id)


def _call(tool, **args) -> dict:
    return json.loads(tool.invoke(args))


# ── who holds the tools ──────────────────────────────────────────────────────

def test_the_main_agent_seed_holds_no_scheduling_tool_but_keeps_notify_user():
    tools = _seed("main-agent")["tools"]
    assert not set(MOVED) & set(tools)
    assert "notify_user" in tools
    assert "@planner" in _seed("main-agent")["delegates"]


def test_the_planner_seed_holds_every_scheduling_and_deployment_tool():
    planner = _seed("planner")
    assert set(MOVED) | set(NEW) <= set(planner["tools"])
    assert planner["system"] is True


def test_the_capability_model_knows_the_new_tools_and_the_planner_set_is_allowed():
    from tools.capabilities import (
        CAN_EXFILTRATE, READS_PRIVATE, check_combination, grants_of, is_recognised_tool_id,
    )
    for tool_id in NEW:
        assert is_recognised_tool_id(tool_id), tool_id
    assert READS_PRIVATE in grants_of("get_scheduled")
    assert CAN_EXFILTRATE in grants_of("run_scheduled_now")
    violation = check_combination(_seed("planner")["tools"])
    assert violation is None or not violation.blocking


def test_the_planner_tools_are_in_the_catalog():
    from tools.registry import get_tool_by_id
    for tool_id in NEW:
        spec = get_tool_by_id(tool_id)
        assert spec is not None and spec.category == "schedule_management", tool_id


# ── the migration ────────────────────────────────────────────────────────────

def _migration():
    import importlib.util
    path = ROOT / "common" / "migrations" / "0045_planner_owns_schedules.py"
    spec = importlib.util.spec_from_file_location("m0045", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _db(records: dict) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE documents (store TEXT, key TEXT, seq INTEGER, doc TEXT, "
                 "created_at TEXT, updated_at TEXT, PRIMARY KEY (store, key))")
    for i, (key, doc) in enumerate(records.items()):
        conn.execute("INSERT INTO documents VALUES ('agents', ?, ?, ?, NULL, NULL)",
                     (key, i, json.dumps(doc)))
    return conn


def _doc(conn, key):
    row = conn.execute("SELECT doc FROM documents WHERE store='agents' AND key=?", (key,)).fetchone()
    return json.loads(row[0])


def test_the_migration_moves_the_tools_even_off_an_edited_main_agent():
    conn = _db({
        "main-agent": {"id": "main-agent", "user_modified": True,
                       "tools": ["read_file", "schedule_task", "notify_user", "list_scheduled"],
                       "tool_policy": {"run_shell": "always_ask", "schedule_task": "always_ask"}},
        "planner": {"id": "planner", "tools": ["list_tasks", "schedule_task"]},
    })
    _migration().upgrade(conn, "sqlite")
    main = _doc(conn, "main-agent")
    assert main["tools"] == ["read_file", "notify_user"]
    assert main["tool_policy"] == {"run_shell": "always_ask"}
    assert main["user_modified"] is True
    planner = _doc(conn, "planner")["tools"]
    assert planner[:2] == ["list_tasks", "schedule_task"]
    assert set(MOVED) | set(NEW) <= set(planner) and len(planner) == len(set(planner))


def test_the_migration_is_a_no_op_without_a_registry():
    conn = sqlite3.connect(":memory:")
    _migration().upgrade(conn, "sqlite")
    conn2 = _db({})
    _migration().upgrade(conn2, "sqlite")


# ── the tools ────────────────────────────────────────────────────────────────

def test_schedule_flow_refuses_an_unknown_flow():
    from tools.schedule_management import schedule_flow
    out = _call(schedule_flow, title="nightly", flow_id="no-such-flow", delay_minutes=5)
    assert out["ok"] is False and out["code"] == "not_found"


def test_schedule_flow_creates_a_flow_deployment(monkeypatch):
    import tools.schedule_management as sm
    monkeypatch.setattr(sm, "_flow_unavailable", lambda flow_id: False)
    out = _call(sm.schedule_flow, title="nightly review", flow_id="f1", delay_minutes=5,
                recurrence="cron", cron="0 2 * * *", timezone="Europe/Berlin",
                seed={"depth": 2}, max_concurrent=1, auto_pause_after=2)
    assert out["ok"] is True
    job = out["job"]
    assert (job["kind"], job["flow_id"], job["seed"], job["auto_pause_after"]) == ("flow", "f1", {"depth": 2}, 2)
    assert job["created_by"] == "agent"


def test_schedule_task_carries_deployment_fields_and_validates_them():
    from tools.schedule_management import schedule_task
    out = _call(schedule_task, title="report", delay_minutes=5, auto_pause_after=0)
    assert out["ok"] is True and out["job"]["auto_pause_after"] == 0
    # A version pin needs the agent it pins.
    bad = _call(schedule_task, title="report", delay_minutes=5, agent_version=2)
    assert bad["ok"] is False


def test_schedule_memory_consolidation_refuses_an_unknown_pool():
    from tools.schedule_management import schedule_memory_consolidation
    out = _call(schedule_memory_consolidation, title="fold", pool_id="no-such-pool", delay_minutes=5)
    assert out["ok"] is False


def test_pause_resume_update_and_the_journal_view():
    from tools.schedule_management import (
        get_scheduled, pause_scheduled, resume_scheduled, schedule_notification, update_scheduled,
    )
    job = _call(schedule_notification, title="check milk", delay_minutes=30, recurrence="daily")["job"]
    paused = _call(pause_scheduled, id=job["id"])
    assert paused["ok"] and paused["job"]["status"] == "paused"
    again = _call(pause_scheduled, id=job["id"])
    assert again["ok"] is False and again["code"] == "invalid_state"
    resumed = _call(resume_scheduled, id=job["id"])
    assert resumed["ok"] and resumed["job"]["status"] == "scheduled"
    updated = _call(update_scheduled, id=job["id"], auto_pause_after=5)
    assert updated["ok"] and updated["job"]["auto_pause_after"] == 5
    shown = _call(get_scheduled, id=job["id"])
    assert shown["ok"] and shown["job"]["id"] == job["id"] and shown["fires"] == []
    missing = _call(get_scheduled, id="00000000-0000-0000-0000-000000000000")
    assert missing["ok"] is False and missing["code"] == "not_found"


def test_run_scheduled_now_asks_the_hub_to_fire_the_job(monkeypatch):
    from tools.schedule_management import run_scheduled_now, schedule_notification
    job = _call(schedule_notification, title="ping", delay_minutes=30)["job"]
    seen = {}

    class _Resp:
        ok = True
        text = ""

        def json(self):
            return {"result": {"fired": True}, "job": job}

    def fake_post(url, headers=None, timeout=None):
        seen["url"] = url
        return _Resp()

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    out = _call(run_scheduled_now, id=job["id"])
    assert out["ok"] is True and out["result"] == {"fired": True}
    assert seen["url"].endswith(f"/api/plan/jobs/{job['id']}/run-now")


def test_run_scheduled_now_reports_a_hub_that_refused(monkeypatch):
    from tools.schedule_management import run_scheduled_now, schedule_notification
    job = _call(schedule_notification, title="ping", delay_minutes=30)["job"]

    class _Resp:
        ok = False
        text = "busy"

        def json(self):
            return {"detail": "Job is currently being fired elsewhere"}

    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())
    out = _call(run_scheduled_now, id=job["id"])
    assert out["ok"] is False and "fired elsewhere" in out["error"]


# ── loops ────────────────────────────────────────────────────────────────────
# A loop job used to be creatable only by the (removed) system workspace: the
# create path had no loop_id, so a loop picked on the Deployments page was
# saved without it and every firing failed.

def _loop(workspace=None):
    from loops import store
    from loops.models import Loop
    return store.save_loop(Loop(name="nightly polish", flow_id="f", exit_criterion="c", workspace=workspace))


def test_create_job_keeps_the_loop_and_validates_it():
    from datetime import datetime, timedelta, timezone
    from plans.models import JobKind
    when = datetime.now(timezone.utc) + timedelta(hours=1)
    loop = _loop()
    job = ps.create_job(kind=JobKind.loop, title="polish", run_at=when, loop_id=loop.loop_id)
    assert ps.get_job(job.id).loop_id == loop.loop_id
    with pytest.raises(ValueError, match="requires loop_id"):
        ps.create_job(kind=JobKind.loop, title="polish", run_at=when)
    with pytest.raises(ValueError, match="does not exist"):
        ps.create_job(kind=JobKind.loop, title="polish", run_at=when, loop_id="no-such-loop")
    with pytest.raises(ValueError, match="loop jobs only"):
        ps.create_job(kind=JobKind.notification, title="x", run_at=when, loop_id=loop.loop_id)


def test_the_route_passes_the_loop_through():
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(ROOT / "dashboard" / "backend"))
    from routes import plan as plan_routes
    app = FastAPI()
    app.include_router(plan_routes.router)
    client = TestClient(app)
    loop = _loop()
    resp = client.post("/api/plan/jobs", json={
        "kind": "loop", "title": "polish", "delay_minutes": 5, "loop_id": loop.loop_id,
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["loop_id"] == loop.loop_id
    missing = client.post("/api/plan/jobs", json={"kind": "loop", "title": "polish", "delay_minutes": 5})
    assert missing.status_code == 400


def test_schedule_loop_creates_a_loop_deployment_and_refuses_an_unknown_loop():
    from tools.schedule_management import schedule_loop
    loop = _loop()
    out = _call(schedule_loop, title="polish", loop_id=loop.loop_id, delay_minutes=5,
                recurrence="daily", auto_pause_after=1)
    assert out["ok"] is True
    assert (out["job"]["kind"], out["job"]["loop_id"], out["job"]["auto_pause_after"]) == ("loop", loop.loop_id, 1)
    bad = _call(schedule_loop, title="polish", loop_id="no-such-loop", delay_minutes=5)
    assert bad["ok"] is False and "does not exist" in bad["error"]
