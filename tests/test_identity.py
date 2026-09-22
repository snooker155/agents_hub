"""The three auth modes: the decision table, and the stores behind it.

Split the way the code is. The first half calls the pure predicates in
``common.auth`` with plain values, no request and no database, so the whole
mode × role × method matrix is readable in one place and cannot drift as the
middleware is refactored around it. The second half exercises the parts that
genuinely need state: password hashing, sessions, membership and the one-shot
bootstrap.
"""
from __future__ import annotations

import pytest

from common.auth import (
    LOCAL_PRINCIPAL,
    MULTI,
    Principal,
    ROLE_ADMIN,
    ROLE_MEMBER,
    SINGLE,
    TOKEN,
    TOKEN_PRINCIPAL,
    WS_EDITOR,
    WS_OWNER,
    WS_VIEWER,
    authorize,
    effective_auth_mode,
    is_open_path,
    required_workspace_role,
    role_satisfies,
    workspace_from_request,
)

MEMBER = Principal(id="u1", username="bob", role=ROLE_MEMBER)
ADMIN = Principal(id="u2", username="root", role=ROLE_ADMIN)


# ── which mode is actually in force ──────────────────────────────────────────

def test_default_is_the_single_operator():
    assert effective_auth_mode(None, "") == SINGLE
    assert effective_auth_mode("", "") == SINGLE


def test_a_configured_token_alone_still_means_token_mode():
    """The compatibility rule this whole feature has to preserve: a deployment
    that only ever set AGENTS_HUB_API_TOKEN keeps behaving exactly as before."""
    assert effective_auth_mode(None, "s3cret") == TOKEN
    assert effective_auth_mode(SINGLE, "s3cret") == TOKEN


def test_an_explicit_mode_wins_over_the_compatibility_rule():
    assert effective_auth_mode(MULTI, "s3cret") == MULTI
    assert effective_auth_mode(TOKEN, "") == TOKEN


def test_a_typo_falls_back_to_single_rather_than_failing_to_start():
    assert effective_auth_mode("multiuser", "") == SINGLE


# ── what needs no principal at all ───────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("OPTIONS", "/api/tasks"),          # CORS preflight
    ("GET", "/"),                       # not /api
    ("POST", "/api/ingest/runs"),       # a connection's own credential
    ("GET", "/api/external/abc/state"), # token in the path
    ("GET", "/api/auth/mode"),          # the frontend has to render something
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/bootstrap"),
])
def test_open_paths(method, path):
    assert is_open_path(method, path) is True


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/tasks"),
    ("GET", "/api/auth/users"),
    ("POST", "/api/auth/mode"),      # only the GET of it is public
    ("GET", "/api/ingestion-report"),  # prefix match must be on a boundary
])
def test_closed_paths(method, path):
    assert is_open_path(method, path) is False


# ── which workspace a request is about ───────────────────────────────────────

def test_workspace_comes_from_the_path_first():
    assert workspace_from_request(path="/api/workspaces/alpha/files",
                                  query_workspace="beta") == "alpha"


def test_workspace_from_the_query_parameter():
    assert workspace_from_request(path="/api/tasks", query_workspace="beta") == "beta"


def test_workspace_from_the_header():
    assert workspace_from_request(path="/api/chats", header_workspace="gamma") == "gamma"


def test_a_request_may_name_no_workspace():
    assert workspace_from_request(path="/api/health") is None
    assert workspace_from_request(path="/api/workspaces") is None


def test_a_route_under_workspaces_is_not_a_workspace_name():
    """POST /api/workspaces/attach registers a directory; it is not a request
    against a workspace called "attach"."""
    assert workspace_from_request(path="/api/workspaces/attach") is None


# ── which role a request needs ───────────────────────────────────────────────

@pytest.mark.parametrize("method,path,expected", [
    ("GET", "/api/workspaces/alpha", WS_VIEWER),
    ("HEAD", "/api/workspaces/alpha/files", WS_VIEWER),
    ("POST", "/api/workspaces/alpha/agents", WS_EDITOR),
    ("PUT", "/api/workspaces/alpha/instructions", WS_EDITOR),
    ("PATCH", "/api/tasks/1", WS_EDITOR),
    ("DELETE", "/api/workspaces/alpha/files", WS_EDITOR),   # a file, not the workspace
    ("DELETE", "/api/workspaces/alpha", WS_OWNER),          # the workspace itself
    ("GET", "/api/workspaces/alpha/policy", WS_OWNER),
    ("PUT", "/api/workspaces/alpha/env", WS_OWNER),
    ("GET", "/api/workspaces/alpha/settings-overrides", WS_OWNER),
    ("GET", "/api/workspaces/alpha/members", WS_OWNER),
])
def test_required_workspace_role(method, path, expected):
    assert required_workspace_role(method, path) == expected


def test_roles_are_ordered():
    assert role_satisfies(WS_OWNER, WS_EDITOR) is True
    assert role_satisfies(WS_EDITOR, WS_EDITOR) is True
    assert role_satisfies(WS_VIEWER, WS_EDITOR) is False
    assert role_satisfies(None, WS_VIEWER) is False


# ── the matrix ───────────────────────────────────────────────────────────────

def _allowed(mode, **kwargs):
    kwargs.setdefault("method", "GET")
    kwargs.setdefault("path", "/api/tasks")
    return authorize(mode, **kwargs)


def test_single_mode_refuses_nothing():
    for method in ("GET", "POST", "DELETE"):
        assert _allowed(SINGLE, principal=None, method=method,
                        path="/api/workspaces/alpha") is True


def test_token_mode_needs_a_principal_and_asks_nothing_else():
    assert _allowed(TOKEN, principal=None) is False
    assert _allowed(TOKEN, principal=TOKEN_PRINCIPAL) is True
    # No membership anywhere, and it still writes: one operator, one workspace
    # set, nothing to be a member of.
    assert _allowed(TOKEN, principal=TOKEN_PRINCIPAL, method="DELETE",
                    path="/api/workspaces/alpha", workspace="alpha") is True


def test_multi_mode_refuses_the_unauthenticated():
    assert _allowed(MULTI, principal=None) is False


def test_multi_mode_lets_any_user_at_what_names_no_workspace():
    assert _allowed(MULTI, principal=MEMBER, method="POST",
                    path="/api/chat/message") is True


@pytest.mark.parametrize("held,method,expected", [
    (None, "GET", False),        # not a member at all
    (WS_VIEWER, "GET", True),
    (WS_VIEWER, "POST", False),
    (WS_EDITOR, "GET", True),
    (WS_EDITOR, "POST", True),
    (WS_EDITOR, "DELETE", True),  # a file under the workspace
    (WS_OWNER, "POST", True),
])
def test_multi_mode_membership_matrix(held, method, expected):
    assert _allowed(MULTI, principal=MEMBER, method=method,
                    path="/api/workspaces/alpha/files", workspace="alpha",
                    membership_role=held) is expected


@pytest.mark.parametrize("held,expected", [
    (WS_EDITOR, False), (WS_OWNER, True),
])
def test_only_an_owner_deletes_a_workspace(held, expected):
    assert _allowed(MULTI, principal=MEMBER, method="DELETE",
                    path="/api/workspaces/alpha", workspace="alpha",
                    membership_role=held) is expected


@pytest.mark.parametrize("held,expected", [
    (WS_EDITOR, False), (WS_OWNER, True),
])
def test_only_an_owner_reads_the_env_block(held, expected):
    assert _allowed(MULTI, principal=MEMBER, method="GET",
                    path="/api/workspaces/alpha/env", workspace="alpha",
                    membership_role=held) is expected


def test_an_admin_bypasses_membership():
    assert _allowed(MULTI, principal=ADMIN, method="DELETE",
                    path="/api/workspaces/alpha", workspace="alpha",
                    membership_role=None) is True


def test_the_service_credential_acts_as_an_admin():
    from common.identity import SERVICE_PRINCIPAL
    assert _allowed(MULTI, principal=SERVICE_PRINCIPAL, method="POST",
                    path="/api/stream/notify", workspace="alpha",
                    membership_role=None) is True


def test_the_local_operator_is_an_admin():
    assert LOCAL_PRINCIPAL.is_admin is True


# ── passwords, sessions, users, membership ───────────────────────────────────

@pytest.fixture
def multi(monkeypatch):
    """Put the settings object in multi mode for one test."""
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", MULTI, raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    import common.identity as identity
    return identity


def test_a_password_is_never_stored(multi):
    record = multi.hash_password("hunter2hunter2")
    assert "hunter2hunter2" not in record["password_hash"]
    assert record["password_iterations"] >= 200_000
    assert multi.verify_password("hunter2hunter2", **record) is True
    assert multi.verify_password("hunter3hunter3", **record) is False


def test_two_users_with_the_same_password_get_different_hashes(multi):
    a = multi.hash_password("same-password")
    b = multi.hash_password("same-password")
    assert a["password_salt"] != b["password_salt"]
    assert a["password_hash"] != b["password_hash"]


def test_bootstrap_happens_exactly_once(multi):
    assert multi.bootstrap_required() is True
    multi.create_first_admin("root", "hunter2hunter2")
    assert multi.bootstrap_required() is False
    with pytest.raises(ValueError):
        multi.create_first_admin("second", "hunter2hunter2")


def test_bootstrap_is_refused_outside_multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", SINGLE, raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    import common.identity as identity
    with pytest.raises(ValueError):
        identity.create_first_admin("root", "hunter2hunter2")


def test_login_opens_a_session_and_logout_closes_it(multi):
    multi.create_first_admin("root", "hunter2hunter2")
    session = multi.login("root", "hunter2hunter2")
    assert session and session["token"]
    assert multi.user_for_session(session["token"])["username"] == "root"
    assert multi.logout(session["token"]) is True
    assert multi.user_for_session(session["token"]) is None


def test_a_wrong_password_opens_nothing(multi):
    multi.create_first_admin("root", "hunter2hunter2")
    assert multi.login("root", "wrong-password") is None
    assert multi.login("nobody", "hunter2hunter2") is None


def test_an_expired_session_is_refused_and_dropped(multi, monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_session_hours", 1, raising=False)
    multi.create_first_admin("root", "hunter2hunter2")
    token = multi.login("root", "hunter2hunter2")["token"]
    from common import db
    with db.transaction() as conn:
        conn.execute("UPDATE auth_sessions SET expires_at = '2000-01-01T00:00:00+00:00'")
    assert multi.user_for_session(token) is None
    assert db.get_conn().execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0


def test_resetting_a_password_drops_the_sessions_it_protected(multi):
    admin = multi.create_first_admin("root", "hunter2hunter2")
    token = multi.login("root", "hunter2hunter2")["token"]
    multi.set_password(admin["id"], "brand-new-password")
    assert multi.user_for_session(token) is None
    assert multi.login("root", "brand-new-password") is not None


def test_the_last_admin_cannot_be_demoted_or_deleted(multi):
    admin = multi.create_first_admin("root", "hunter2hunter2")
    with pytest.raises(ValueError):
        multi.update_user(admin["id"], role="member")
    with pytest.raises(ValueError):
        multi.delete_user(admin["id"])
    second = multi.create_user("second", "hunter2hunter2", role="admin")
    assert multi.update_user(admin["id"], role="member")["role"] == "member"
    # ...and now `second` is the last one, so the rule has simply moved.
    with pytest.raises(ValueError):
        multi.delete_user(second["id"])
    assert multi.delete_user(admin["id"]) is True


def test_usernames_are_unique_and_case_folded(multi):
    multi.create_user("bob", "hunter2hunter2")
    with pytest.raises(ValueError):
        multi.create_user("BOB", "hunter2hunter2")
    assert multi.get_user_by_username("Bob")["username"] == "bob"


def test_membership_is_read_back_and_removable(multi):
    bob = multi.create_user("bob", "hunter2hunter2")
    multi.set_member("alpha", bob["id"], WS_EDITOR)
    assert multi.membership_role("alpha", bob["id"]) == WS_EDITOR
    assert multi.workspaces_for_user(bob["id"]) == ["alpha"]
    multi.set_member("alpha", bob["id"], WS_VIEWER)
    assert multi.membership_role("alpha", bob["id"]) == WS_VIEWER
    assert multi.remove_member("alpha", bob["id"]) is True
    assert multi.membership_role("alpha", bob["id"]) is None


def test_a_workspace_may_not_lose_its_last_owner(multi):
    bob = multi.create_user("bob", "hunter2hunter2")
    multi.set_member("alpha", bob["id"], WS_OWNER)
    with pytest.raises(ValueError):
        multi.remove_member("alpha", bob["id"])


def test_deleting_a_user_takes_their_memberships_with_them(multi):
    admin = multi.create_first_admin("root", "hunter2hunter2")
    bob = multi.create_user("bob", "hunter2hunter2")
    multi.set_member("alpha", bob["id"], WS_EDITOR)
    assert multi.delete_user(bob["id"]) is True
    assert multi.membership_role("alpha", bob["id"]) is None
    assert multi.get_user(admin["id"]) is not None


def test_the_service_token_is_stable_and_compared_in_constant_time(multi):
    first = multi.service_token()
    assert first and multi.service_token() == first
    assert multi.is_service_token(first) is True
    assert multi.is_service_token("not-it") is False
    assert multi.is_service_token("") is False


def test_the_service_token_is_the_api_token_when_one_is_configured(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", MULTI, raising=False)
    monkeypatch.setattr(settings, "api_token", "configured-token", raising=False)
    import common.identity as identity
    assert identity.service_token() == "configured-token"


def test_the_current_user_defaults_to_the_local_operator(multi):
    from common.auth import LOCAL_OPERATOR_ID
    assert multi.current_user_id() == LOCAL_OPERATOR_ID
    token = multi.set_current_user("u9")
    try:
        assert multi.current_user_id() == "u9"
    finally:
        multi.reset_current_user(token)
    assert multi.current_user_id() == LOCAL_OPERATOR_ID
