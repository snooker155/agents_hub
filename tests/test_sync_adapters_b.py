"""
Sync adapters, second batch (hubsync/adapters/): flows, teams, loops,
scenarios, worlds, tasks, scheduled jobs, watchers, projects, and finished
agent and entity runs.

Two hubs are simulated on one database: a record is read from workspace A,
removed locally (the other hub does not have it yet), applied into workspace
B and read back. The content hash must be the same, or the engine would see
its own write as a change and send it back forever. A record's id is the same
on both hubs, which is why the original has to go before the copy lands.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from common import db
from common import entity_runs as er
from hubsync.adapters import base
from hubsync.adapters.base import AdapterError
from workspace import create_workspace_folder

A, B = "ws-a", "ws-b"
OWNER = "local"


@pytest.fixture(autouse=True)
def workspaces():
    create_workspace_folder(A)
    create_workspace_folder(B)


def adapter(kind):
    found = base.get(kind)
    assert found is not None, kind
    return found


def move(kind, record_id, *, owner=OWNER):
    """Read from A, drop it from A, apply into B, read back: (doc, copy)."""
    ad = adapter(kind)
    doc = ad.read(A, record_id)
    assert doc is not None
    json.dumps(doc)  # portable
    ad.delete(A, record_id)
    assert ad.read(A, record_id) is None
    assert ad.apply(B, record_id, doc, owner=owner) == record_id
    copy = ad.read(B, record_id)
    assert copy is not None
    assert ad.content_hash(copy) == ad.content_hash(doc), (doc, copy)
    assert record_id in ad.list_ids(B, owner)
    assert record_id not in ad.list_ids(A, owner)
    return doc, copy


def test_every_kind_is_registered_with_its_order():
    expected = {"flow": 30, "team": 32, "loop": 34, "scenario": 36, "world": 35, "task": 45,
                "plan": 47, "watcher": 48, "project": 44, "run": 70, "entity_run": 72}
    for kind, order in expected.items():
        assert adapter(kind).order == order


# ── flows ─────────────────────────────────────────────────────────────────────

def _flow(flow_id="f1"):
    from flow import registry
    from flow import store as flow_store
    from workspace.storage import update_workspace_metadata
    registry.add_user_entity(registry.FlowEntitySpec(
        id="my_proc", name="My proc", category="processor", entrypoint="flow.x:run"))
    flow_store.save_flow({
        "id": flow_id, "name": "Intake", "description": "d", "workspace": None,
        "nodes": [{"id": "n1", "type": "flowNode", "position": {"x": 12.5, "y": 40},
                   "data": {"entity_id": "my_proc", "category": "processor", "label": "P"}}],
        "edges": [], "webhook_secret": "s3cret", "task_id": "t", "running": True,
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-02T00:00:00+00:00",
    })
    update_workspace_metadata(A, {"allowed_flows": [flow_id]})
    return flow_id


def test_flow_round_trip_keeps_layout_entities_and_leaves_the_secret():
    from flow import registry
    from flow import store as flow_store
    fid = _flow()
    ad = adapter("flow")
    assert ad.list_ids(A, OWNER) == [fid]
    doc = ad.read(A, fid)
    assert "webhook_secret" not in json.dumps(doc) and "s3cret" not in json.dumps(doc)
    assert doc["nodes"][0]["position"] == {"x": 12.5, "y": 40}
    assert [e["id"] for e in doc["entities"]] == ["my_proc"]
    ad.delete(A, fid)
    # A global flow is only unlisted (other workspaces see it); the other
    # hub has never had it at all.
    assert flow_store.get_flow(fid) is not None
    flow_store.delete_flow(fid)
    registry.remove_user_entity("my_proc")  # the other hub has never heard of it
    ad.apply(B, fid, doc, owner=OWNER)
    copy = ad.read(B, fid)
    assert ad.content_hash(copy) == ad.content_hash(doc)
    assert registry.get_entity("my_proc") is not None
    assert flow_store.get_flow(fid)["owner_user"] == OWNER
    assert flow_store.get_flow(fid)["workspace"] == B  # owned there, not global
    assert ad.list_ids(B, OWNER) == [fid]
    assert copy["nodes"][0]["position"] == {"x": 12.5, "y": 40}


def test_flow_apply_keeps_the_local_secret_and_delete_unlists_it():
    from flow import store as flow_store
    from workspace.storage import get_workspace_metadata
    fid = _flow()
    ad = adapter("flow")
    doc = ad.read(A, fid)
    doc["name"] = "Renamed"
    ad.apply(A, fid, doc, owner=OWNER)
    assert flow_store.get_flow(fid)["webhook_secret"] == "s3cret"
    assert ad.read(A, fid)["name"] == "Renamed"
    ad.delete(A, fid)
    # Global: unlisted here, kept for the workspaces that see every global flow.
    assert flow_store.get_flow(fid) is not None
    assert fid not in (get_workspace_metadata(A).get("allowed_flows") or [])


def test_flow_of_another_person_is_not_listed_or_overwritten():
    from flow import store as flow_store
    fid = _flow()
    flow = flow_store.get_flow(fid)
    flow["owner_user"] = "someone"
    flow_store.save_flow(flow)
    ad = adapter("flow")
    assert ad.list_ids(A, OWNER) == []
    with pytest.raises(AdapterError):
        ad.apply(A, fid, {"nodes": [], "edges": []}, owner=OWNER)


def test_flow_with_a_missing_entity_does_not_read_as_deleted():
    from flow import registry
    fid = _flow()
    registry.remove_user_entity("my_proc")
    assert adapter("flow").read(A, fid) is not None


# ── teams, loops, scenarios, worlds ───────────────────────────────────────────

def test_team_round_trip_and_scope():
    from teams import store
    from teams.models import Team, TeamMember
    mine = store.save_team(Team(name="Crew", workspace=A, charter="c",
                                members=[TeamMember(agent_id="swe", name="Sam", role="eng")]))
    store.save_team(Team(name="Shared", workspace=None))
    store.save_team(Team(name="Other", workspace=B))
    assert adapter("team").list_ids(A, OWNER) == [mine.team_id]
    doc, _ = move("team", mine.team_id)
    assert "workspace" not in doc and doc["members"][0]["agent_id"] == "swe"
    assert store.get_team(mine.team_id).workspace == B


def test_team_in_another_workspace_is_refused():
    from teams import store
    from teams.models import Team
    other = store.save_team(Team(name="Other", workspace=B))
    with pytest.raises(AdapterError):
        adapter("team").apply(A, other.team_id, adapter("team").read(B, other.team_id), owner=OWNER)


def test_loop_round_trip():
    from loops import store
    from loops.models import Loop
    loop = store.save_loop(Loop(name="Polish", workspace=A, flow_id="f1",
                                exit_criterion="good", rubric="- clear", max_iterations=3))
    store.save_loop(Loop(name="Shared", workspace=None))
    assert adapter("loop").list_ids(A, OWNER) == [loop.loop_id]
    doc, _ = move("loop", loop.loop_id)
    assert doc["rubric"] == "- clear" and doc["max_iterations"] == 3


def test_scenario_and_world_round_trip():
    from playground import store
    from playground.models import Scenario
    from playground.worlds import WorldSpec
    world = store.save_world(WorldSpec(name="Village", workspace=A, rules=["be kind"]))
    scenario = store.save_scenario(Scenario(name="Market day", workspace=A,
                                            environment=f"world:{world.world_id}"))
    store.save_world(WorldSpec(name="Shared", workspace=None))
    assert adapter("world").list_ids(A, OWNER) == [world.world_id]
    assert adapter("scenario").list_ids(A, OWNER) == [scenario.scenario_id]
    wdoc, _ = move("world", world.world_id)
    assert wdoc["rules"] == ["be kind"]
    move("scenario", scenario.scenario_id)


# ── tasks ─────────────────────────────────────────────────────────────────────

def test_task_round_trip_drops_approvals_and_run_state():
    from tasks import storage
    store = storage.TaskStore()
    task = store.create("Write the memo", "details", workspace=A)
    task = store.update(task.id, status="done", pending_approval={"tool": "shell"},
                        approved_calls=[{"tool": "shell"}], retry_count=2,
                        assigned_agent_run_id="r1", session_id="s1")
    tid = str(task.id)
    storage.append_activity_log(None, tid, {"at": "t1", "what": "created"})
    storage.set_task_result(None, tid, "the memo", files=["a.md"], run_id="r1", agent_id="w")
    doc = adapter("task").read(A, tid)
    assert doc["status"] == "done" and doc["activity"] and doc["results"][0]["result"] == "the memo"
    for dropped in ("pending_approval", "approved_calls", "assigned_agent_run_id", "session_id",
                    "workspace", "created_by_user", "id"):
        assert dropped not in doc
    move("task", tid, owner="local")
    copy = storage.TaskStore().get(tid)
    assert copy.workspace == B and copy.pending_approval is None
    assert storage.get_task_results(None, tid)[0]["files"] == ["a.md"]


def test_task_apply_keeps_local_approvals():
    from tasks import storage
    store = storage.TaskStore()
    task = store.create("T", workspace=A)
    tid = str(task.id)
    ad = adapter("task")
    doc = ad.read(A, tid)
    store.update(task.id, pending_approval={"tool": "shell"}, retry_count=3)
    doc["title"] = "T2"
    ad.apply(A, tid, doc, owner=OWNER)
    now = store.get(tid)
    assert now.title == "T2" and now.pending_approval == {"tool": "shell"} and now.retry_count == 3


# ── scheduled jobs ────────────────────────────────────────────────────────────

def _job(**kw):
    from plans import service
    from plans.models import JobKind
    kw.setdefault("kind", JobKind.notification)
    kw.setdefault("title", "Water the plants")
    kw.setdefault("run_at", datetime.now(timezone.utc) + timedelta(days=1))
    kw.setdefault("workspace", A)
    return service.create_job(**kw)


def test_one_off_job_round_trip_arrives_paused():
    from plans import service
    job = _job()
    doc, _ = move("plan", str(job.id))
    assert "status" not in doc and "lease_owner" not in doc and "fire_count" not in doc
    copy = service.get_job(str(job.id))
    assert copy.status.value == "paused" and copy.paused_reason == "synced"
    assert copy.workspace == B and copy.run_at == job.run_at


def test_recurring_job_keeps_its_own_schedule_and_status():
    from plans import service
    from plans.models import Recurrence
    job = _job(recurrence=Recurrence.cron, cron="0 9 * * *", timezone="Europe/Berlin")
    jid = str(job.id)
    ad = adapter("plan")
    doc = ad.read(A, jid)
    assert "run_at" not in doc and doc["next_run_hint"]
    local_run_at = service.get_job(jid).run_at
    service.update_job(jid, run_at=local_run_at + timedelta(days=3))
    doc["title"] = "Renamed"
    ad.apply(A, jid, doc, owner=OWNER)
    now = service.get_job(jid)
    assert now.title == "Renamed" and now.status.value == "scheduled"
    assert now.run_at == local_run_at + timedelta(days=3)
    # the moving slot does not count as a change
    before = ad.content_hash(ad.read(A, jid))
    service.update_job(jid, run_at=local_run_at + timedelta(days=5))
    assert ad.content_hash(ad.read(A, jid)) == before
    doc["cron"] = "0 7 * * *"
    ad.apply(A, jid, doc, owner=OWNER)
    assert service.get_job(jid).run_at > datetime.now(timezone.utc)


def test_agent_owned_jobs_are_skipped_and_bad_schedules_refused():
    from plans.models import JobKind
    heartbeat = _job(kind=JobKind.heartbeat, created_by="system", agent_id="a")
    ad = adapter("plan")
    assert ad.list_ids(A, OWNER) == []
    assert ad.read(A, str(heartbeat.id)) is None
    doc = ad.read(A, str(_job().id))
    with pytest.raises(AdapterError):
        ad.apply(A, "x", {**doc, "timezone": "Mars/Base"}, owner=OWNER)


# ── watchers ──────────────────────────────────────────────────────────────────

def test_watcher_arrives_disabled_with_no_state_and_keeps_secret_names():
    from watchers import service
    from watchers.store import store
    w = service.create(A, {"kind": "imap", "name": "Inbox", "interval_seconds": 120,
                           "config": {"host": "imap.example.com", "username": "me",
                                      "password_secret": "MAIL_PW"}})
    store.update(w.id, state={"uid": 9}, last_error="boom", fired=4)
    doc, _ = move("watcher", w.id)
    assert doc["config"]["password_secret"] == "MAIL_PW"
    for local in ("enabled", "state", "last_error", "fired", "workspace", "paused_reason"):
        assert local not in doc
    copy = store.get(w.id)
    assert copy.enabled is False and copy.state == {} and copy.workspace == B


def test_watcher_apply_keeps_the_local_enabled_flag_and_resets_state_on_new_source():
    from watchers import service
    from watchers.store import store
    w = service.create(A, {"kind": "http", "name": "Page", "config": {"url": "https://example.com/a"}})
    store.update(w.id, state={"hash": "x"})
    ad = adapter("watcher")
    doc = ad.read(A, w.id)
    doc["name"] = "Page 2"
    ad.apply(A, w.id, doc, owner=OWNER)
    now = store.get(w.id)
    assert now.name == "Page 2" and now.enabled is True and now.state == {"hash": "x"}
    doc["config"] = {**doc["config"], "url": "https://example.com/b"}
    ad.apply(A, w.id, doc, owner=OWNER)
    assert store.get(w.id).state == {}


# ── projects ──────────────────────────────────────────────────────────────────

def test_project_round_trip_strips_url_credentials_and_keeps_graphs():
    from projects.graph_store import ProjectGraphStore
    from projects.models import Project, RepoConfig
    from projects.storage import ProjectStore
    project = ProjectStore().add(Project(
        name="Site", workspace=A, tags=["web"],
        repo=RepoConfig(type="github", url="https://me:tok3n@github.com/me/site.git")))
    graphs = ProjectGraphStore()
    graphs.save(project.id, "structure", [{"id": "a"}], [], source="manual")
    graphs.append_message(project.id, "structure", "user", "hello")
    graphs.save(project.id, "empty", [], [])
    doc = adapter("project").read(A, project.id)
    assert "tok3n" not in json.dumps(doc)
    assert list(doc["graphs"]) == ["structure"] and "messages" not in doc["graphs"]["structure"]
    move("project", project.id)
    assert ProjectGraphStore().get(project.id, "structure")["nodes"] == [{"id": "a"}]
    assert ProjectStore().get(project.id).workspace == B


def test_project_apply_keeps_the_working_clone_url():
    from projects.models import Project, RepoConfig
    from projects.storage import ProjectStore
    project = ProjectStore().add(Project(
        name="Site", workspace=A, repo=RepoConfig(url="https://me:tok3n@github.com/me/site.git")))
    ad = adapter("project")
    doc = ad.read(A, project.id)
    ad.apply(A, project.id, doc, owner=OWNER)
    assert ProjectStore().get(project.id).repo.url == "https://me:tok3n@github.com/me/site.git"


# ── agent runs ────────────────────────────────────────────────────────────────

def _run(run_id="run1", status="completed", workspace=A):
    from managers import run_manager
    from common.paths import AGENTS_HUB_ROOT
    log = AGENTS_HUB_ROOT / "run_logs" / f"agent_run_{run_id}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("step 1\nstep 2\n", encoding="utf-8")
    run_manager.upsert_run({
        "run_id": run_id, "agent_id": "writer", "workspace": workspace, "status": "running",
        "pid": 4242, "container_name": "c1", "log_file": str(log), "title": "Memo",
        "session_id": "s1", "model": "m", "provider": "p", "instance_id": "inst1",
        "created_at": "2026-01-01T00:00:00+00:00"})
    run_manager.update_run(run_id, {
        "status": status, "finished_at": "2026-01-01T00:01:00+00:00", "output": "done",
        "process": {
            "token_usage": {"inbound_tokens": 10, "outbound_tokens": 5, "total_tokens": 15},
            "duration_ms": 900,
            "input_context": {"system_prompt": "sp", "history": [], "user_message": "hi"},
            "response": {"text": "hello", "structured": None},
            "tool_calls": [{"step": 1, "tool": "x", "input": {}, "output": "y"}],
            "reasoning": ["thought"], "llm_invocations": [], "llm_raw_responses": [{"a": 1}],
            "artifacts": []}})
    return run_id


def test_finished_run_round_trip_and_machine_fields_stay_home():
    from managers.runs import store
    rid = _run()
    ad = adapter("run")
    assert ad.list_ids(A, OWNER) == [rid]
    doc = ad.read(A, rid)
    for local in ("pid", "container_name", "log_file", "instance_id", "run_id", "workspace"):
        assert local not in doc
    assert doc["usage"]["token_usage"]["total_tokens"] == 15
    assert doc["payload"]["response"]["text"] == "hello" and doc["log"] == "step 1\nstep 2\n"
    move("run", rid)
    rec = store._row_to_record(db.get_conn().execute(
        "SELECT * FROM runs WHERE run_id = ?", (rid,)).fetchone())
    assert rec["origin"] == "sync" and rec["workspace"] == B and rec["status"] == "completed"
    assert rec["process"]["token_usage"]["total_tokens"] == 15
    assert rec["process"]["duration_ms"] == 900


def test_running_runs_are_not_history():
    rid = _run("live", status="running")
    ad = adapter("run")
    assert ad.list_ids(A, OWNER) == []
    assert ad.read(A, rid) is None
    with pytest.raises(AdapterError):
        ad.apply(A, "x", {"status": "running"}, owner=OWNER)


def test_applying_a_run_fires_no_notifications_or_exports(monkeypatch):
    rid = _run()
    ad = adapter("run")
    doc = ad.read(A, rid)
    ad.delete(A, rid)
    calls = []
    from managers.runs import store as runs
    for name in ("_notify_task_run_finished", "_export_run_finished", "_mirror_run_finished",
                 "_sync_instance"):
        monkeypatch.setattr(runs, name, lambda *a, _n=name, **k: calls.append(_n))
    monkeypatch.setattr("notify.rules.evaluate_run_finished", lambda *a, **k: calls.append("rules"))
    ad.apply(B, rid, doc, owner=OWNER)
    assert calls == []


def test_long_log_is_cut_once_and_stays_stable():
    from hubsync.adapters import run as run_adapter
    rid = _run()
    path = run_adapter._log_path(rid)
    path.write_text("x" * (run_adapter.LOG_CAP + 5000), encoding="utf-8")
    ad = adapter("run")
    doc = ad.read(A, rid)
    assert doc["log"].startswith(run_adapter.LOG_CUT)
    ad.delete(A, rid)
    ad.apply(B, rid, doc, owner=OWNER)
    assert ad.content_hash(ad.read(B, rid)) == ad.content_hash(doc)


# ── flow, loop, team and scenario runs ────────────────────────────────────────

def _entity_run(kind, run_id, entity_id, status="completed", **extra):
    return er.upsert({"kind": kind, "run_id": run_id, "entity_id": entity_id, "workspace": A,
                      "status": status, "pid": 99, "host": "h", "heartbeat_at": "2026-01-01",
                      "created_at": "2026-01-01T00:00:00+00:00",
                      "finished_at": "2026-01-01T00:02:00+00:00", "total_cost": 0.5, **extra},
                     kind=kind, notify=False)


def test_flow_run_round_trip_with_its_log():
    from flow import run_store
    _entity_run("flow", "fr1", "f1", title="Intake run")
    run_store.append_flow_log("f1", "fr1", {"event": "start", "run_group": "fr1"})
    run_store.append_flow_log("f1", "fr1", {"event": "end", "run_group": "fr1"})
    ad = adapter("entity_run")
    assert ad.list_ids(A, OWNER) == ["fr1"]
    doc = ad.read(A, "fr1")
    for local in ("pid", "host", "heartbeat_at", "flow_run_id", "flow_id", "workspace"):
        assert local not in doc
    assert [e["event"] for e in doc["children"]] == ["start", "end"]
    move("entity_run", "fr1")
    assert [e["event"] for e in run_store.read_flow_logs("f1")] == ["start", "end"]
    rec = er.get("fr1")
    assert rec["workspace"] == B and rec["pid"] is None and rec["status"] == "completed"


def test_loop_team_and_scenario_runs_bring_their_children():
    from loops import store as loops
    from loops.models import Iteration
    from playground import store as playground
    from playground.models import TickRecord
    from teams import store as teams
    from teams.models import TeamMessage
    _entity_run("loop", "lr1", "l1")
    loops.save_iteration(Iteration(loop_run_id="lr1", iteration=1, score=71.0, verdict="continue",
                                   output="draft", status="completed"))
    _entity_run("team", "tr1", "t1")
    teams.append_message(TeamMessage(team_run_id="tr1", sender="Sam", content="hello"))
    teams.append_message(TeamMessage(team_run_id="tr1", sender="Rae", content="hi", round=1))
    _entity_run("scenario", "sr1", "s1", story={"text": "once"})
    playground.save_tick(TickRecord(sim_run_id="sr1", tick=0, events=["woke"], cost=0.1))
    for run_id, key in (("lr1", "output"), ("tr1", "content"), ("sr1", "events")):
        doc, _ = move("entity_run", run_id)
        assert doc["children"] and key in doc["children"][0]
    assert [m.content for m in teams.list_messages("tr1")] == ["hello", "hi"]
    assert loops.list_iterations("lr1")[0].get("score") == 71.0
    assert playground.list_ticks("sr1")[0]["events"] == ["woke"]
    assert er.get("sr1")["story"] == {"text": "once"}


def test_entity_runs_apply_as_history_without_alerts_or_relaunch(monkeypatch):
    _entity_run("flow", "fr2", "f1")
    ad = adapter("entity_run")
    doc = ad.read(A, "fr2")
    ad.delete(A, "fr2")
    calls = []
    monkeypatch.setattr("notify.rules.evaluate_run_finished", lambda *a, **k: calls.append("rules"))
    ad.apply(B, "fr2", doc, owner=OWNER)
    assert calls == []
    rec = er.get("fr2")
    assert rec["status"] == "completed" and "checkpoint" not in rec
    assert er.active_runs() == []
    with pytest.raises(AdapterError):
        ad.apply(B, "fr3", {**doc, "status": "running"}, owner=OWNER)


def test_unfinished_entity_runs_are_not_listed():
    _entity_run("flow", "live", "f1", status="running")
    _entity_run("flow", "done", "f1")
    ad = adapter("entity_run")
    assert ad.list_ids(A, OWNER) == ["done"]
    assert ad.read(A, "live") is None
