"""routes/account.py over real HTTP: sessions, password, personal API keys.

Same fixture shape as ``tests/test_identity_routes.py`` (bootstrap an admin,
add a member, log in) since this is the same guard and the same client.
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


def _member(client, admin_headers, username="bob") -> tuple[str, dict]:
    created = client.post("/api/auth/users",
                          json={"username": username, "password": PASSWORD},
                          headers=admin_headers)
    assert created.status_code == 200, created.text
    session = client.post("/api/auth/login",
                          json={"username": username, "password": PASSWORD})
    assert session.status_code == 200, session.text
    return created.json()["id"], _bearer(session.json()["token"])


# ── outside multi mode, and with no credential ───────────────────────────────

def test_the_account_routes_are_404_outside_multi_mode(client):
    assert client.get("/api/auth/sessions").status_code == 404
    assert client.get("/api/auth/keys").status_code == 404
    assert client.post("/api/auth/password",
                       json={"current_password": "x", "password": "y"}).status_code == 404


def test_sessions_need_a_credential(multi, client):
    assert client.get("/api/auth/sessions").status_code == 401
    assert client.get("/api/auth/keys").status_code == 401


def test_the_service_credential_has_no_account_of_its_own(multi, client):
    from common import identity
    headers = _bearer(identity.service_token())
    assert client.get("/api/auth/sessions", headers=headers).status_code == 403
    assert client.get("/api/auth/keys", headers=headers).status_code == 403


# ── API keys: creation, scope, resolution ────────────────────────────────────

def test_a_key_authenticates_as_its_owner(multi, client):
    admin = _admin(client)
    me = client.get("/api/auth/me", headers=admin).json()
    created = client.post("/api/auth/keys", json={"name": "laptop"}, headers=admin)
    assert created.status_code == 200, created.text
    body = created.json()
    assert "key" in body and body["key"].startswith("ahk_")
    assert body["name"] == "laptop"

    key_headers = _bearer(body["key"])
    as_key = client.get("/api/auth/me", headers=key_headers).json()
    assert as_key["id"] == me["id"]
    assert as_key["via"] == "api_key"
    assert as_key["credential_id"] == body["id"]


def test_a_scoped_key_is_refused_outside_its_scope_even_for_an_admin(multi, client):
    admin = _admin(client)
    assert client.post("/api/workspaces", json={"name": "alpha"},
                       headers=admin).status_code == 200
    assert client.post("/api/workspaces", json={"name": "beta"},
                       headers=admin).status_code == 200

    created = client.post("/api/auth/keys",
                          json={"name": "scoped", "workspaces": ["alpha"]},
                          headers=admin)
    assert created.status_code == 200, created.text
    key_headers = _bearer(created.json()["key"])

    # Outside its scope: refused, admin or not.
    assert client.get("/api/workspaces/beta", headers=key_headers).status_code == 403
    # Inside its scope: allowed.
    assert client.get("/api/workspaces/alpha", headers=key_headers).status_code == 200


def test_a_non_admin_may_not_scope_a_key_beyond_their_own_membership(multi, client):
    admin = _admin(client)
    client.post("/api/workspaces", json={"name": "alpha"}, headers=admin)
    bob_id, bob = _member(client, admin)

    # Bob is not a member of alpha at all.
    denied = client.post("/api/auth/keys",
                         json={"name": "reach-too-far", "workspaces": ["alpha"]},
                         headers=bob)
    assert denied.status_code == 403

    client.put("/api/workspaces/alpha/members",
              json={"user_id": bob_id, "role": "viewer"}, headers=admin)
    allowed = client.post("/api/auth/keys",
                          json={"name": "fine", "workspaces": ["alpha"]}, headers=bob)
    assert allowed.status_code == 200, allowed.text


def test_a_non_admin_may_still_cut_an_unscoped_key(multi, client):
    admin = _admin(client)
    _, bob = _member(client, admin)
    created = client.post("/api/auth/keys", json={"name": "full-reach"}, headers=bob)
    assert created.status_code == 200
    assert created.json()["workspaces"] is None


def test_a_revoked_key_gives_401(multi, client):
    admin = _admin(client)
    created = client.post("/api/auth/keys", json={"name": "throwaway"}, headers=admin)
    key_headers = _bearer(created.json()["key"])
    assert client.get("/api/auth/me", headers=key_headers).status_code == 200

    revoked = client.delete(f"/api/auth/keys/{created.json()['id']}", headers=admin)
    assert revoked.status_code == 200
    assert client.get("/api/auth/me", headers=key_headers).status_code == 401


def test_my_keys_list_excludes_someone_elses(multi, client):
    admin = _admin(client)
    _, bob = _member(client, admin)
    client.post("/api/auth/keys", json={"name": "admin-key"}, headers=admin)
    client.post("/api/auth/keys", json={"name": "bob-key"}, headers=bob)
    mine = client.get("/api/auth/keys", headers=bob).json()
    assert [k["name"] for k in mine] == ["bob-key"]


# ── sessions ─────────────────────────────────────────────────────────────────

def test_sessions_list_marks_the_current_one(multi, client):
    admin = _admin(client)
    me = client.get("/api/auth/me", headers=admin).json()
    sessions = client.get("/api/auth/sessions", headers=admin).json()
    assert len(sessions) == 1
    assert sessions[0]["id"] == me["credential_id"]
    assert sessions[0]["current"] is True


def test_revoking_a_session_by_id_ends_it(multi, client):
    admin = _admin(client)
    me = client.get("/api/auth/me", headers=admin).json()
    removed = client.delete(f"/api/auth/sessions/{me['credential_id']}", headers=admin)
    assert removed.status_code == 200
    assert client.get("/api/auth/me", headers=admin).status_code == 401


def test_revoking_someone_elses_session_id_is_404(multi, client):
    admin = _admin(client)
    _, bob = _member(client, admin)
    bob_me = client.get("/api/auth/me", headers=bob).json()
    # Admin tries to revoke bob's session through their *own* account route:
    # this route only ever touches the caller's own sessions.
    denied = client.delete(f"/api/auth/sessions/{bob_me['credential_id']}", headers=admin)
    assert denied.status_code == 404
    assert client.get("/api/auth/me", headers=bob).status_code == 200


def test_revoke_others_keeps_the_current_session(multi, client):
    admin = _admin(client)
    _, bob = _member(client, admin)
    # A second session for bob, from a second "device".
    second = client.post("/api/auth/login", json={"username": "bob", "password": PASSWORD})
    assert second.status_code == 200
    bob2 = _bearer(second.json()["token"])

    result = client.post("/api/auth/sessions/revoke-others", headers=bob)
    assert result.status_code == 200
    assert result.json()["revoked"] == 1

    assert client.get("/api/auth/me", headers=bob).status_code == 200
    assert client.get("/api/auth/me", headers=bob2).status_code == 401


# ── password ─────────────────────────────────────────────────────────────────

def test_password_change_with_the_wrong_current_password_is_refused(multi, client):
    admin = _admin(client)
    result = client.post("/api/auth/password",
                         json={"current_password": "not-it", "password": "a-new-one-longer"},
                         headers=admin)
    assert result.status_code in (400, 403)
    # The old password still works.
    assert client.post("/api/auth/login",
                       json={"username": "root", "password": PASSWORD}).status_code == 200


def test_password_change_with_the_right_current_password_drops_every_session(multi, client):
    admin = _admin(client)
    result = client.post("/api/auth/password",
                         json={"current_password": PASSWORD, "password": "a-new-one-longer"},
                         headers=admin)
    assert result.status_code == 200
    body = result.json()
    assert body["ok"] is True and body["relogin"] is True

    # The session that made the request is gone too.
    assert client.get("/api/auth/me", headers=admin).status_code == 401
    # The new password works.
    relog = client.post("/api/auth/login",
                        json={"username": "root", "password": "a-new-one-longer"})
    assert relog.status_code == 200


# ── an administrator, on someone else's keys ─────────────────────────────────

def test_an_admin_lists_and_revokes_another_users_keys(multi, client):
    admin = _admin(client)
    bob_id, bob = _member(client, admin)
    created = client.post("/api/auth/keys", json={"name": "bobs"}, headers=bob)
    key_id = created.json()["id"]

    listed = client.get(f"/api/auth/users/{bob_id}/keys", headers=admin)
    assert listed.status_code == 200
    assert [k["id"] for k in listed.json()] == [key_id]

    revoked = client.delete(f"/api/auth/users/{bob_id}/keys/{key_id}", headers=admin)
    assert revoked.status_code == 200
    assert api_key_dead(client, created.json()["key"])


def api_key_dead(client, key: str) -> bool:
    return client.get("/api/auth/me", headers=_bearer(key)).status_code == 401


def test_a_member_cannot_list_another_users_keys(multi, client):
    admin = _admin(client)
    bob_id, bob = _member(client, admin)
    _, olivia = _member(client, admin, username="olivia")
    denied = client.get(f"/api/auth/users/{bob_id}/keys", headers=olivia)
    assert denied.status_code == 403
