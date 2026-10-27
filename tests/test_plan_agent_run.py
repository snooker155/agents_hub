"""Scheduled agent runs, what a firing's work came to, and the agent's
criterion on scheduled tasks.

* ``agent_run`` jobs: the agent is required, a firing opens a conversation and
  starts a chat turn in it, and no task is created.
* ``fire_result``: a firing's ``ok`` means "started"; its result follows the
  task, flow run, loop run or conversation to where it stands now.
* ``use_agent_outcome`` on an ``agent_task`` job reaches the task it creates,
  and only that kind accepts it.
* migration 0048 gives an edited Planner record the new tool.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from plans import service as ps
from plans.models import FireRecord, JobKind, Notification, Recurrence, ScheduledJob
from plans.storage import FireStore, PlanStore

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]


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
def known_agent(monkeypatch):
    import agents.registry as registry
    monkeypatch.setattr(registry, "get_agent",
                        lambda agent_id: SimpleNamespace(id=agent_id) if agent_id == "researcher" else None)


@pytest.fixture
def turns(monkeypatch):
    started = []
    monkeypatch.setattr(ps, "_start_chat_turn", started.append)
    return started


def _due(**kw) -> ScheduledJob:
    defaults = dict(kind=JobKind.agent_run, title="Morning news", message="Collect the news",
                    run_at=datetime.now(UTC) - timedelta(seconds=5), agent_id="researcher")
    defaults.update(kw)
    return ScheduledJob(**defaults)


# ── agent_run jobs ───────────────────────────────────────────────────────────

def test_an_agent_run_job_needs_an_agent_that_exists(store, known_agent):
    with pytest.raises(ValueError, match="requires agent_id"):
        ps.create_job(kind=JobKind.agent_run, title="x", run_at=datetime.now(UTC))
    with pytest.raises(ValueError, match="Agent not found"):
        ps.create_job(kind=JobKind.agent_run, title="x", run_at=datetime.now(UTC), agent_id="ghost")
    job = ps.create_job(kind=JobKind.agent_run, title="x", message="go",
                        run_at=datetime.now(UTC), agent_id="researcher")
    assert job.kind == JobKind.agent_run and job.agent_id == "researcher"


def test_firing_an_agent_run_opens_a_conversation_and_starts_a_turn_without_a_task(
        store, known_agent, turns, notifications, monkeypatch):
    from common import chat_store
    from tasks import service as tasks_service
    created = []
    monkeypatch.setattr(tasks_service, "create_task", lambda **kw: created.append(kw))
    job = store.add(_due(recurrence=Recurrence.daily))

    result = ps.fire_job(job)

    assert result["ok"] is True
    conv_id = result["conversation_id"]
    chat = chat_store.get_chat(conv_id)
    assert chat["agent_id"] == "researcher" and chat["origin"] == "schedule"
    assert chat["title"].startswith("Morning news · ")
    assert len(turns) == 1
    request = turns[0]
    assert (request.conversation_id, request.agent_id, request.message, request.source) == \
        (conv_id, "researcher", "Collect the news", "schedule")
    assert created == []
    assert store.get(job.id).created_conversation_ids == [conv_id]
    row = ps.fire_store.list_for_job(job.id)[0]
    assert row.conversation_id == conv_id and row.task_id is None
    assert notifications[0]["source"]["conversation_id"] == conv_id


def test_a_scheduled_conversation_holds_its_message_and_then_the_answer(
        store, known_agent, turns, notifications):
    """Opened mid-run the conversation already shows the message (not the empty
    chat's greeting); the answer joins it when the turn ends, once."""
    from chat.broadcast import _persist_turn
    from common import chat_store
    job = store.add(_due())
    conv_id = ps.fire_job(job)["conversation_id"]
    assert [(m["role"], m["content"]) for m in chat_store.get_chat(conv_id)["messages"]] == \
        [("user", "Collect the news")]

    assert _persist_turn(conv_id, turns[0], {"ok": True, "run_id": "r1", "response": "Here they are",
                                            "agent_id": "researcher"})
    assert [(m["role"], m["content"]) for m in chat_store.get_chat(conv_id)["messages"]] == \
        [("user", "Collect the news"), ("agent", "Here they are")]


def test_an_agent_run_whose_agent_is_gone_fails_as_agent_missing(store, known_agent, turns, notifications):
    job = store.add(_due(agent_id="ghost"))
    result = ps.fire_job(job)
    assert result["ok"] is False and result["error_type"] == "agent_missing"
    assert turns == []


def test_a_chat_turn_started_off_the_loop_runs_on_a_thread(monkeypatch):
    """No running loop and no backend loop: the turn still runs, on a thread."""
    import threading
    import chat.pipelines as pipelines
    from common.session_broker import broker
    done = threading.Event()

    async def _pipeline(request):
        done.set()
        yield {"type": "done"}

    monkeypatch.setattr(pipelines, "run_chat_pipeline", _pipeline)
    monkeypatch.setattr(broker, "_loop", None)
    ps._start_chat_turn(SimpleNamespace(conversation_id="c1"))
    assert done.wait(5)


# ── an agent run's deployment resources ─────────────────────────────────────

@pytest.fixture
def run_resources(monkeypatch):
    """A project, a file and a pool in workspace ``dev``, and an agent whose
    tools the capability guard has nothing against."""
    import agents.registry as registry
    monkeypatch.setattr(registry, "get_agent", lambda agent_id: SimpleNamespace(
        id=agent_id, tools=[], secrets=[], capability_override=False, delegates=[])
        if agent_id == "researcher" else None)
    monkeypatch.setattr("projects.storage.ProjectStore.get",
                        lambda self, pid: SimpleNamespace(id=pid, workspace="dev") if pid == "proj-1" else None)
    monkeypatch.setattr("files.service.get_files", lambda ids: [
        {"file_id": i, "name": f"{i}.csv", "workspace": "dev"} for i in ids if i == "file_1"])
    monkeypatch.setattr("memory.store.MemoryStore.load",
                        lambda self: [SimpleNamespace(id="pool-1", workspace="dev", name="news")])


def test_an_agent_run_job_carries_the_deployment_resources(store, run_resources):
    job = ps.create_job(kind=JobKind.agent_run, title="x", message="go", run_at=datetime.now(UTC),
                        workspace="dev", agent_id="researcher", project_id="proj-1",
                        file_ids=["file_1"], secrets=["JIRA_TOKEN"], memory_pool_ids=["news"])
    assert (job.project_id, job.file_ids, job.secrets, job.memory_pool_ids, job.memory_access) == \
        ("proj-1", ["file_1"], ["JIRA_TOKEN"], ["pool-1"], "read")
    with pytest.raises(ValueError, match="does not exist"):
        ps.update_job(job.id, file_ids=["file_x"])


def test_a_firing_hands_the_resources_to_its_turn(store, run_resources, turns, notifications):
    from common import chat_store
    job = store.add(_due(workspace="dev", project_id="proj-1", file_ids=["file_1", "file_gone"],
                         secrets=["JIRA_TOKEN"], memory_pool_ids=["pool-1"], memory_access="write"))
    conv_id = ps.fire_job(job)["conversation_id"]

    request = turns[0]
    assert request.project_id == "proj-1"
    # The file still there is attached by id and named in the message; the
    # one deleted since the job was saved is left out, the run goes on.
    assert [a.file_id for a in request.attachments] == ["file_1"]
    assert request.message == "Collect the news\nAttached files: file_1.csv"
    assert request._grants == {"agent_id": "researcher", "memory_pool_ids": ["pool-1"],
                               "memory_access": "write", "secrets": ["JIRA_TOKEN"]}
    # The stored bubble is the message the turn answers, so it is not doubled.
    assert chat_store.get_chat(conv_id)["messages"][0]["content"] == request.message


def test_a_firing_without_resources_grants_nothing(store, known_agent, turns, notifications):
    ps.fire_job(store.add(_due()))
    assert turns[0].attachments == [] and turns[0]._grants is None


def test_grants_reach_only_the_job_agent():
    from chat.models import ChatRequest
    from chat.runs import grant_overrides, grant_secrets, journal_pool_id, request_overrides
    # A client cannot send grants: the field is private.
    sent = ChatRequest.model_validate({"agent_id": "researcher", "message": "hi",
                                       "_grants": {"agent_id": "researcher", "secrets": ["X"]}})
    assert sent._grants is None and grant_overrides(sent) == {}

    req = ChatRequest(agent_id="researcher", message="hi")
    req._grants = {"agent_id": "researcher", "memory_pool_ids": ["pool-1"],
                   "memory_access": "read", "secrets": ["JIRA_TOKEN"]}
    assert grant_overrides(req) == {"memory_pool": ["pool-1"], "memory_access": "read",
                                    "extra_secrets": ["JIRA_TOKEN"]}
    assert request_overrides(req)["memory_pool"] == ["pool-1"]
    # Read only: the exchange is journaled nowhere; write: into the pool.
    assert journal_pool_id(req) is None
    req._grants["memory_access"] = "write"
    assert journal_pool_id(req) == "pool-1"
    # The agent the turn is handed to gets none of it.
    handed = req.model_copy(update={"agent_id": "billing"})
    assert grant_overrides(handed) == {} and grant_secrets(handed) == []


# ── what a firing's work came to ─────────────────────────────────────────────

def test_a_fire_with_nothing_to_follow_has_no_result():
    assert ps.fire_result(FireRecord(job_id=ScheduledJob(kind=JobKind.notification, title="t",
                                                         run_at=datetime.now(UTC)).id)) is None


def test_a_started_task_is_running_until_it_resolves():
    from tasks import service as tasks_service
    from tasks.models import CreatedBy, TaskStatus
    task = tasks_service.create_task(title="Sum up sales", description="d",
                                     created_by=CreatedBy.user, status=TaskStatus.in_progress)
    fire = FireRecord(job_id=_due().id, task_id=str(task.id))

    assert ps.fire_result(fire)["state"] == "running"
    assert ps.fire_result(fire)["status"] == "in_progress"

    tasks_service.update_task(task.id, status=TaskStatus.blocked, blocked_reason="needs a key")
    out = ps.fire_result(fire)
    assert out["state"] == "waiting" and out["reason"] == "needs a key"

    tasks_service.update_task(task.id, status=TaskStatus.in_progress)
    tasks_service.update_task(task.id, status=TaskStatus.resolved)
    assert ps.fire_result(fire)["state"] == "completed"


def test_a_deleted_task_is_missing():
    from uuid import uuid4
    fire = FireRecord(job_id=_due().id, task_id=str(uuid4()))
    assert ps.fire_result(fire)["state"] == "missing"


def test_a_conversation_result_follows_its_turn(monkeypatch):
    from common import chat_store
    import managers.runs.lifecycle as lifecycle
    chat_store.save_chat({"id": "conv-1", "title": "t", "agent_id": "researcher", "messages": []})
    fire = FireRecord(job_id=_due().id, conversation_id="conv-1")

    monkeypatch.setattr(lifecycle, "latest_run_for_task", lambda task_id: None)
    assert ps.fire_result(fire)["state"] == "running"

    monkeypatch.setattr(lifecycle, "latest_run_for_task",
                        lambda task_id: {"run_id": "r1", "status": "completed"})
    chat_store.save_chat({"id": "conv-1", "title": "t", "agent_id": "researcher", "messages": [
        {"id": "u", "role": "user", "content": "Collect the news"},
        {"id": "a", "role": "agent", "content": "Here are the news of the day."},
    ]})
    out = ps.fire_result(fire)
    assert out["state"] == "completed" and out["run_id"] == "r1"
    assert out["result"] == "Here are the news of the day."


def test_run_states_read_both_spellings():
    assert ps._run_state("assigned") == "running"
    assert ps._run_state("stop") == "running"
    assert ps._run_state("done") == "completed"
    assert ps._run_state("error") == "failed"
    assert ps._run_state("awaiting_input") == "waiting"


def test_get_scheduled_adds_each_firing_s_result(store, monkeypatch):
    import tools.schedule_management as sm
    job = store.add(_due(kind=JobKind.notification, agent_id=None))
    ps.fire_store.add(FireRecord(job_id=job.id, ok=True, task_id="t-1"))
    monkeypatch.setattr(sm.plan_service, "fire_result", lambda f: {"state": "running"})
    out = json.loads(sm.get_scheduled.invoke({"id": str(job.id)}))
    assert out["fires"][0]["result"] == {"state": "running"}


# ── the agent's criterion on scheduled tasks ────────────────────────────────

def test_use_agent_outcome_belongs_to_agent_task_jobs_only(store):
    with pytest.raises(ValueError, match="agent task jobs only"):
        ps.create_job(kind=JobKind.flow, title="x", run_at=datetime.now(UTC), flow_id="f",
                      use_agent_outcome=True)
    job = ps.create_job(kind=JobKind.agent_task, title="x", run_at=datetime.now(UTC),
                        use_agent_outcome=True)
    assert job.use_agent_outcome is True


def test_a_scheduled_task_carries_the_job_s_switch(store, notifications):
    from tasks import service as tasks_service
    job = store.add(ScheduledJob(kind=JobKind.agent_task, title="Forecast", run_at=datetime.now(UTC),
                                 use_agent_outcome=True))
    task_id = ps._fire_agent_task(job)
    assert tasks_service.get_task(task_id).use_agent_outcome is True

    plain = store.add(ScheduledJob(kind=JobKind.agent_task, title="Plain", run_at=datetime.now(UTC)))
    assert tasks_service.get_task(ps._fire_agent_task(plain)).use_agent_outcome is False


def test_the_schedule_reply_says_whether_the_agent_has_a_criterion(monkeypatch):
    import agents.registry as registry
    import tools.schedule_management as sm
    specs = {"graded": SimpleNamespace(default_outcome={"rubric": "- **Done**: it is done"}),
             "plain": SimpleNamespace(default_outcome=None)}
    monkeypatch.setattr(registry, "get_agent", specs.get)
    assert "these tasks do not use it" in sm._criterion_note("graded", False)
    assert "graded against" in sm._criterion_note("graded", True)
    assert sm._criterion_note("plain", False) == ""
    assert "no default criterion" in sm._criterion_note("plain", True)


# ── the migration ────────────────────────────────────────────────────────────

def _migration():
    import importlib.util
    path = ROOT / "common" / "migrations" / "0048_planner_agent_runs.py"
    spec = importlib.util.spec_from_file_location("m0048", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_migration_gives_an_edited_planner_the_new_tool_once():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE documents (store TEXT, key TEXT, seq INTEGER, doc TEXT, "
                 "created_at TEXT, updated_at TEXT, PRIMARY KEY (store, key))")
    conn.execute("INSERT INTO documents VALUES ('agents', 'planner', 0, ?, NULL, NULL)",
                 (json.dumps({"id": "planner", "user_modified": True,
                              "tools": ["schedule_notification", "schedule_task", "schedule_flow"]}),))
    _migration().upgrade(conn, "sqlite")
    _migration().upgrade(conn, "sqlite")
    doc = json.loads(conn.execute("SELECT doc FROM documents WHERE key='planner'").fetchone()[0])
    assert doc["tools"] == ["schedule_notification", "schedule_task", "schedule_agent_run", "schedule_flow"]


def test_the_planner_seed_and_catalog_have_the_new_tool():
    from tools.capabilities import is_recognised_tool_id
    from tools.registry import get_tool_by_id
    seed = json.loads((ROOT / "bootstrap" / "agents.json").read_text(encoding="utf-8"))
    planner = next(a for a in seed["agents"] if a["id"] == "planner")
    assert "schedule_agent_run" in planner["tools"]
    assert is_recognised_tool_id("schedule_agent_run")
    assert get_tool_by_id("schedule_agent_run").category == "schedule_management"
