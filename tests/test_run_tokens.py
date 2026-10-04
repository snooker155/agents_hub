"""
Run tokens (common/run_tokens.py): the credential a run's own process calls
back with reaches only the relay routes (common/auth.py RELAY_ROUTES), and
people no longer reach those routes at all.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from common import auth, db, run_tokens
from common.auth import MULTI, SINGLE, TOKEN, Principal

PASSWORD = "hunter2-but-longer"
SHARED = "s3cret-shared-token"


@pytest.fixture(autouse=True)
def _fresh_cache():
    run_tokens._cache.clear()
    yield
    run_tokens._cache.clear()


# ── the store ────────────────────────────────────────────────────────────────

def test_only_a_hash_is_stored_and_the_token_resolves():
    token = run_tokens.mint(run_id="run-1", session_id="sess-1", workspace="acme")
    assert token.startswith(run_tokens.PREFIX)
    rows = db.get_conn().execute("SELECT * FROM run_tokens").fetchall()
    assert len(rows) == 1 and token not in str(dict(rows[0]))
    row = run_tokens.resolve(token)
    assert row["run_id"] == "run-1" and row["workspace"] == "acme"
    assert run_tokens.resolve(token + "x") is None
    assert run_tokens.resolve("not-a-run-token") is None


def _set_expiry(token: str, when: datetime) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE run_tokens SET expires_at = ? WHERE token_hash = ?",
                     (when.isoformat(), run_tokens._hash(token)))
    run_tokens._cache.clear()


def test_an_expired_token_is_refused_and_a_used_one_slides():
    now = datetime.now(timezone.utc)
    token = run_tokens.mint(run_id="run-1")
    _set_expiry(token, now - timedelta(seconds=1))
    assert run_tokens.resolve(token) is None
    # Less than half the TTL left: using it pushes the expiry out again.
    _set_expiry(token, now + timedelta(seconds=60))
    row = run_tokens.resolve(token)
    assert datetime.fromisoformat(row["expires_at"]) > now + timedelta(hours=1)


def test_closing_the_run_retires_its_token_after_a_grace():
    from managers import run_manager
    token = run_tokens.mint(run_id="run-close")
    other = run_tokens.mint(run_id="run-other")
    run_manager.open_run("run-close", "swe_agent", link_to_session=False, status="running")
    run_manager.close_run("run-close", status="completed", exit_code=0)
    row = run_tokens.resolve(token)
    assert row is not None and row["retired_at"]
    left = (datetime.fromisoformat(row["expires_at"]) - datetime.now(timezone.utc)).total_seconds()
    assert 0 < left <= run_tokens.GRACE_SECONDS
    # A retired token no longer slides, and the grace ends it.
    _set_expiry(token, datetime.now(timezone.utc) - timedelta(seconds=1))
    assert run_tokens.resolve(token) is None
    assert run_tokens.resolve(other)["retired_at"] is None


def test_a_carrier_restart_retires_the_old_token_at_once():
    token = run_tokens.mint(kind="instance", instance_id="inst-1")
    assert run_tokens.retire_for_instance("inst-1") == 1
    assert run_tokens.resolve(token) is None


def test_prune_drops_tokens_long_expired():
    token = run_tokens.mint(run_id="run-old")
    _set_expiry(token, datetime.now(timezone.utc) - timedelta(days=8))
    assert run_tokens.prune(7) == 1


# ── what goes into a run's environment ───────────────────────────────────────

def test_the_hub_wide_credentials_never_reach_a_run(monkeypatch):
    monkeypatch.setattr("common.identity.current_mode", lambda: TOKEN)
    env = {"AGENTS_HUB_API_TOKEN": SHARED, "AGENTS_HUB_SERVICE_TOKEN": "svc",
           "AGENTS_HUB_API_KEY": "ahk_x", "OTHER": "kept"}
    run_tokens.mint_for_env(env, run_id="run-1")
    assert set(env) == {"OTHER", run_tokens.ENV}
    assert run_tokens.resolve(env[run_tokens.ENV])["run_id"] == "run-1"


def test_single_mode_mints_nothing_but_still_strips(monkeypatch):
    monkeypatch.setattr("common.identity.current_mode", lambda: SINGLE)
    env = {"AGENTS_HUB_API_TOKEN": SHARED}
    run_tokens.mint_for_env(env, run_id="run-1")
    assert env == {}
    assert db.get_conn().execute("SELECT COUNT(*) AS n FROM run_tokens").fetchone()["n"] == 0


def test_auth_headers_prefer_the_run_token(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_API_TOKEN", SHARED)
    monkeypatch.setenv(run_tokens.ENV, "ahrun_abc")
    assert auth.auth_headers() == {"Authorization": "Bearer ahrun_abc"}


# ── the decision ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path,expected", [
    ("POST", "/api/run-state/runs/r1/heartbeat", True),
    ("GET", "/api/run-state/runs/r1", True),
    ("POST", "/api/run-state/tasks/t1/delegate", True),
    ("POST", "/api/sessions/s1/events", True),
    ("POST", "/api/instances/i1/events", True),
    ("POST", "/api/stream/notify", True),
    ("POST", "/api/stream/publish", True),
    ("POST", "/api/plan/notifications/publish", True),
    ("GET", "/api/sessions/s1/events", False),
    ("POST", "/api/sessions/s1/events/x", False),
    ("GET", "/api/stream", False),
    ("PUT", "/api/settings", False),
    ("GET", "/api/run-statefoo", False),
])
def test_the_relay_routes(method, path, expected):
    assert auth.is_relay_route(method, path) is expected


RUN = Principal(id="service", username="run:r1", role="admin", kind="run", via="run_token")
MEMBER = Principal(id="u1", username="bob", role="member", kind="user")
ADMIN = Principal(id="u0", username="root", role="admin", kind="user")
SHARED_P = auth.TOKEN_PRINCIPAL
SERVICE = Principal(id="service", username="service", role="admin", kind="service")


@pytest.mark.parametrize("mode", [TOKEN, MULTI])
def test_a_run_token_reaches_its_relays_and_nothing_else(mode):
    assert auth.authorize(mode, principal=RUN, method="POST", path="/api/run-state/runs/r1/close")
    assert auth.authorize(mode, principal=RUN, method="POST", path="/api/stream/notify")
    for method, path in (("GET", "/api/workspaces"), ("PUT", "/api/settings"),
                         ("POST", "/api/connectors/jira/config"), ("GET", "/api/auth/users"),
                         ("POST", "/api/tool-approvals/appr_1")):
        assert not auth.authorize(mode, principal=RUN, method=method, path=path), path


def test_people_do_not_reach_the_relays_in_multi_mode():
    for who in (MEMBER, ADMIN):
        assert not auth.authorize(MULTI, principal=who, method="POST",
                                  path="/api/run-state/tasks/t1/delegate")
        assert not auth.authorize(MULTI, principal=who, method="POST", path="/api/stream/publish")
        assert auth.authorize(MULTI, principal=who, method="GET", path="/api/tasks")
    assert auth.authorize(MULTI, principal=SERVICE, method="POST", path="/api/run-state/runs/r/close")
    assert auth.authorize(TOKEN, principal=SHARED_P, method="POST", path="/api/run-state/runs/r/close")
    assert auth.authorize(SINGLE, principal=None, method="POST", path="/api/run-state/runs/r/close")


# ── through the app ──────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_token_mode_through_the_app(monkeypatch, client):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", SHARED, raising=False)
    token = run_tokens.mint(run_id="run-app")
    relay = client.post("/api/stream/notify", json={"resource": "tasks", "meta": {}},
                        headers=_bearer(token))
    assert relay.status_code == 200, relay.text
    assert client.get("/api/workspaces", headers=_bearer(token)).status_code == 403
    assert client.get("/api/settings", headers=_bearer(token)).status_code == 403
    assert client.get("/api/workspaces", headers=_bearer(SHARED)).status_code == 200
    # A retired token past its grace is just a wrong credential.
    run_tokens.retire_for_run("run-app", grace=0)
    assert client.post("/api/stream/notify", json={"resource": "tasks", "meta": {}},
                       headers=_bearer(token)).status_code == 401


def test_multi_mode_through_the_app(monkeypatch, client):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    boot = client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    assert boot.status_code == 200, boot.text
    admin = _bearer(boot.json()["token"])
    # An administrator's session is a person: the run-state surface is not theirs.
    assert client.post("/api/run-state/runs/r1/heartbeat", json={}, headers=admin).status_code == 403
    token = run_tokens.mint(run_id="run-multi")
    assert client.post("/api/stream/notify", json={"resource": "tasks", "meta": {}},
                       headers=_bearer(token)).status_code == 200
    assert client.get("/api/auth/users", headers=_bearer(token)).status_code == 403


def test_a_container_delegates_as_its_own_run(monkeypatch, client):
    # The body says service_agent in another workspace; the token says which
    # run this is, and that run's agent and workspace are what count.
    from common.config import settings
    from managers import run_manager
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", SHARED, raising=False)
    run_manager.open_run("run-deleg", "helper", link_to_session=False, status="running",
                         workspace="team-a")
    token = run_tokens.mint(run_id="run-deleg", workspace="team-a")
    seen = {}
    monkeypatch.setattr("tasks.delegate.launch_delegation",
                        lambda task_id, req: seen.update(req) or {"ok": True})
    resp = client.post("/api/run-state/tasks/t1/delegate", headers=_bearer(token), json={
        "agent_id": "writer", "input": "x", "workspace": "team-b",
        "caller_agent_id": "service_agent"})
    assert resp.status_code == 200, resp.text
    assert seen["workspace"] == "team-a" and seen["caller_agent_id"] == "helper"
