"""Stage 3 of identity: the foundation the OIDC, SCIM, audit, API key and
secrets work stands on.

The pure part (a scoped principal in the decision table) first, then the
stores: an account linked to an external identity, a session with metadata
that can be listed and revoked, the login throttle, membership rows that
remember who granted them, and the accounts route reporting the new fields.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from common.auth import (
    MULTI,
    Principal,
    ROLE_ADMIN,
    ROLE_MEMBER,
    WS_EDITOR,
    authorize,
    auth_headers,
    is_open_path,
)

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

PASSWORD = "hunter2-but-longer"


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    monkeypatch.setattr(settings, "auth_local_passwords", True, raising=False)
    monkeypatch.setattr(settings, "auth_login_max_attempts", 3, raising=False)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


# ── the pure part ────────────────────────────────────────────────────────────

def test_a_scoped_key_never_reaches_outside_its_scope_even_for_an_admin():
    scoped_admin = Principal(id="u1", username="root", role=ROLE_ADMIN, via="api_key",
                             scope=("w1",))
    assert authorize(MULTI, principal=scoped_admin, method="GET",
                     path="/api/workspaces/w1/files", workspace="w1")
    assert not authorize(MULTI, principal=scoped_admin, method="GET",
                         path="/api/workspaces/w2/files", workspace="w2")
    # A request naming no workspace is not narrowed: there is nothing to
    # compare the scope against.
    assert authorize(MULTI, principal=scoped_admin, method="GET", path="/api/agents")


def test_a_scoped_member_still_needs_membership_inside_the_scope():
    member = Principal(id="u1", username="bob", role=ROLE_MEMBER, via="api_key", scope=("w1",))
    assert not authorize(MULTI, principal=member, method="POST",
                         path="/api/workspaces/w1/files", workspace="w1", membership_role=None)
    assert authorize(MULTI, principal=member, method="POST",
                     path="/api/workspaces/w1/files", workspace="w1", membership_role=WS_EDITOR)


def test_the_sso_routes_and_scim_are_open_paths():
    assert is_open_path("GET", "/api/auth/oidc/start")
    assert is_open_path("GET", "/api/auth/oidc/callback")
    assert is_open_path("POST", "/scim/v2/Users")
    assert not is_open_path("GET", "/api/auth/keys")


def test_a_personal_key_in_the_environment_is_presented_last(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_API_TOKEN", raising=False)
    monkeypatch.delenv("AGENTS_HUB_SERVICE_TOKEN", raising=False)
    monkeypatch.setenv("AGENTS_HUB_API_KEY", "ahk_personal")
    assert auth_headers() == {"Authorization": "Bearer ahk_personal"}
    monkeypatch.setenv("AGENTS_HUB_SERVICE_TOKEN", "svc")
    assert auth_headers() == {"Authorization": "Bearer svc"}


# ── external identities ──────────────────────────────────────────────────────

def test_an_external_identity_creates_an_account_without_a_password(multi):
    from common import identity
    user = identity.upsert_external_user("https://idp", "sub-1", username="alice",
                                         email="alice@example.com", display_name="Alice")
    assert user["source"] == identity.SOURCE_OIDC
    assert user["has_password"] is False
    assert user["email"] == "alice@example.com"
    # No password: a password login is impossible, whatever is typed.
    assert identity.login("alice", "anything") is None
    # The same subject comes back to the same account.
    again = identity.upsert_external_user("https://idp", "sub-1", username="alice2")
    assert again["id"] == user["id"]


def test_link_by_email_attaches_to_the_existing_local_account(multi):
    from common import identity
    local = identity.create_user("carol", PASSWORD, email="carol@example.com")
    linked = identity.upsert_external_user("https://idp", "sub-2", username="carol.x",
                                           email="carol@example.com")
    assert linked["id"] == local["id"]
    assert linked["external_subject"] == "sub-2"
    # The local password keeps working after linking.
    assert identity.login("carol", PASSWORD) is not None


def test_a_taken_username_gets_a_suffix_when_link_by_email_is_off(multi, monkeypatch):
    from common import identity
    from common.config import settings
    monkeypatch.setattr(settings, "auth_oidc_link_by_email", False, raising=False)
    identity.create_user("dave", PASSWORD)
    external = identity.upsert_external_user("https://idp", "sub-3", username="dave")
    assert external["username"] == "dave-2"


# ── sessions ─────────────────────────────────────────────────────────────────

def test_sessions_carry_metadata_and_can_be_revoked_one_by_one(multi):
    from common import identity
    user = identity.create_user("erin", PASSWORD)
    first = identity.login("erin", PASSWORD, ip="10.0.0.1", user_agent="curl")
    second = identity.open_session(user["id"], kind=identity.SESSION_OIDC, ip="10.0.0.2")
    listed = identity.list_sessions(user["id"])
    assert {s["id"] for s in listed} == {first["session_id"], second["session_id"]}
    assert {s["kind"] for s in listed} == {"password", "oidc"}
    assert identity.revoke_session(user["id"], first["session_id"])
    assert identity.user_for_session(first["token"]) is None
    assert identity.user_for_session(second["token"])["id"] == user["id"]
    assert identity.revoke_other_sessions(user["id"], second["session_id"]) == 0
    assert identity.revoke_other_sessions(user["id"], None) == 1


def test_oidc_sessions_are_shorter(multi, monkeypatch):
    from datetime import datetime
    from common import identity
    from common.config import settings
    monkeypatch.setattr(settings, "auth_oidc_session_hours", 2, raising=False)
    monkeypatch.setattr(settings, "auth_session_hours", 100, raising=False)
    user = identity.create_user("frank", PASSWORD)
    short = identity.open_session(user["id"], kind=identity.SESSION_OIDC)
    long = identity.open_session(user["id"])
    assert datetime.fromisoformat(short["expires_at"]) < datetime.fromisoformat(long["expires_at"])


def test_disabling_an_account_drops_its_sessions(multi):
    from common import identity
    identity.create_user("root", PASSWORD, role=ROLE_ADMIN)
    user = identity.create_user("gina", PASSWORD)
    session = identity.login("gina", PASSWORD)
    identity.update_user(user["id"], disabled=True)
    assert identity.user_for_session(session["token"]) is None


# ── the login throttle ───────────────────────────────────────────────────────

def test_too_many_failed_logins_throttle_the_account(multi):
    from common import identity
    identity.create_user("hank", PASSWORD)
    for _ in range(3):
        assert identity.login("hank", "wrong", ip="1.2.3.4") is None
    with pytest.raises(identity.LoginThrottled):
        identity.login("hank", PASSWORD, ip="1.2.3.4")
    # Another account from the same address is not locked by three failures.
    identity.create_user("ivy", PASSWORD)
    assert identity.login("ivy", PASSWORD, ip="1.2.3.4") is not None


def test_a_successful_login_clears_the_counter(multi):
    from common import identity
    identity.create_user("jack", PASSWORD)
    identity.login("jack", "wrong")
    identity.login("jack", "wrong")
    assert identity.login("jack", PASSWORD) is not None
    assert not identity.login_throttled("jack")


def test_the_login_route_answers_429_when_throttled(multi, client):
    client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    for _ in range(3):
        client.post("/api/auth/login", json={"username": "root", "password": "nope"})
    response = client.post("/api/auth/login", json={"username": "root", "password": PASSWORD})
    assert response.status_code == 429


def test_passwords_off_leaves_the_door_open_to_admins_only(multi, monkeypatch):
    from common import identity
    from common.config import settings
    identity.create_user("root", PASSWORD, role=ROLE_ADMIN)
    identity.create_user("kate", PASSWORD)
    monkeypatch.setattr(settings, "auth_local_passwords", False, raising=False)
    assert identity.login("kate", PASSWORD) is None
    assert identity.login("root", PASSWORD) is not None


# ── membership source ────────────────────────────────────────────────────────

def test_a_membership_remembers_who_granted_it(multi):
    from common import identity
    user = identity.create_user("liam", PASSWORD)
    identity.set_member("w1", user["id"], "viewer", source="group")
    assert identity.list_members("w1")[0]["source"] == "group"
    # A manual grant on top marks the row manual, so the group's later
    # disappearance does not take the membership away.
    identity.set_member("w1", user["id"], "editor")
    assert identity.list_members("w1")[0]["source"] == "manual"


# ── the accounts route ───────────────────────────────────────────────────────

def test_me_reports_the_new_fields_and_the_credential(multi, client):
    boot = client.post("/api/auth/bootstrap",
                       json={"username": "root", "password": PASSWORD}).json()
    headers = {"Authorization": f"Bearer {boot['token']}"}
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["via"] == "session"
    assert me["credential_id"] == boot["session_id"]
    assert me["has_password"] is True
    assert me["source"] == "local"
    assert me["groups"] == []
    assert me["scope"] is None


def test_an_admin_may_create_an_account_without_a_password(multi, client):
    boot = client.post("/api/auth/bootstrap",
                       json={"username": "root", "password": PASSWORD}).json()
    headers = {"Authorization": f"Bearer {boot['token']}"}
    created = client.post("/api/auth/users", json={"username": "mia", "email": "mia@x.io"},
                          headers=headers)
    assert created.status_code == 200, created.text
    assert created.json()["has_password"] is False
    assert created.json()["email"] == "mia@x.io"
    assert client.post("/api/auth/login",
                       json={"username": "mia", "password": ""}).status_code == 401


def test_logins_are_audited(multi, client):
    from common import audit
    client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    client.post("/api/auth/login", json={"username": "root", "password": "nope"})
    client.post("/api/auth/login", json={"username": "root", "password": PASSWORD})
    rows = audit.query(action="auth.login")["items"]
    assert [r["result"] for r in rows] == ["ok", "denied"]
    assert rows[0]["actor_name"] == "root"
    # The middleware logged the write requests too, without the credential.
    posted = audit.query(action="http.post")["items"]
    assert posted and all("token" not in (r["details"] or {}) for r in posted)
