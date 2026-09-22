"""The identity routes and the guard in front of them, over real HTTP.

``tests/test_identity.py`` covers the decision table; this covers the wiring:
that the middleware puts the right principal on the request, that the public
routes really are public, that a workspace-scoped route refuses a non-member
and serves an editor, and, the property that matters most to everyone not
using any of this, that the default mode leaves every existing route exactly
as open as it was.
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
    """The default posture: one operator, nothing closed."""
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
    """Bootstrap the first admin and return their auth header."""
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


# ── single mode leaves everything as it was ──────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/health", "/api/tasks", "/api/workspaces", "/api/agents",
])
def test_single_mode_leaves_every_route_open(single, client, path):
    assert client.get(path).status_code == 200


def test_single_mode_reports_itself_and_offers_nothing(single, client):
    body = client.get("/api/auth/mode").json()
    assert body["mode"] == "single"
    assert body["bootstrap_required"] is False
    assert body["features"] == {"login": False, "users": False,
                                "members": False, "api_token": False}


def test_single_mode_has_no_user_management(single, client):
    """404, not 403: there are no accounts to be refused access to."""
    assert client.get("/api/auth/users").status_code == 404


def test_single_mode_still_answers_who_the_caller_is(single, client):
    me = client.get("/api/auth/me").json()
    assert me["id"] == "local" and me["kind"] == "local"


# ── token mode is unchanged ──────────────────────────────────────────────────

def test_token_mode_is_reached_by_setting_only_the_token(monkeypatch, client):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "s3cret", raising=False)
    assert client.get("/api/auth/mode").json()["mode"] == "token"
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers=_bearer("s3cret")).status_code == 200
    assert client.get("/api/health", headers={"X-Api-Token": "s3cret"}).status_code == 200
    assert client.get("/api/health", params={"token": "s3cret"}).status_code == 200


# ── multi: the public surface ────────────────────────────────────────────────

def test_multi_mode_closes_the_api_but_not_the_mode_probe(multi, client):
    assert client.get("/api/health").status_code == 401
    body = client.get("/api/auth/mode").json()
    assert body["mode"] == "multi"
    assert body["bootstrap_required"] is True
    assert body["features"]["login"] is True


def test_bootstrap_then_login_then_me(multi, client):
    headers = _admin(client)
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["username"] == "root" and me["role"] == "admin"
    assert client.get("/api/health", headers=headers).status_code == 200

    # Once an account exists the route closes for good.
    again = client.post("/api/auth/bootstrap",
                        json={"username": "other", "password": PASSWORD})
    assert again.status_code == 400


def test_login_refuses_a_wrong_password(multi, client):
    _admin(client)
    bad = client.post("/api/auth/login",
                      json={"username": "root", "password": "not-it"})
    assert bad.status_code == 401


def test_logout_ends_the_session(multi, client):
    headers = _admin(client)
    assert client.post("/api/auth/logout", headers=headers).json()["ok"] is True
    assert client.get("/api/health", headers=headers).status_code == 401


# ── multi: users ─────────────────────────────────────────────────────────────

def test_only_an_admin_manages_accounts(multi, client):
    admin = _admin(client)
    _, bob = _member(client, admin)
    assert client.get("/api/auth/users", headers=admin).status_code == 200
    assert client.get("/api/auth/users", headers=bob).status_code == 403


def test_an_admin_changes_a_role_and_deletes_an_account(multi, client):
    admin = _admin(client)
    bob_id, _ = _member(client, admin)
    patched = client.patch(f"/api/auth/users/{bob_id}", json={"role": "admin"},
                           headers=admin)
    assert patched.status_code == 200 and patched.json()["role"] == "admin"
    assert client.delete(f"/api/auth/users/{bob_id}", headers=admin).status_code == 200


def test_a_password_reset_invalidates_the_old_session(multi, client):
    admin = _admin(client)
    bob_id, bob = _member(client, admin)
    assert client.get("/api/auth/me", headers=bob).status_code == 200
    reset = client.post(f"/api/auth/users/{bob_id}/password",
                        json={"password": "a-different-password"}, headers=admin)
    assert reset.status_code == 200
    assert client.get("/api/auth/me", headers=bob).status_code == 401


# ── multi: workspace scoping ─────────────────────────────────────────────────

def test_a_workspace_route_refuses_a_non_member_and_serves_an_editor(multi, client):
    admin = _admin(client)
    assert client.post("/api/workspaces", json={"name": "alpha"},
                       headers=admin).status_code == 200
    bob_id, bob = _member(client, admin)

    # Not a member: 403, not 401. An authenticated caller who is simply not
    # a member has a live session, and a 401 would make the browser drop it.
    assert client.get("/api/workspaces/alpha", headers=bob).status_code == 403
    assert [w["name"] for w in client.get("/api/workspaces", headers=bob).json()] == []

    added = client.put("/api/workspaces/alpha/members",
                       json={"user_id": bob_id, "role": "editor"}, headers=admin)
    assert added.status_code == 200
    assert client.get("/api/workspaces/alpha", headers=bob).status_code == 200
    assert [w["name"] for w in client.get("/api/workspaces", headers=bob).json()] == ["alpha"]


def test_an_editor_may_not_delete_the_workspace_or_read_its_env(multi, client):
    admin = _admin(client)
    client.post("/api/workspaces", json={"name": "alpha"}, headers=admin)
    bob_id, bob = _member(client, admin)
    client.put("/api/workspaces/alpha/members",
               json={"user_id": bob_id, "role": "editor"}, headers=admin)

    assert client.get("/api/workspaces/alpha/env", headers=bob).status_code == 403
    assert client.get("/api/workspaces/alpha/members", headers=bob).status_code == 403
    assert client.delete("/api/workspaces/alpha", headers=bob).status_code == 403


def test_an_owner_manages_the_members_of_their_own_workspace(multi, client):
    admin = _admin(client)
    client.post("/api/workspaces", json={"name": "alpha"}, headers=admin)
    owner_id, owner = _member(client, admin, username="olivia")
    client.put("/api/workspaces/alpha/members",
               json={"user_id": owner_id, "role": "owner"}, headers=admin)
    bob_id, bob = _member(client, admin)

    added = client.put("/api/workspaces/alpha/members",
                       json={"user_id": bob_id, "role": "viewer"}, headers=owner)
    assert added.status_code == 200
    names = {m["username"] for m in client.get("/api/workspaces/alpha/members",
                                               headers=owner).json()}
    assert names == {"olivia", "bob"}
    assert client.delete(f"/api/workspaces/alpha/members/{bob_id}",
                         headers=owner).status_code == 200


def test_the_workspace_query_parameter_is_scoped_too(multi, client):
    """A route that names its workspace in the query, not the path, is checked
    the same way; otherwise the path rule would be a formality."""
    admin = _admin(client)
    client.post("/api/workspaces", json={"name": "alpha"}, headers=admin)
    _, bob = _member(client, admin)
    assert client.get("/api/tasks", params={"workspace": "alpha"},
                      headers=bob).status_code == 403
    assert client.get("/api/tasks", headers=bob).status_code == 200


def test_the_x_workspace_header_is_scoped_too(multi, client):
    admin = _admin(client)
    client.post("/api/workspaces", json={"name": "alpha"}, headers=admin)
    _, bob = _member(client, admin)
    assert client.get("/api/tasks", headers={**bob, "X-Workspace": "alpha"}
                      ).status_code == 403


def test_the_creator_of_a_workspace_becomes_its_owner(multi, client):
    """The contextvar the middleware sets has to reach the workspace store, or
    a user could create a workspace they cannot then open."""
    admin = _admin(client)
    _, bob = _member(client, admin)
    assert client.post("/api/workspaces", json={"name": "bobsplace"},
                       headers=bob).status_code == 200
    assert client.get("/api/workspaces/bobsplace", headers=bob).status_code == 200
    assert client.get("/api/workspaces/bobsplace/env", headers=bob).status_code == 200


def test_the_service_credential_acts_as_an_admin(multi, client):
    from common import identity
    headers = _bearer(identity.service_token())
    assert client.get("/api/health", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=headers).json()["kind"] == "service"


def test_ingest_stays_self_authenticating(multi, client):
    """An external service reporting its runs carries its own connection
    credential; requiring an operator session instead would hand every such
    service a key to the whole dashboard."""
    from common.auth import is_open_path
    assert is_open_path("POST", "/api/ingest/anything") is True


# ── the record owners ────────────────────────────────────────────────────────

def test_records_created_in_single_mode_belong_to_the_local_operator(single, client):
    created = client.post("/api/tasks", json={"title": "a task"})
    assert created.status_code in (200, 201), created.text
    from common import db
    owners = {r[0] for r in db.get_conn().execute(
        "SELECT created_by_user FROM tasks")}
    assert owners == {"local"}


def test_a_task_created_in_multi_mode_records_who_filed_it(multi, client):
    admin = _admin(client)
    me = client.get("/api/auth/me", headers=admin).json()
    created = client.post("/api/tasks", json={"title": "a task"}, headers=admin)
    assert created.status_code in (200, 201), created.text
    from common import db
    owners = {r[0] for r in db.get_conn().execute(
        "SELECT created_by_user FROM tasks")}
    assert owners == {me["id"]}
