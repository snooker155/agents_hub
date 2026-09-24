"""The hub's browser routes (dashboard/backend/routes/browser.py) over real
HTTP, with the browser service replaced by a small in-memory fake: no
Playwright and no service process. What is checked is the hub's part: the
not-configured answer, the policy pre-flight on typed addresses, the pass
through of frames and refusals, the run lookup, the hand-off and membership
in ``multi`` mode."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

PASSWORD = "hunter2-but-longer"


class _Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data if data is not None else {}
        self.text = json.dumps(self._data)

    def json(self):
        return self._data


class FakeService:
    """The browser service's registry and a page that does what it is told."""

    def __init__(self):
        self.sessions = {}
        self.calls = []

    def add(self, sid, **tags):
        self.sessions[sid] = {"session_id": sid, "run_id": "", "workspace": "default",
                              "owner": "user", "label": "", "url": "https://example.com/",
                              "title": "Example", "created_at": 1.0, "last_used_at": 1.0, **tags}

    def __call__(self, method, path, **kw):
        self.calls.append((method, path, kw))
        parts = path.strip("/").split("/")
        if path == "/sessions" and method == "GET":
            params = kw.get("params") or {}
            out = [s for s in self.sessions.values()
                   if all(not params.get(k) or s.get(k) == params[k] for k in ("run_id", "workspace"))]
            return _Resp(data={"sessions": out})
        if path == "/sessions" and method == "POST":
            sid = f"S{len(self.sessions) + 1}"
            body = kw["json"]
            self.add(sid, workspace=body["workspace"], owner=body["owner"], label=body["label"],
                     url="about:blank", title="")
            return _Resp(data={"session_id": sid})
        sid = parts[1]
        if sid not in self.sessions:
            return _Resp(404, {"detail": "no such session (closed or expired)"})
        s = self.sessions[sid]
        if len(parts) == 2:
            if method == "GET":
                return _Resp(data=s)
            if method == "PATCH":
                s.update({k: v for k, v in kw["json"].items() if v is not None})
                return _Resp(data=s)
            if method == "DELETE":
                del self.sessions[sid]
                return _Resp(data={"ok": True})
        action = parts[2]
        if action == "frame":
            return _Resp(data={"url": s["url"], "title": s["title"], "width": 1280, "height": 800,
                               "image": "data:image/jpeg;base64,AAAA"})
        if action == "navigate":
            s["url"] = kw["json"]["url"]
            return _Resp(data={"url": s["url"], "title": "T", "status": 200, "blocked": []})
        if action == "control":
            s["controlled_by"] = kw["json"]["by"] if kw["json"]["on"] else ""
            return _Resp(data=s)
        if action == "input":
            if kw["json"].get("kind") == "click" and kw["json"].get("x") == 666:
                return _Resp(403, {"detail": "refused the page the input led to: private address"})
            return _Resp(data={"url": s["url"], "title": s["title"], "blocked": []})
        raise AssertionError(path)


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
def service(monkeypatch, single):
    from common.config import settings
    from tools import browser as browser_tools
    from tools import web
    monkeypatch.setattr(settings, "browser_url", "http://browser.test:3000")
    monkeypatch.setattr(settings, "browser_token", "tok")
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda h, p: [(2, 1, 6, "", ("93.184.216.34", 0))])
    fake = FakeService()
    monkeypatch.setattr(browser_tools, "_request", fake)
    return fake


def test_status_and_actions_when_not_configured(single, client, monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "browser_url", "")
    monkeypatch.setattr(settings, "browser_token", "")
    assert client.get("/api/browser/status").json() == {"configured": False, "url": None}
    for method, path in (("get", "/api/browser/sessions"), ("get", "/api/browser/sessions/X/frame"),
                         ("get", "/api/browser/runs/r1/session")):
        response = getattr(client, method)(path)
        assert response.status_code == 503, path
        assert "not configured" in response.json()["detail"]
    assert client.post("/api/browser/sessions", json={"workspace": "default"}).status_code == 503


def test_status_when_configured(service, client):
    assert client.get("/api/browser/status").json() == {"configured": True, "url": "http://browser.test:3000"}


def test_create_a_user_session_under_the_workspace_policy(service, client):
    response = client.post("/api/browser/sessions",
                           json={"workspace": "default", "url": "https://example.com/start"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["url"] == "https://example.com/start"
    created = next(c for c in service.calls if c[:2] == ("POST", "/sessions"))[2]["json"]
    assert created["owner"] == "user" and created["workspace"] == "default"
    assert set(created["policy"]) == {"deny_domains", "allow_domains", "allowlist_enabled"}


def test_a_refused_first_address_opens_no_session(service, client, monkeypatch):
    from tools import web
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda h, p: [(2, 1, 6, "", ("127.0.0.1", 0))])
    response = client.post("/api/browser/sessions", json={"workspace": "default", "url": "http://localhost/"})
    assert response.status_code == 400
    assert service.sessions == {}


def test_frame_passes_through(service, client):
    service.add("A1")
    frame = client.get("/api/browser/sessions/A1/frame").json()
    assert frame["image"].startswith("data:image/jpeg;base64,")
    assert (frame["width"], frame["height"]) == (1280, 800)
    assert client.get("/api/browser/sessions/NOPE/frame").status_code == 404


def test_input_navigate_to_a_refused_url_never_reaches_the_service(service, client, monkeypatch):
    from common.config import settings
    service.add("A1")
    monkeypatch.setattr(settings, "web_deny_domains", ("evil.com",))
    response = client.post("/api/browser/sessions/A1/input", json={"kind": "navigate", "url": "https://evil.com/"})
    assert response.status_code == 400
    assert "deny list" in response.json()["detail"]
    assert not any(c[1].endswith("/input") for c in service.calls)


def test_input_passes_through_and_a_refused_landing_is_a_403(service, client):
    service.add("A1")
    ok = client.post("/api/browser/sessions/A1/input", json={"kind": "click", "x": 10, "y": 20})
    assert ok.status_code == 200
    sent = next(c for c in service.calls if c[1] == "/sessions/A1/input")[2]["json"]
    assert (sent["kind"], sent["x"], sent["y"]) == ("click", 10, 20)
    refused = client.post("/api/browser/sessions/A1/input", json={"kind": "click", "x": 666, "y": 1})
    assert refused.status_code == 403 and "private address" in refused.json()["detail"]


def test_run_session_lookup(service, client):
    assert client.get("/api/browser/runs/r1/session").status_code == 404
    service.add("A1", run_id="r1", owner="agent")
    found = client.get("/api/browser/runs/r1/session")
    assert found.status_code == 200 and found.json()["session_id"] == "A1"


def test_control_is_taken_and_released_in_the_callers_name(service, client):
    service.add("A1", owner="agent", run_id="r1")
    taken = client.post("/api/browser/sessions/A1/control", json={"on": True})
    assert taken.status_code == 200 and taken.json()["controlled_by"] == "local"
    assert service.calls[-1][2]["json"] == {"on": True, "by": "local"}
    released = client.post("/api/browser/sessions/A1/control", json={"on": False})
    assert released.json()["controlled_by"] == ""


def test_the_websocket_relays_the_services_frames(service, client, monkeypatch):
    from routes import browser as browser_routes
    service.add("A1", owner="agent")
    frames = [json.dumps({"type": "frame", "seq": 1, "image": "data:image/jpeg;base64,AAAA"}),
              json.dumps({"type": "keepalive", "seq": 1})]

    async def fake_frames(session_id):
        assert session_id == "A1"
        for f in frames:
            yield f

    monkeypatch.setattr(browser_routes, "_service_frames", fake_frames)
    with client.websocket_connect("/api/browser/sessions/A1/ws") as ws:
        assert ws.receive_json()["type"] == "frame"
        assert ws.receive_json()["type"] == "keepalive"


def test_the_websocket_reports_a_dead_service_and_closes(service, client, monkeypatch):
    from routes import browser as browser_routes
    from starlette.websockets import WebSocketDisconnect
    service.add("A1", owner="agent")

    async def broken(session_id):
        raise OSError("connection refused")
        yield  # unreachable, but it makes this an async generator

    monkeypatch.setattr(browser_routes, "_service_frames", broken)
    with client.websocket_connect("/api/browser/sessions/A1/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "error" and "connection refused" in first["detail"]
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    # An unknown session never gets accepted.
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/browser/sessions/nope/ws"):
            pass


def test_handoff_creates_a_task_and_launches_with_the_session(service, client, monkeypatch):
    from routes import browser as browser_routes
    from runtime import entity_launch
    from tasks import service as tasks_service
    from tools import browser as browser_tools
    service.add("A1", workspace="default", url="https://example.com/cart")
    seen = {}

    def fake_launch(task_id, agent_id):
        seen["env"] = dict(entity_launch._CHILD_ENV.get() or {})
        seen["adopted"] = browser_tools.adopted_session.get()
        seen["agent"] = agent_id
        return {"run_id": "run-new"}

    monkeypatch.setattr(browser_routes, "_launch", fake_launch)
    response = client.post("/api/browser/sessions/A1/handoff",
                           json={"agent_id": "scout", "message": "Finish the checkout"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == "run-new"
    assert seen == {"env": {"AGENTS_HUB_BROWSER_SESSION": "A1"}, "adopted": "A1", "agent": "scout"}
    task = tasks_service.get_task(body["task_id"])
    assert task.title == "Finish the checkout" and task.workspace == "default"
    assert "browser session is attached" in task.description and "A1" in task.description
    assert service.sessions["A1"]["run_id"] == "run-new"
    assert service.sessions["A1"]["owner"] == "agent" and service.sessions["A1"]["label"] == "scout"
    # The adoption does not outlive the request.
    assert browser_tools.adopted_session.get() is None


def test_handoff_to_another_workspace_is_refused(service, client):
    service.add("A1", workspace="default")
    response = client.post("/api/browser/sessions/A1/handoff",
                           json={"agent_id": "scout", "message": "x", "workspace": "other"})
    assert response.status_code == 400


# ── multi mode ───────────────────────────────────────────────────────────────

def test_multi_mode_refuses_a_session_outside_the_callers_workspaces(service, client, monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    boot = client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    admin = {"Authorization": f"Bearer {boot.json()['token']}"}
    assert client.post("/api/workspaces", json={"name": "alpha"}, headers=admin).status_code == 200
    created = client.post("/api/auth/users", json={"username": "bob", "password": PASSWORD}, headers=admin)
    login = client.post("/api/auth/login", json={"username": "bob", "password": PASSWORD})
    bob = {"Authorization": f"Bearer {login.json()['token']}"}

    service.add("A1", workspace="alpha", run_id="r1")
    assert client.get("/api/browser/sessions/A1/frame", headers=bob).status_code == 403
    assert client.post("/api/browser/sessions/A1/input", json={"kind": "click"}, headers=bob).status_code == 403
    assert client.delete("/api/browser/sessions/A1", headers=bob).status_code == 403
    assert client.post("/api/browser/sessions/A1/handoff", json={"agent_id": "a"}, headers=bob).status_code == 403
    assert client.get("/api/browser/sessions", headers=bob).json() == {"sessions": []}
    assert client.get("/api/browser/runs/r1/session", headers=bob).status_code == 404
    assert client.get("/api/browser/sessions/A1/frame", headers=admin).status_code == 200

    client.put("/api/workspaces/alpha/members",
               json={"user_id": created.json()["id"], "role": "editor"}, headers=admin)
    assert client.get("/api/browser/sessions/A1/frame", headers=bob).status_code == 200
    assert client.post("/api/browser/sessions/A1/input", json={"kind": "click"}, headers=bob).status_code == 200
