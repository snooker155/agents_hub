"""Writes on a record named by id are checked in the record's own workspace.

The middleware checks the workspace a request names; these routes name a view,
a run group, an eval set or run, an A2A task, a project or a consolidation job
instead, so each checks the record's workspace itself. In every test the caller
``u-a`` is an editor of ``alpha`` and nothing in ``beta``: a write on beta's
record is refused, the same write on alpha's goes through.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from common import identity
from common.auth import Principal

ROLES = {("u-a", "alpha"): "editor", ("u-v", "alpha"): "viewer"}
EDITOR = {"x-test-principal": "user:u-a:member"}
VIEWER = {"x-test-principal": "user:u-v:member"}
ADMIN = {"x-test-principal": "user:u-admin:admin"}


@pytest.fixture
def multi(monkeypatch):
    monkeypatch.setattr(identity, "current_mode", lambda: "multi")
    monkeypatch.setattr(identity, "membership_role", lambda ws, uid: ROLES.get((uid, ws)))
    monkeypatch.setattr(identity, "workspaces_for_user",
                        lambda uid: [ws for (u, ws) in ROLES if u == uid])


def _client(*modules):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.middleware("http")
    async def _principal(request: Request, call_next):
        who = request.headers.get("x-test-principal")
        if who:
            kind, _, rest = who.partition(":")
            uid, _, role = rest.partition(":")
            request.state.principal = Principal(id=uid, username=uid, role=role or "member", kind=kind)
        return await call_next(request)

    for module in modules:
        app.include_router(module.router)
    return TestClient(app)


# ── 1. views ─────────────────────────────────────────────────────────────────

def test_view_writes_need_editor_in_the_views_workspace(multi):
    from routes import views as views_routes
    from views import create_view, get_view

    client = _client(views_routes)
    theirs = create_view("markdown", "B", {"markdown": "b"}, workspace="beta").view_id
    mine = create_view("markdown", "A", {"markdown": "a"}, workspace="alpha").view_id

    for method, suffix, body in (("PATCH", "/state", {"state": {"x": 1}}),
                                 ("POST", "/checkpoints", {"name": "cp"}),
                                 ("POST", "/revert", {"seq": 0}),
                                 ("DELETE", "", None)):
        resp = client.request(method, f"/api/views/{theirs}{suffix}", json=body, headers=EDITOR)
        assert resp.status_code == 403, (method, suffix, resp.text)
        resp = client.request(method, f"/api/views/{mine}{suffix}", json=body, headers=EDITOR)
        assert resp.status_code == 200, (method, suffix, resp.text)
    assert get_view(theirs) is not None


def test_a_viewer_reads_a_view_but_does_not_change_it(multi):
    from routes import views as views_routes
    from views import create_view

    client = _client(views_routes)
    view_id = create_view("markdown", "A", {"markdown": "a"}, workspace="alpha").view_id
    assert client.get(f"/api/views/{view_id}", headers=VIEWER).status_code == 200
    assert client.delete(f"/api/views/{view_id}", headers=VIEWER).status_code == 403


def test_a_view_created_in_a_body_workspace_needs_editor_there(multi):
    from routes import views as views_routes

    client = _client(views_routes)
    body = {"kind": "markdown", "title": "S"}
    assert client.post("/api/views/studio", json={**body, "workspace": "beta"},
                       headers=EDITOR).status_code == 403
    assert client.post("/api/views/studio", json={**body, "workspace": "alpha"},
                       headers=EDITOR).status_code == 200


# ── 2. run groups ────────────────────────────────────────────────────────────

def test_stopping_a_run_group_needs_editor_in_its_workspace(multi, monkeypatch):
    from routes import run_groups as run_group_routes

    stopped = []
    groups = {gid: SimpleNamespace(workspace=gid, stop=lambda gid=gid: stopped.append(gid) or True)
              for gid in ("alpha", "beta")}
    monkeypatch.setattr(run_group_routes.run_groups, "get_group", lambda kind, gid: groups.get(gid))
    client = _client(run_group_routes)

    assert client.post("/api/runs/groups/container/beta/stop", headers=EDITOR).status_code == 403
    resp = client.post("/api/runs/groups/container/alpha/stop", headers=EDITOR)
    assert resp.status_code == 200 and resp.json()["stopped"] is True
    assert stopped == ["alpha"]


# ── 3. evals ─────────────────────────────────────────────────────────────────

def test_eval_writes_check_the_sets_and_runs_own_workspace(multi):
    from evals import store
    from evals.models import EvalRun, EvalSet, PromptSuggestion
    from routes import evals as eval_routes

    client = _client(eval_routes)
    theirs = store.save_eval_set(EvalSet(name="B", workspace="beta")).eval_set_id
    mine = store.save_eval_set(EvalSet(name="A", workspace="alpha")).eval_set_id

    # A request naming the caller's own workspace does not reach beta's set.
    assert client.delete(f"/api/evals/{theirs}", params={"workspace": "alpha"},
                         headers=EDITOR).status_code == 403
    assert client.delete(f"/api/evals/{mine}", headers=EDITOR).status_code == 200

    # A run that names no workspace inherits its set's.
    their_run = store.save_eval_run(EvalRun(eval_set_id=theirs, workspace=None)).eval_run_id
    assert client.post(f"/api/eval-runs/{their_run}/cancel", headers=EDITOR).status_code == 403

    sugg = store.save_prompt_suggestion(PromptSuggestion(eval_set_id=theirs, workspace="beta"))
    assert client.post(f"/api/prompt-suggestions/{sugg.suggestion_id}/dismiss",
                       headers=EDITOR).status_code == 403


def test_an_eval_set_may_not_target_another_workspaces_agent(multi, monkeypatch):
    from common import record_access
    from routes import evals as eval_routes

    homes = {"their-agent": "beta", "my-agent": "alpha"}
    monkeypatch.setattr(record_access, "resolve",
                        lambda kind, rid: record_access.Record(workspace=homes[rid]) if rid in homes else None)
    client = _client(eval_routes)
    body = {"name": "S", "workspace": "alpha"}
    resp = client.post("/api/evals", json={**body, "target": {"kind": "agent", "id": "their-agent"}},
                       headers=EDITOR)
    assert resp.status_code == 403, resp.text
    resp = client.post("/api/evals", json={**body, "target": {"kind": "agent", "id": "my-agent"}},
                       headers=EDITOR)
    assert resp.status_code == 200, resp.text


# ── 4. A2A ───────────────────────────────────────────────────────────────────

def _rpc(client, method, task_id, headers):
    resp = client.post("/api/a2a/agents/helper",
                       json={"jsonrpc": "2.0", "id": 1, "method": method, "params": {"id": task_id}},
                       headers=headers)
    assert resp.status_code == 200
    return resp.json()


def test_a2a_tasks_are_read_and_cancelled_only_in_their_workspace(multi, monkeypatch):
    from a2a import server as a2a
    from routes import a2a as a2a_routes
    from tasks import service as tasks_service

    monkeypatch.setattr(a2a_routes.registry, "get_agent",
                        lambda aid: SimpleNamespace(id=aid, owner_workspace="alpha"))
    client = _client(a2a_routes)
    theirs = str(tasks_service.create_task("b", workspace="beta").id)
    mine = str(tasks_service.create_task("a", workspace="alpha").id)

    assert _rpc(client, "tasks/get", theirs, EDITOR)["error"]["code"] == a2a.TASK_NOT_FOUND
    assert "result" in _rpc(client, "tasks/get", mine, EDITOR)
    assert _rpc(client, "tasks/cancel", theirs, EDITOR)["error"]["code"] == a2a.TASK_NOT_FOUND
    # Seen but not editable: the viewer is told why, not that it is missing.
    assert _rpc(client, "tasks/cancel", mine, VIEWER)["error"]["code"] == a2a.INVALID_REQUEST
    # The editor gets past the check to the run itself (none here to stop).
    assert _rpc(client, "tasks/cancel", mine, EDITOR)["error"]["code"] == a2a.TASK_NOT_CANCELABLE


# ── 5. trackers ──────────────────────────────────────────────────────────────

def test_a_projects_tracker_is_read_and_set_only_in_its_workspace(multi):
    from projects.models import Project
    from routes import trackers as tracker_routes

    client = _client(tracker_routes)
    theirs = tracker_routes._store.add(Project(name="B", workspace="beta")).id
    mine = tracker_routes._store.add(Project(name="A", workspace="alpha")).id

    assert client.get(f"/api/trackers/projects/{theirs}", headers=EDITOR).status_code == 403
    assert client.get(f"/api/trackers/projects/{mine}", headers=VIEWER).status_code == 200
    body = {"provider": "none"}
    assert client.put(f"/api/trackers/projects/{theirs}", json=body, headers=EDITOR).status_code == 403
    assert client.put(f"/api/trackers/projects/{mine}", json=body, headers=VIEWER).status_code == 403
    assert client.put(f"/api/trackers/projects/{mine}", json=body, headers=EDITOR).status_code == 200


# ── 6. memory consolidation ──────────────────────────────────────────────────

def test_a_consolidation_whose_pool_is_gone_is_refused_to_a_member(multi):
    from memory import consolidation
    from routes import memory_consolidation as mc_routes

    client = _client(mc_routes)
    job = consolidation._insert_queued("no-such-pool", workspace="alpha", session_limit=5,
                                       trigger="manual", actor_kind=None, actor_id=None)
    assert client.get(f"/api/memory/consolidations/{job}", headers=EDITOR).status_code == 404
    assert client.post(f"/api/memory/consolidations/{job}/discard", headers=EDITOR).status_code == 404
    assert client.get(f"/api/memory/consolidations/{job}", headers=ADMIN).status_code == 200


def test_consolidation_writes_need_editor_in_the_pools_workspace(multi):
    from memory import consolidation
    from memory.models import SharedMemory
    from memory.store import MemoryStore
    from routes import memory_consolidation as mc_routes

    client = _client(mc_routes)
    store = MemoryStore()
    theirs = store.add(SharedMemory(name="B", workspace="beta"))
    mine = store.add(SharedMemory(name="A", workspace="alpha"))
    jobs = {m.workspace: consolidation._insert_queued(str(m.id), workspace=m.workspace, session_limit=5,
                                                      trigger="manual", actor_kind=None, actor_id=None)
            for m in (theirs, mine)}

    assert client.post(f"/api/memory/consolidations/{jobs['beta']}/discard",
                       headers=EDITOR).status_code == 403
    assert client.post(f"/api/memory/consolidations/{jobs['alpha']}/discard",
                       headers=VIEWER).status_code == 403
    # Past the check: a queued job is not discardable yet.
    assert client.post(f"/api/memory/consolidations/{jobs['alpha']}/discard",
                       headers=EDITOR).status_code == 400
    assert client.post(f"/api/memory/{theirs.id}/consolidate", json={},
                       headers=EDITOR).status_code == 403
