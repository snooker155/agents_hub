"""Memory pool routes: seeing a pool is not writing it.

The caller ``u-a`` is an editor of ``alpha``, ``u-v`` a viewer there, and
neither reaches ``beta``. A viewer reads a pool and gets 403 on every change;
an editor changes it; a pool or a search in a workspace the caller is not in
is refused; a personal pool is written by its owner whatever their role.
"""
from __future__ import annotations

import pytest

from common import identity
from common.auth import Principal
from memory.models import SharedMemory
from memory.store import MemoryStore
from memory import personal

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


@pytest.fixture
def client(multi):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from routes import memory as memory_routes
    from routes import memory_versions as versions_routes

    app = FastAPI()

    @app.middleware("http")
    async def _principal(request: Request, call_next):
        who = request.headers.get("x-test-principal")
        if who:
            kind, _, rest = who.partition(":")
            uid, _, role = rest.partition(":")
            request.state.principal = Principal(id=uid, username=uid, role=role or "member", kind=kind)
        return await call_next(request)

    app.include_router(memory_routes.router)
    app.include_router(versions_routes.router)
    return TestClient(app)


def _pool(workspace, **kw) -> SharedMemory:
    mem = SharedMemory(name=f"pool {workspace}", workspace=workspace, **kw)
    MemoryStore().add(mem)
    return mem


def test_a_viewer_reads_a_pool_but_every_write_is_refused(client):
    mem = _pool("alpha")
    base = f"/api/shared-memory/{mem.id}"
    assert client.get(base, headers=VIEWER).status_code == 200
    assert client.get(f"{base}/blocks", headers=VIEWER).status_code == 200

    writes = [
        ("PATCH", "", {"name": "x"}),
        ("POST", "/notes", {"title": "t", "content": "c"}),
        ("PUT", "/blocks/persona", {"value": "x"}),
        ("DELETE", "/blocks/persona", None),
        ("PUT", "/structured/slot", {"data": {"k": "x"}}),
        ("DELETE", "/structured/slot", None),
        ("POST", "/files/from-workspace", {"file_id": "none"}),
        ("POST", "/graph/prune", None),
        ("POST", "/graph/merge-slots", None),
        ("DELETE", "", None),
    ]
    for method, suffix, body in writes:
        resp = client.request(method, f"{base}{suffix}", json=body, headers=VIEWER)
        assert resp.status_code == 403, (method, suffix, resp.text)
    for method, suffix in (("POST", "/files/f/index"), ("POST", "/rag/reindex"),
                           ("DELETE", "/files/f")):
        resp = client.request(method, f"{base}{suffix}?workspace=alpha", headers=VIEWER)
        assert resp.status_code == 403, (method, suffix, resp.text)
    # Nothing was deleted by the refused DELETE.
    assert MemoryStore().get(mem.id) is not None


def test_a_viewer_cannot_restore_or_redact_a_version(client):
    mem = _pool("alpha")
    for action in ("restore", "redact"):
        resp = client.post(f"/api/memory/{mem.id}/versions/1/{action}", json={}, headers=VIEWER)
        assert resp.status_code == 403, (action, resp.text)
    # An editor gets past the role check (the version itself does not exist).
    resp = client.post(f"/api/memory/{mem.id}/versions/999999/restore", headers=EDITOR)
    assert resp.status_code == 404


def test_an_editor_changes_a_pool(client):
    mem = _pool("alpha")
    base = f"/api/shared-memory/{mem.id}"
    assert client.patch(base, json={"name": "renamed"}, headers=EDITOR).status_code == 200
    assert client.post(f"{base}/notes", json={"title": "t", "content": "c"},
                       headers=EDITOR).status_code == 200
    assert client.delete(base, headers=EDITOR).status_code == 200
    assert MemoryStore().get(mem.id) is None


def test_a_member_of_alpha_cannot_touch_betas_pool(client):
    mem = _pool("beta")
    base = f"/api/shared-memory/{mem.id}"
    assert client.get(base, headers=EDITOR).status_code == 403
    assert client.patch(base, json={"name": "x"}, headers=EDITOR).status_code == 403
    assert client.post(f"{base}/rag/search", json={"query": "q"}, headers=EDITOR).status_code == 403
    assert client.post(f"{base}/rag/search", json={"query": "q"}, headers=VIEWER).status_code == 403


def test_a_search_needs_visibility_only_and_a_missing_pool_is_404(client):
    mem = _pool("alpha")
    assert client.post(f"/api/shared-memory/{mem.id}/rag/search", json={"query": "q"},
                       headers=VIEWER).status_code == 200
    assert client.post("/api/shared-memory/00000000-0000-0000-0000-000000000001/rag/search",
                       json={"query": "q"}, headers=VIEWER).status_code == 404


def test_a_pool_is_created_only_where_the_caller_is_an_editor(client):
    body = {"name": "new"}
    assert client.post("/api/shared-memory", json={**body, "workspace": "beta"},
                       headers=EDITOR).status_code == 403
    assert client.post("/api/shared-memory", json={**body, "workspace": "alpha"},
                       headers=VIEWER).status_code == 403
    # No workspace means default, where u-a holds no role.
    assert client.post("/api/shared-memory", json=body, headers=EDITOR).status_code == 403
    assert client.post("/api/shared-memory", json={**body, "workspace": "alpha"},
                       headers=EDITOR).status_code == 200
    assert client.post("/api/shared-memory", json={**body, "workspace": "beta"},
                       headers=ADMIN).status_code == 200


def test_the_knowledge_file_routes_check_the_workspace_they_name(client):
    mem = _pool("alpha")
    base = f"/api/shared-memory/{mem.id}"
    # An editor of alpha names beta's folder: refused on read and on write.
    assert client.get(f"{base}/files?workspace=beta", headers=EDITOR).status_code == 403
    for method, suffix in (("POST", "/files/f/index"), ("POST", "/rag/reindex"),
                           ("DELETE", "/files/f")):
        resp = client.request(method, f"{base}{suffix}?workspace=beta", headers=EDITOR)
        assert resp.status_code == 403, (method, suffix, resp.text)
    resp = client.post(f"{base}/files/upload", data={"workspace": "beta"},
                       files={"file": ("a.txt", b"x")}, headers=EDITOR)
    assert resp.status_code == 403


def test_a_personal_pool_is_written_by_its_owner_whatever_their_role(client):
    mem = _pool("alpha", kind=personal.PERSONAL_KIND, owner_user="u-v")
    base = f"/api/shared-memory/{mem.id}"
    # u-v is only a viewer of alpha, yet the pool is theirs.
    assert client.post(f"{base}/notes", json={"title": "t", "content": "c"},
                       headers=VIEWER).status_code == 200
    # Another member, even an editor, does not see it, so cannot write it.
    assert client.post(f"{base}/notes", json={"title": "t", "content": "c"},
                       headers=EDITOR).status_code == 403
    assert client.patch(base, json={"name": "x"}, headers=ADMIN).status_code == 200
