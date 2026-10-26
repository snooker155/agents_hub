"""
The per-agent streaming switch (POST /agents/{id}/streaming, Behavior tab):
on makes the agent stream in background runs whatever AGENT_STREAMING says,
off hands the decision back to the global flag (agent_factory.resolve_streaming).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from agents.agent_factory import resolve_streaming
from agents.registry import AgentSpec, add_agent, get_agent, replace_all_raw


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    monkeypatch.setattr("common.config.read_dot_env", lambda: {"AGENT_STREAMING": "false"})
    replace_all_raw([])
    add_agent(AgentSpec(
        id="worker", name="worker", description="", type="langchain",
        entrypoint="agents.agent_launcher:run", tools=[],
    ), user_edit=False)
    yield
    replace_all_raw([])


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import agents as agent_routes

    app = FastAPI()
    app.include_router(agent_routes.router)
    return TestClient(app)


def test_switch_round_trips_and_drives_the_build_flag(client):
    assert client.get("/api/agents/worker/streaming").json() == {"streaming": False}
    assert resolve_streaming(get_agent("worker").streaming, {}) is False

    r = client.post("/api/agents/worker/streaming", json={"streaming": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"streaming": True}
    assert get_agent("worker").streaming is True
    assert resolve_streaming(get_agent("worker").streaming, {}) is True

    client.post("/api/agents/worker/streaming", json={"streaming": False})
    assert get_agent("worker").streaming is False


def test_unknown_agent_is_a_404(client):
    assert client.post("/api/agents/nobody/streaming", json={"streaming": True}).status_code == 404
