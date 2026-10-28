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
                recurrence="daily", auto_pause_after=1, user_approved=True)
    assert out["ok"] is True
    assert (out["job"]["kind"], out["job"]["loop_id"], out["job"]["auto_pause_after"]) == ("loop", loop.loop_id, 1)
    bad = _call(schedule_loop, title="polish", loop_id="no-such-loop", delay_minutes=5, user_approved=True)
    assert bad["ok"] is False and bad["code"] == "not_found" and "list_loops_tool" in bad["error"]


def test_schedule_loop_asks_first_and_names_the_real_loop():
    """The planner scheduled an existing loop under the name of one that did
    not exist, and asked nothing (found live). Without user_approved it now
    creates nothing and hands back the loop's own name to show the person."""
    from tools.schedule_management import schedule_loop
    loop = _loop()
    out = _call(schedule_loop, title="Доработать анонс запуска", loop_id=loop.loop_id,
                cron="0 10 * * 1", timezone="Europe/Berlin")
    assert out["ok"] is False and out["code"] == "approval_required"
    assert out["loop_name"] == "nightly polish"
    assert out["requested_title"] == "Доработать анонс запуска"
    assert out["recurrence"] == "cron" and out["first_run_at"]
    assert ps.list_jobs() == []


# ── cron needs no start time ─────────────────────────────────────────────────

def test_a_cron_task_needs_no_run_at_and_starts_on_the_next_cron_time():
    from datetime import datetime
    from tools.schedule_management import schedule_task
    out = _call(schedule_task, title="weekday report", recurrence="cron",
                cron="0 9 * * 1-5", timezone="Europe/Berlin")
    job = out["job"]
    assert job["recurrence"] == "cron"
    first = datetime.fromisoformat(job["run_at"])
    assert first == datetime.fromisoformat(ps.upcoming_runs("0 9 * * 1-5", "Europe/Berlin", count=1)[0])


def test_a_bare_cron_expression_means_a_cron_schedule():
    from tools.schedule_management import schedule_notification
    job = _call(schedule_notification, title="stand-up", cron="30 9 * * *")["job"]
    assert job["recurrence"] == "cron" and job["cron"] == "30 9 * * *"


def test_a_cron_run_at_off_the_grid_moves_to_the_next_cron_time():
    from datetime import datetime
    from tools.schedule_management import schedule_task
    job = _call(schedule_task, title="nightly", recurrence="cron", cron="0 3 * * *",
                run_at="2030-01-01T10:00:00+00:00")["job"]
    assert datetime.fromisoformat(job["run_at"]) == datetime.fromisoformat("2030-01-02T03:00:00+00:00")


def test_a_cron_expression_wins_over_weekly():
    """The Planner sent recurrence='weekly' with cron='0 7 * * 1' and was refused."""
    from tools.schedule_management import schedule_task
    job = _call(schedule_task, title="weekly fold", recurrence="weekly",
                cron="0 7 * * 1", timezone="Europe/Berlin")["job"]
    assert job["recurrence"] == "cron" and job["cron"] == "0 7 * * 1"


def test_weekly_without_a_time_starts_a_week_from_now():
    from datetime import datetime, timedelta, timezone
    from tools.schedule_management import schedule_task
    before = datetime.now(timezone.utc)
    job = _call(schedule_task, title="weekly", recurrence="weekly", run_at=None,
                delay_minutes=None, cron=None)["job"]
    first = datetime.fromisoformat(job["run_at"])
    assert job["recurrence"] == "weekly"
    assert timedelta(weeks=1) <= first - before < timedelta(weeks=1, minutes=1)


def test_a_one_off_without_a_time_and_cron_without_an_expression_are_refused():
    from pydantic import ValidationError
    from tools.schedule_management import schedule_task
    with pytest.raises(ValidationError, match="one-off job"):
        schedule_task.invoke({"title": "x"})
    with pytest.raises(ValidationError, match="requires a cron expression"):
        schedule_task.invoke({"title": "x", "recurrence": "cron"})


def test_switching_a_job_to_cron_moves_it_onto_the_cron_grid():
    from datetime import datetime
    from tools.schedule_management import schedule_notification, update_scheduled
    job = _call(schedule_notification, title="ping", delay_minutes=7)["job"]
    updated = _call(update_scheduled, id=job["id"], cron="0 12 * * *")["job"]
    assert updated["recurrence"] == "cron"
    assert datetime.fromisoformat(updated["run_at"]) == datetime.fromisoformat(
        ps.upcoming_runs("0 12 * * *", None, count=1)[0])


# ── a memory pool by name ────────────────────────────────────────────────────

def _pools(monkeypatch, *pools):
    from types import SimpleNamespace
    from memory.store import MemoryStore
    items = [SimpleNamespace(id=i, name=n, workspace=w, kind=k) for i, n, w, k in pools]
    monkeypatch.setattr(MemoryStore, "load", lambda self, *a, **k: items)


def test_a_consolidation_finds_its_pool_by_name_in_the_workspace(monkeypatch):
    """The Planner passed pool_id='guests', the name the person said."""
    from datetime import datetime, timedelta, timezone
    from plans.models import JobKind
    _pools(monkeypatch, ("p-zerno", "guests", "zerno", "shared"), ("p-dev", "guests", "dev", "shared"))
    when = datetime.now(timezone.utc) + timedelta(hours=1)
    job = ps.create_job(kind=JobKind.memory_consolidate, title="fold", run_at=when,
                        workspace="zerno", consolidate_pool_id="Guests")
    assert job.consolidate_pool_id == "p-zerno"
    with pytest.raises(ValueError, match="its pools: 'guests' \\(p-zerno\\)"):
        ps.create_job(kind=JobKind.memory_consolidate, title="fold", run_at=when,
                      workspace="zerno", consolidate_pool_id="visitors")


def test_two_pools_with_one_name_ask_for_an_id(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from plans.models import JobKind
    _pools(monkeypatch, ("a", "guests", "zerno", "shared"), ("b", "guests", "zerno", "shared"))
    when = datetime.now(timezone.utc) + timedelta(hours=1)
    with pytest.raises(ValueError, match="several memory pools"):
        ps.create_job(kind=JobKind.memory_consolidate, title="fold", run_at=when,
                      workspace="zerno", consolidate_pool_id="guests")


# ── the Task Manager keeps the tracker ───────────────────────────────────────
# The Planner held the task tools too, and a small model asked "which jobs do
# we have?" listed tracker tasks, or created a task when asked to pause a job
# (found live). Tasks now belong to task_manager, which also replaces the
# Planner in the task planner chat.

TASK_TOOLS = ("list_tasks", "get_task", "create_task", "add_subtask", "create_sequence",
              "update_task", "block_task", "set_task_dependencies", "get_task_result")


def test_the_planner_holds_no_task_tool_and_the_task_manager_holds_them_all():
    planner, manager = _seed("planner"), _seed("task_manager")
    assert not set(TASK_TOOLS) & set(planner["tools"])
    assert set(TASK_TOOLS) <= set(manager["tools"])
    assert not {t for t in manager["tools"] if "schedul" in t}
    assert manager["system"] is True
    assert {c["name"] for c in manager["commands"]} == {"/plan", "/plan-from-view"}
    assert "/plan" not in {c["name"] for c in planner["commands"]}


def test_every_agent_that_reaches_the_planner_also_reaches_the_task_manager():
    data = json.loads((ROOT / "bootstrap" / "agents.json").read_text(encoding="utf-8"))
    for agent in data["agents"]:
        if "@planner" in (agent.get("delegates") or []):
            assert "@task_manager" in agent["delegates"], agent["id"]


def test_the_task_planner_chat_runs_the_task_manager_and_it_is_a_role():
    from agents import roles
    from projects import planner_service
    assert planner_service.PLANNER_AGENT_ID == "task_manager"
    role = {r.id: r for r in roles.ROLES}["task_manager"]
    assert role.default == "task_manager"


def test_migration_0046_moves_the_tasks_off_the_planner():
    import importlib.util
    path = ROOT / "common" / "migrations" / "0046_task_manager_owns_tasks.py"
    spec = importlib.util.spec_from_file_location("m0046", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    conn = _db({
        "planner": {"id": "planner", "user_modified": True,
                    "tools": ["list_tasks", "schedule_task", "create_task", "list_scheduled"],
                    "tool_policy": {"create_task": "always_ask"},
                    "commands": [{"name": "/plan"}, {"name": "/schedule"}]},
        "main-agent": {"id": "main-agent", "user_modified": True, "tools": ["create_task"],
                       "delegates": ["orchestrator", "@planner", "@writer"]},
        "mine": {"id": "mine", "delegates": ["@task_manager", "@planner"]},
    })
    mod.upgrade(conn, "sqlite")
    planner = _doc(conn, "planner")
    assert planner["tools"] == ["schedule_task", "list_scheduled"]
    assert planner["tool_policy"] == {} and planner["commands"] == [{"name": "/schedule"}]
    main = _doc(conn, "main-agent")
    assert main["delegates"] == ["orchestrator", "@planner", "@task_manager", "@writer"]
    assert main["tools"] == ["create_task"]
    assert _doc(conn, "mine")["delegates"] == ["@task_manager", "@planner"]
