"""GET /api/workspaces/{name}/summary: the one answer the header, the palette
and the chat page read about the selected workspace, in place of ``/{name}``,
``/{name}/model``, ``/{name}/isolation`` and ``/{name}/settings-overrides``.
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


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_summary_carries_what_the_pages_used_to_ask_for_separately(single, client):
    assert client.post("/api/workspaces", json={"name": "sum-ws"}).status_code == 200
    palette = {"brand": "#166534"}
    client.put("/api/workspaces/sum-ws/settings-overrides", json={"overrides": {"palette": palette}})
    client.put("/api/workspaces/sum-ws/model", json={"provider": "anthropic", "model": "claude-sonnet-5"})
    client.put("/api/workspaces/sum-ws/personal-memory", json={"enabled": False})

    response = client.get("/api/workspaces/sum-ws/summary")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "sum-ws"
    assert body["palette"] == palette
    assert body["isolated"] is False
    assert body["personal_memory_enabled"] is False
    assert isinstance(body["allowed_agents"], list)
    assert body["allowed_agents"] == client.get("/api/workspaces/sum-ws").json()["metadata"].get("allowed_agents", [])
    assert body["model"] == client.get("/api/workspaces/sum-ws/model").json()
    assert body["model"]["override"] == {"provider": "anthropic", "model": "claude-sonnet-5"}


def test_summary_defaults(single, client):
    client.post("/api/workspaces", json={"name": "sum-plain"})
    body = client.get("/api/workspaces/sum-plain/summary").json()
    assert body["palette"] is None
    assert body["personal_memory_enabled"] is True


def test_summary_of_an_unknown_workspace_is_404(single, client):
    assert client.get("/api/workspaces/no-such-ws/summary").status_code == 404


def test_a_viewer_reads_the_summary_but_not_the_owner_settings(multi, client):
    boot = client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    assert boot.status_code == 200, boot.text
    admin = _bearer(boot.json()["token"])
    client.post("/api/workspaces", json={"name": "alpha"}, headers=admin)
    client.put("/api/workspaces/alpha/settings-overrides",
               json={"overrides": {"palette": {"brand": "#c2410c"}}}, headers=admin)
    created = client.post("/api/auth/users", json={"username": "bob", "password": PASSWORD},
                          headers=admin)
    bob = _bearer(client.post("/api/auth/login",
                              json={"username": "bob", "password": PASSWORD}).json()["token"])
    client.put("/api/workspaces/alpha/members", json={"user_id": created.json()["id"], "role": "viewer"},
               headers=admin)

    assert client.get("/api/workspaces/alpha/settings-overrides", headers=bob).status_code == 403
    assert client.get("/api/workspaces/alpha/isolation", headers=bob).status_code == 403
    summary = client.get("/api/workspaces/alpha/summary", headers=bob)
    assert summary.status_code == 200, summary.text
    assert summary.json()["palette"] == {"brand": "#c2410c"}
    assert summary.json()["isolated"] is False
