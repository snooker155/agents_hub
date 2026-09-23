"""
Record-level visibility (common/access.py): whether a request that names no
workspace still only shows a caller their own records.

``common.auth.authorize`` lets a request naming no workspace through for any
signed-in account in ``multi`` mode (see its "Record-level ownership is out
of scope" comment) — that is a decision about the *request*, and until now
every list route it reached simply returned the whole table to whoever asked.
This exercises the routes common/access.py now filters: chats (owner *and*
workspace), tasks, sessions/runs, and a memory pool, plus the two escape
hatches — ``single`` mode changes nothing, and a scoped API key narrows even
an admin.

Follows the fixture shape of tests/test_identity_routes.py (``single``,
``multi``, ``_admin``, ``_member``).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

PASSWORD = "hunter2-but-longer"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture
def single(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin(client) -> dict:
    response = client.post("/api/auth/bootstrap",
                           json={"username": "root", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return _bearer(response.json()["token"])


def _member(client, admin_headers, username: str) -> tuple[str, dict]:
    created = client.post("/api/auth/users",
                          json={"username": username, "password": PASSWORD},
                          headers=admin_headers)
    assert created.status_code == 200, created.text
    session = client.post("/api/auth/login",
                          json={"username": username, "password": PASSWORD})
    assert session.status_code == 200, session.text
    return created.json()["id"], _bearer(session.json()["token"])


def _two_workspaces(client, admin):
    """w1 and w2, alice an editor of w1 only, bob an editor of w2 only."""
    assert client.post("/api/workspaces", json={"name": "w1"}, headers=admin).status_code == 200
    assert client.post("/api/workspaces", json={"name": "w2"}, headers=admin).status_code == 200
    alice_id, alice = _member(client, admin, "alice")
    bob_id, bob = _member(client, admin, "bob")
    assert client.put("/api/workspaces/w1/members",
                      json={"user_id": alice_id, "role": "editor"},
                      headers=admin).status_code == 200
    assert client.put("/api/workspaces/w2/members",
                      json={"user_id": bob_id, "role": "editor"},
                      headers=admin).status_code == 200
    return alice_id, alice, bob_id, bob


def _save_chat_as(user_id: str, chat_id: str, workspace: str) -> None:
    """Write a chat owned by ``user_id`` directly through the store, the way
    routes/chats.py's PUT route stamps the owner from the request in flight
    (common/identity.py's ``_current_user`` contextvar)."""
    from common import chat_store, identity
    token = identity.set_current_user(user_id)
    try:
        chat_store.save_chat({"id": chat_id, "workspace": workspace, "messages": []})
    finally:
        identity.reset_current_user(token)


# ── single mode: nothing is filtered ─────────────────────────────────────────

def test_single_mode_every_list_route_answers_with_everything(single, client):
    from common import chat_store
    chat_store.save_chat({"id": "solo-chat", "workspace": "default", "messages": []})
    client.post("/api/tasks", json={"title": "solo task"})

    for path in ("/api/chats", "/api/tasks", "/api/sessions", "/api/messages",
                 "/api/shared-memory", "/api/instances", "/api/nodes",
                 "/api/runs/groups"):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path}: {resp.text}"


# ── chats: owner and workspace both gate ─────────────────────────────────────

def test_chat_list_is_scoped_to_the_caller(multi, client):
    admin = _admin(client)
    alice_id, alice, bob_id, bob = _two_workspaces(client, admin)
    _save_chat_as(alice_id, "chat-alice", "w1")
    _save_chat_as(bob_id, "chat-bob", "w2")

    seen_by_alice = {c["id"] for c in client.get("/api/chats", headers=alice).json()["items"]}
    assert seen_by_alice == {"chat-alice"}

    seen_by_admin = {c["id"] for c in client.get("/api/chats", headers=admin).json()["items"]}
    assert {"chat-alice", "chat-bob"} <= seen_by_admin


def test_chat_get_and_delete_403_for_another_users_chat(multi, client):
    admin = _admin(client)
    alice_id, alice, bob_id, bob = _two_workspaces(client, admin)
    _save_chat_as(bob_id, "chat-bob2", "w2")

    assert client.get("/api/chats/chat-bob2", headers=alice).status_code == 403
    assert client.get("/api/chats/chat-bob2", headers=bob).status_code == 200
    # Overwriting is gated the same way as reading: knowing the id is not enough.
    overwrite = {"id": "chat-bob2", "workspace": "w2", "messages": []}
    assert client.put("/api/chats/chat-bob2", json=overwrite,
                      headers=alice).status_code == 403
    assert client.put("/api/chats/chat-bob2", json=overwrite,
                      headers=bob).status_code == 200
    assert client.delete("/api/chats/chat-bob2", headers=alice).status_code == 403
    assert client.delete("/api/chats/chat-bob2", headers=bob).status_code == 200


def test_a_system_owned_chat_in_a_visible_workspace_is_visible_to_any_member(multi, client):
    """``owner`` of ``local``/``""`` predates identity or was written by the
    system; docs/identity.md says such a record is anyone's who can see the
    workspace, not nobody's."""
    admin = _admin(client)
    alice_id, alice, bob_id, bob = _two_workspaces(client, admin)
    from common import chat_store
    chat_store.save_chat({"id": "chat-system", "workspace": "w1", "owner": "local",
                          "messages": []})

    assert client.get("/api/chats/chat-system", headers=alice).status_code == 200
    assert client.get("/api/chats/chat-system", headers=bob).status_code == 403


# ── tasks ─────────────────────────────────────────────────────────────────────

def test_task_list_and_get_are_scoped_to_workspace(multi, client):
    admin = _admin(client)
    alice_id, alice, bob_id, bob = _two_workspaces(client, admin)
    t1 = client.post("/api/tasks", json={"title": "t1", "workspace": "w1"},
                     headers=admin).json()
    t2 = client.post("/api/tasks", json={"title": "t2", "workspace": "w2"},
                     headers=admin).json()

    seen = {t["id"] for t in client.get("/api/tasks", headers=alice).json()}
    assert t1["id"] in seen and t2["id"] not in seen

    assert client.get(f"/api/tasks/{t1['id']}", headers=alice).status_code == 200
    assert client.get(f"/api/tasks/{t2['id']}", headers=alice).status_code == 403

    admin_seen = {t["id"] for t in client.get("/api/tasks", headers=admin).json()}
    assert {t1["id"], t2["id"]} <= admin_seen


# ── sessions and runs ─────────────────────────────────────────────────────────

def _make_session_and_run(workspace: str, title: str) -> tuple[str, str]:
    from common import session_service
    from managers import run_manager
    session_id = session_service.get_or_create_task_session(title=title, workspace=workspace)
    run_id = run_manager.new_unique_run_id()
    run_manager.open_run(run_id, "probe_agent", session_id=session_id,
                         workspace=workspace, status="completed", link_to_session=True)
    return session_id, run_id


def test_session_list_and_get_are_scoped_to_workspace(multi, client):
    admin = _admin(client)
    alice_id, alice, bob_id, bob = _two_workspaces(client, admin)
    sid1, _ = _make_session_and_run("w1", "session in w1")
    sid2, _ = _make_session_and_run("w2", "session in w2")

    seen = {s["session_id"] for s in client.get("/api/sessions", headers=alice).json()["items"]}
    assert sid1 in seen and sid2 not in seen

    assert client.get(f"/api/sessions/{sid1}", headers=alice).status_code == 200
    assert client.get(f"/api/sessions/{sid2}", headers=alice).status_code == 403


def test_run_list_and_get_are_scoped_to_workspace(multi, client):
    admin = _admin(client)
    alice_id, alice, bob_id, bob = _two_workspaces(client, admin)
    _, run1 = _make_session_and_run("w1", "run in w1")
    _, run2 = _make_session_and_run("w2", "run in w2")

    seen = {r["run_id"] for r in client.get("/api/messages", headers=alice).json()["items"]}
    assert run1 in seen and run2 not in seen

    assert client.get(f"/api/messages/{run1}", headers=alice).status_code == 200
    assert client.get(f"/api/messages/{run2}", headers=alice).status_code == 403
    assert client.get(f"/api/messages/{run2}/logs", headers=alice).status_code == 403
    assert client.get(f"/api/messages/{run2}/insights", headers=alice).status_code == 403


# ── memory pools ──────────────────────────────────────────────────────────────

def test_memory_pool_is_visible_only_from_its_workspace(multi, client):
    admin = _admin(client)
    alice_id, alice, bob_id, bob = _two_workspaces(client, admin)
    created = client.post("/api/shared-memory",
                          json={"name": "pool-w1", "workspace": "w1"}, headers=admin)
    assert created.status_code == 200, created.text
    pool_id = created.json()["id"]

    ids_alice = {m["id"] for m in client.get("/api/shared-memory", headers=alice).json()}
    assert pool_id in ids_alice
    ids_bob = {m["id"] for m in client.get("/api/shared-memory", headers=bob).json()}
    assert pool_id not in ids_bob

    assert client.get(f"/api/shared-memory/{pool_id}", headers=alice).status_code == 200
    assert client.get(f"/api/shared-memory/{pool_id}", headers=bob).status_code == 403


# ── a scoped API key narrows even an admin ───────────────────────────────────

def test_a_key_scoped_to_one_workspace_does_not_see_the_other_even_for_an_admin(multi, client):
    admin = _admin(client)
    alice_id, alice, bob_id, bob = _two_workspaces(client, admin)
    t1 = client.post("/api/tasks", json={"title": "t1", "workspace": "w1"},
                     headers=admin).json()
    t2 = client.post("/api/tasks", json={"title": "t2", "workspace": "w2"},
                     headers=admin).json()

    admin_id = client.get("/api/auth/me", headers=admin).json()["id"]
    from common import api_keys
    key, _record = api_keys.create_key(admin_id, workspaces=["w2"])
    scoped = _bearer(key)

    # The credential is an admin's, but scope always wins (common/access.py).
    seen = {t["id"] for t in client.get("/api/tasks", headers=scoped).json()}
    assert t2["id"] in seen
    assert t1["id"] not in seen
    assert client.get(f"/api/tasks/{t1['id']}", headers=scoped).status_code == 403
    assert client.get(f"/api/tasks/{t2['id']}", headers=scoped).status_code == 200
