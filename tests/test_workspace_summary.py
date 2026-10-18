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
    pass


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


