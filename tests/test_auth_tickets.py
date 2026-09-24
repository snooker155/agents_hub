"""One-time auth tickets for streams, and preview ticket renewal.

``common/preview_tickets.py`` mints two kinds of signed ticket: the preview
proxy's (container/project, ten minutes, renewable) and the auth ticket an
EventSource or a WebSocket presents as ``?ticket=`` (one minute, one use).
``common/identity.py`` resolves ``?ticket=`` to the principal that minted it
and, in ``multi`` mode, no longer accepts ``?token=`` at all.

Run: ``python -m pytest tests/test_auth_tickets.py -q``
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from common import preview_tickets  # noqa: E402 - after the sys.path shim above

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


@pytest.fixture
def token_mode(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "token", raising=False)
    monkeypatch.setattr(settings, "api_token", "s3cret", raising=False)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin(client) -> tuple[dict, str]:
    response = client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    return _bearer(token), token


def _request(**query) -> SimpleNamespace:
    """The two attributes ``current_principal`` reads, without a server."""
    return SimpleNamespace(headers={}, query_params=query)


# ── mint / verify ────────────────────────────────────────────────────────────

def _user_principal(user_id="u1", scope=None):
    from common.auth import Principal
    return Principal(id=user_id, username="x", kind="user", via="session", scope=scope)


def test_an_auth_ticket_verifies_once():
    ticket = preview_tickets.mint_auth(_user_principal(scope=("ws1",)))
    found = preview_tickets.verify_auth(ticket)
    assert found["user"] == "u1" and found["kind"] == "user" and found["via"] == "session"
    assert found["scope"] == ["ws1"]
    assert preview_tickets.verify_auth(ticket) is None


def test_an_expired_auth_ticket_does_not_verify(monkeypatch):
    ticket = preview_tickets.mint_auth(_user_principal(), ttl_seconds=1)
    later = time.time() + 120
    monkeypatch.setattr(preview_tickets.time, "time", lambda: later)
    assert preview_tickets.verify_auth(ticket) is None


def test_consumed_nonces_are_pruned_after_expiry(monkeypatch):
    ticket = preview_tickets.mint_auth(_user_principal(), ttl_seconds=1)
    assert preview_tickets.verify_auth(ticket) is not None
    later = time.time() + 120
    monkeypatch.setattr(preview_tickets.time, "time", lambda: later)
    preview_tickets.verify_auth(preview_tickets.mint_auth(_user_principal()))
    assert all(exp >= later for exp in preview_tickets._consumed.values())


def test_kinds_do_not_cross():
    auth = preview_tickets.mint_auth(_user_principal())
    preview = preview_tickets.mint({"kind": "container", "id": "demo"}, principal_id="u1")
    assert preview_tickets.verify(auth) is None
    assert preview_tickets.verify_auth(preview) is None
    assert preview_tickets.verify_auth("garbage") is None


# ── resolving ?ticket= and ?token= ───────────────────────────────────────────

def test_multi_resolves_a_ticket_to_the_user_and_refuses_query_tokens(multi):
    from common import identity
    user = identity.create_user("streamer", PASSWORD)
    session = identity.open_session(user["id"])
    assert identity.current_principal(_request(token=session["token"])) is None

    principal = identity.current_principal(_request(ticket=preview_tickets.mint_auth(
        _user_principal(user["id"], scope=("ws1",)))))
    assert principal.id == user["id"] and principal.via == "ticket"
    assert principal.scope == ("ws1",)


def test_a_ticket_for_a_deleted_user_is_refused(multi):
    from common import identity
    user = identity.create_user("gone", PASSWORD)
    ticket = preview_tickets.mint_auth(_user_principal(user["id"]))
    identity.delete_user(user["id"])
    assert identity.current_principal(_request(ticket=ticket)) is None


def test_token_mode_keeps_query_tokens_and_takes_tickets(token_mode):
    from common import identity
    from common.auth import TOKEN_PRINCIPAL
    assert identity.current_principal(_request(token="s3cret")) == TOKEN_PRINCIPAL
    ticket = preview_tickets.mint_auth(TOKEN_PRINCIPAL)
    assert identity.current_principal(_request(ticket=ticket)) == TOKEN_PRINCIPAL
    assert identity.current_principal(_request(ticket=ticket)) is None


def test_service_ticket_in_multi(multi):
    from common import identity
    ticket = preview_tickets.mint_auth(identity.SERVICE_PRINCIPAL)
    assert identity.current_principal(_request(ticket=ticket)) == identity.SERVICE_PRINCIPAL


# ── over HTTP ────────────────────────────────────────────────────────────────

def test_post_auth_ticket_shape_and_use(multi, client):
    headers, token = _admin(client)
    assert client.post("/api/auth/ticket").status_code == 401
    body = client.post("/api/auth/ticket", headers=headers).json()
    assert set(body) == {"ticket", "expires_in"} and body["expires_in"] == 60

    assert client.get("/api/auth/me", params={"token": token}).status_code == 401
    me = client.get("/api/auth/me", params={"ticket": body["ticket"]})
    assert me.status_code == 200 and me.json()["username"] == "root"
    assert client.get("/api/auth/me", params={"ticket": body["ticket"]}).status_code == 401


def test_post_auth_ticket_in_token_mode(token_mode, client):
    assert client.get("/api/health", params={"token": "s3cret"}).status_code == 200
    body = client.post("/api/auth/ticket", headers=_bearer("s3cret")).json()
    assert client.get("/api/health", params={"ticket": body["ticket"]}).status_code == 200


def test_the_browser_websocket_takes_a_ticket(multi, client):
    from starlette.websockets import WebSocketDisconnect
    headers, token = _admin(client)
    # The service is not configured here, so an authenticated socket is
    # closed for that reason, an unauthenticated one before it gets there.
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect(f"/api/browser/sessions/s1/ws?token={token}"):
            pass
    assert refused.value.reason == "unauthorized"
    ticket = client.post("/api/auth/ticket", headers=headers).json()["ticket"]
    with pytest.raises(WebSocketDisconnect) as passed:
        with client.websocket_connect(f"/api/browser/sessions/s1/ws?ticket={ticket}"):
            pass
    assert passed.value.reason != "unauthorized"


# ── preview tickets: ten minutes, renewed ────────────────────────────────────

def test_preview_ttl_is_ten_minutes():
    assert preview_tickets.DEFAULT_TTL_SECONDS == 600
    ticket = preview_tickets.mint({"kind": "container", "id": "demo"}, principal_id="local")
    assert 590 < preview_tickets.expires_at(ticket) - time.time() <= 600


def test_renew_waits_for_half_life(monkeypatch):
    ticket = preview_tickets.mint({"kind": "project", "id": "p1"}, principal_id="local")
    assert preview_tickets.renew(ticket) is None
    assert preview_tickets.renew(ticket, force=True)
    later = time.time() + 400
    monkeypatch.setattr(preview_tickets.time, "time", lambda: later)
    fresh = preview_tickets.renew(ticket)
    assert preview_tickets.verify(fresh) == {"kind": "project", "id": "p1", "user": "local"}
    assert preview_tickets.expires_at(fresh) > preview_tickets.expires_at(ticket)


def _fake_project(monkeypatch, workspace=None):
    from routes import preview as preview_route
    monkeypatch.setattr(preview_route, "_resolve_target",
                        lambda kind, target_id: ("http://example.test", workspace))


def test_renew_route_by_ticket_and_by_target(multi, client, monkeypatch):
    _fake_project(monkeypatch)
    headers, _ = _admin(client)
    admin_id = client.get("/api/auth/me", headers=headers).json()["id"]
    ticket = preview_tickets.mint({"kind": "project", "id": "p1"}, principal_id=admin_id)
    body = client.post("/api/preview/tickets/renew", json={"ticket": ticket},
                       headers=headers).json()
    assert body["url"].startswith("/preview/") and body["expires_in"] == 600
    assert body["expires_at"] > time.time() + 590

    by_target = client.post("/api/preview/tickets/renew",
                            json={"kind": "project", "project_id": "p1"}, headers=headers)
    assert by_target.status_code == 200
    assert client.post("/api/preview/tickets/renew", json={"ticket": "expired"},
                       headers=headers).status_code == 403


def test_renew_route_refuses_someone_elses_ticket(multi, client, monkeypatch):
    _fake_project(monkeypatch)
    admin, _ = _admin(client)
    client.post("/api/auth/users", json={"username": "bob", "password": PASSWORD}, headers=admin)
    bob = _bearer(client.post("/api/auth/login",
                              json={"username": "bob", "password": PASSWORD}).json()["token"])
    admin_id = client.get("/api/auth/me", headers=admin).json()["id"]
    ticket = preview_tickets.mint({"kind": "project", "id": "p1"}, principal_id=admin_id)
    assert client.post("/api/preview/tickets/renew", json={"ticket": ticket},
                       headers=bob).status_code == 403
