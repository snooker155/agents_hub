"""Feature 7a: preview through the hub.

``common/preview_tickets.py`` mints and verifies the short-lived, signed
ticket an iframe uses in place of a Bearer header; ``dashboard/backend/routes/
preview.py`` mints tickets over ``/api/preview/tickets`` (the standard
identity guard applies, plus a membership check in ``multi`` mode) and proxies
the actual page under ``/preview/<ticket>/...`` (an open path, on purpose: the
ticket in the path is the credential there).

The proxy's own HTTP client is built by ``preview._client_factory``, monkey-
patched here to run over ``httpx.MockTransport`` so no real network is ever
touched.

Run: ``python -m pytest tests/test_preview_proxy.py -q``
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx
import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from common import preview_tickets  # noqa: E402 - after the sys.path shim above


# ── mint / verify: pure, no server ──────────────────────────────────────────

def test_a_minted_ticket_verifies_and_carries_its_target():
    ticket = preview_tickets.mint({"kind": "container", "id": "demo"}, principal_id="local")
    target = preview_tickets.verify(ticket)
    assert target == {"kind": "container", "id": "demo", "user": "local"}


def test_an_expired_ticket_does_not_verify(monkeypatch):
    ticket = preview_tickets.mint({"kind": "project", "id": "p1"}, principal_id="local",
                                  ttl_seconds=1)
    later = time.time() + 3600
    monkeypatch.setattr(preview_tickets.time, "time", lambda: later)
    assert preview_tickets.verify(ticket) is None


def test_a_tampered_ticket_does_not_verify():
    ticket = preview_tickets.mint({"kind": "container", "id": "demo"}, principal_id="local")
    body, _, mac = ticket.rpartition(".")
    tampered = f"{body}x.{mac}"
    assert preview_tickets.verify(tampered) is None


def test_garbage_does_not_verify():
    assert preview_tickets.verify("") is None
    assert preview_tickets.verify("not-a-ticket") is None
    assert preview_tickets.verify("a.b.c") is None


def test_a_ticket_for_a_deleted_user_does_not_verify_in_multi_mode(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    from common import identity
    user = identity.create_user("previewer", "hunter2-but-longer")
    ticket = preview_tickets.mint({"kind": "container", "id": "demo"}, principal_id=user["id"])
    assert preview_tickets.verify(ticket) is not None
    identity.delete_user(user["id"])
    assert preview_tickets.verify(ticket) is None


# ── POST /api/preview/tickets ────────────────────────────────────────────────

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
                           json={"username": "root", "password": "hunter2-but-longer"})
    assert response.status_code == 200, response.text
    return _bearer(response.json()["token"])


def _member(client, admin_headers, username="bob") -> dict:
    created = client.post("/api/auth/users",
                          json={"username": username, "password": "hunter2-but-longer"},
                          headers=admin_headers)
    assert created.status_code == 200, created.text
    session = client.post("/api/auth/login",
                          json={"username": username, "password": "hunter2-but-longer"})
    assert session.status_code == 200, session.text
    return _bearer(session.json()["token"])


def _fake_running_container(monkeypatch, *, name="demo", workspace=None,
                            http_url="http://localhost:5055"):
    import managers.container_manager as container_manager
    import managers.node_manager as node_manager
    monkeypatch.setattr(node_manager, "list_nodes", lambda: [
        {"container_name": name, "http_url": http_url, "workspace": workspace},
    ])
    monkeypatch.setattr(container_manager, "list_containers", lambda: [
        {"name": name, "state": "running"},
    ])


def _fake_stopped_container(monkeypatch, *, name="demo"):
    import managers.container_manager as container_manager
    import managers.node_manager as node_manager
    monkeypatch.setattr(node_manager, "list_nodes", lambda: [])
    monkeypatch.setattr(container_manager, "list_containers", lambda: [
        {"name": name, "state": "exited"},
    ])


def test_single_mode_mints_a_ticket_for_a_running_container(single, client, monkeypatch):
    _fake_running_container(monkeypatch)
    resp = client.post("/api/preview/tickets", json={"kind": "container", "name": "demo"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["url"].startswith("/preview/")
    assert body["url"].endswith("/")
    assert body["expires_in"] == preview_tickets.DEFAULT_TTL_SECONDS


def test_a_stopped_container_is_404(single, client, monkeypatch):
    _fake_stopped_container(monkeypatch)
    resp = client.post("/api/preview/tickets", json={"kind": "container", "name": "demo"})
    assert resp.status_code == 404


def test_an_unknown_container_is_404(single, client, monkeypatch):
    import managers.container_manager as container_manager
    import managers.node_manager as node_manager
    monkeypatch.setattr(node_manager, "list_nodes", lambda: [])
    monkeypatch.setattr(container_manager, "list_containers", lambda: [])
    resp = client.post("/api/preview/tickets", json={"kind": "container", "name": "nope"})
    assert resp.status_code == 404


def test_multi_mode_refuses_a_non_member_and_serves_a_member(multi, client, monkeypatch):
    _fake_running_container(monkeypatch, workspace="alpha")
    admin = _admin(client)
    assert client.post("/api/workspaces", json={"name": "alpha"},
                       headers=admin).status_code == 200
    bob = _member(client, admin)

    refused = client.post("/api/preview/tickets", json={"kind": "container", "name": "demo"},
                          headers=bob)
    assert refused.status_code == 403

    added = client.put("/api/workspaces/alpha/members",
                       json={"user_id": client.get("/api/auth/me", headers=bob).json()["id"],
                             "role": "viewer"},
                       headers=admin)
    assert added.status_code == 200
    allowed = client.post("/api/preview/tickets", json={"kind": "container", "name": "demo"},
                          headers=bob)
    assert allowed.status_code == 200, allowed.text


def test_a_project_with_no_preview_url_or_port_is_404(single, client):
    from projects.models import Project
    from projects.storage import ProjectStore
    from common.paths import PROJECTS_FILE
    store = ProjectStore(path=PROJECTS_FILE)
    project = store.add(Project(name="no-preview", workspace="default"))
    resp = client.post("/api/preview/tickets", json={"kind": "project", "project_id": project.id})
    assert resp.status_code == 404


def test_a_project_with_a_port_mints_a_ticket(single, client):
    from projects.models import Project, FrontendConfig
    from projects.storage import ProjectStore
    from common.paths import PROJECTS_FILE
    store = ProjectStore(path=PROJECTS_FILE)
    project = store.add(Project(name="has-preview", workspace="default",
                                frontend=FrontendConfig(enabled=True, port=5173)))
    resp = client.post("/api/preview/tickets", json={"kind": "project", "project_id": project.id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["url"].startswith("/preview/")


# ── the proxy itself, over httpx.MockTransport ──────────────────────────────

BASE = "http://localhost:9931"


@pytest.fixture
def mock_upstream(monkeypatch):
    """Point the proxy's client factory at an ``httpx.MockTransport``, and
    the proxy's target resolution at a fixed, fake target, so every test in
    this section is pure request/response, no container or project lookup."""
    from routes import preview

    handler_box = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        return handler_box["handler"](request)

    def _client_factory(pinned_ip):
        return httpx.AsyncClient(transport=httpx.MockTransport(_handler), follow_redirects=False)

    monkeypatch.setattr(preview, "_client_factory", _client_factory)
    monkeypatch.setattr(preview, "_resolve_target", lambda kind, target_id: (BASE, None))

    def _set(handler):
        handler_box["handler"] = handler

    return _set


def _ticket() -> str:
    return preview_tickets.mint({"kind": "container", "id": "demo"}, principal_id="local")


def test_html_gets_a_base_tag_and_absolute_path_rewrite(single, client, mock_upstream):
    html = (b"<html><head><title>x</title></head><body>"
           b'<a href="/static/app.js">js</a>'
           b'<script>var u = "/should/not/change";</script>'
           b"</body></html>")

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=html)

    mock_upstream(handler)
    ticket = _ticket()
    resp = client.get(f"/preview/{ticket}/index.html")
    assert resp.status_code == 200
    body = resp.text
    assert f'<base href="/preview/{ticket}/">' in body
    assert f'href="/preview/{ticket}/static/app.js"' in body
    # Never rewritten inside a script block.
    assert '"/should/not/change"' in body


def test_response_headers_are_filtered_and_frame_headers_added(single, client, mock_upstream):
    def handler(request):
        return httpx.Response(200, headers={
            "content-type": "text/plain",
            "set-cookie": "sekrit=1",
            "content-security-policy": "default-src 'self'",
            "x-frame-options": "DENY",
            "connection": "keep-alive",
        }, content=b"hello")

    mock_upstream(handler)
    ticket = _ticket()
    resp = client.get(f"/preview/{ticket}/anything")
    assert resp.status_code == 200
    assert "set-cookie" not in resp.headers
    assert resp.headers["content-security-policy"] == "frame-ancestors 'self'"
    assert resp.headers["x-frame-options"] == "SAMEORIGIN"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers["cache-control"] == "no-store"


def test_a_same_origin_redirect_is_rewritten_under_the_ticket(single, client, mock_upstream):
    def handler(request):
        return httpx.Response(302, headers={"location": f"{BASE}/somewhere/else"})

    mock_upstream(handler)
    ticket = _ticket()
    resp = client.get(f"/preview/{ticket}/start", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == f"/preview/{ticket}/somewhere/else"


def test_a_redirect_to_another_origin_is_left_alone(single, client, mock_upstream):
    def handler(request):
        return httpx.Response(302, headers={"location": "https://elsewhere.example/x"})

    mock_upstream(handler)
    ticket = _ticket()
    resp = client.get(f"/preview/{ticket}/start", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://elsewhere.example/x"


def test_a_bad_ticket_is_403(single, client, mock_upstream):
    mock_upstream(lambda request: httpx.Response(200))
    resp = client.get("/preview/not-a-real-ticket/anything")
    assert resp.status_code == 403
    assert "expired" in resp.text.lower()


def test_a_refused_host_is_400(single, client, monkeypatch):
    from routes import preview
    # A private literal address: no DNS lookup needed, is_public_address says no.
    monkeypatch.setattr(preview, "_resolve_target",
                        lambda kind, target_id: ("http://10.1.2.3:9999", None))
    ticket = _ticket()
    resp = client.get(f"/preview/{ticket}/anything")
    assert resp.status_code == 400


def test_no_trailing_slash_redirects_to_one(single, client, mock_upstream):
    mock_upstream(lambda request: httpx.Response(200))
    ticket = _ticket()
    resp = client.get(f"/preview/{ticket}", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == f"/preview/{ticket}/"


def test_a_ticket_past_half_life_gets_a_successor_header(single, client, mock_upstream,
                                                         monkeypatch):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"ok")

    mock_upstream(handler)
    fresh = client.get(f"/preview/{_ticket()}/a")
    assert "x-preview-ticket" not in fresh.headers
    aging = preview_tickets.mint({"kind": "container", "id": "demo"}, principal_id="local",
                                 ttl_seconds=preview_tickets.DEFAULT_TTL_SECONDS)
    later = time.time() + 400
    monkeypatch.setattr(preview_tickets.time, "time", lambda: later)
    resp = client.get(f"/preview/{aging}/a")
    successor = resp.headers["x-preview-ticket"]
    assert preview_tickets.verify(successor) == {"kind": "container", "id": "demo",
                                                 "user": "local"}
