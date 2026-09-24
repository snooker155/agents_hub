"""
GET/PUT /api/agents/{agent_id}/loop-settings (dashboard/backend/routes/
agent_loop_settings.py): fallback_models validated against the enabled
catalog, output_schema checked as a JSON Schema jsonschema itself accepts,
tool_search/compaction are tri-state (true/false/null), saved through
agents.registry.add_agent like the neighbouring per-field routes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from agents.registry import AgentSpec, add_agent, get_agent, replace_all_raw
from providers.catalog import save_catalog_raw


@pytest.fixture(autouse=True)
def fresh_registry():
    replace_all_raw([])
    yield
    replace_all_raw([])


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import agent_loop_settings as loop_settings_routes

    app = FastAPI()
    app.include_router(loop_settings_routes.router)
    return TestClient(app)


@pytest.fixture
def agent():
    spec = AgentSpec(id="loop-agent", name="Loop Agent", type="langchain",
                     entrypoint="agents.definitions.demo:build")
    add_agent(spec)
    return spec


def test_get_defaults(client, agent):
    resp = client.get(f"/api/agents/{agent.id}/loop-settings")
    assert resp.status_code == 200
    assert resp.json() == {
        "fallback_models": [], "output_schema": None, "tool_search": None, "compaction": None,
    }


def test_get_missing_agent_404(client):
    resp = client.get("/api/agents/does-not-exist/loop-settings")
    assert resp.status_code == 404


def test_put_fallback_models_round_trips(client, agent):
    save_catalog_raw({"openai": {"default": "", "models": [{"id": "gpt-4o-mini", "enabled": True}]}})
    resp = client.put(f"/api/agents/{agent.id}/loop-settings",
                      json={"fallback_models": ["openai/gpt-4o-mini"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["fallback_models"] == ["openai/gpt-4o-mini"]
    assert get_agent(agent.id).fallback_models == ["openai/gpt-4o-mini"]


def test_put_rejects_unknown_catalog_model(client, agent):
    save_catalog_raw({"openai": {"default": "", "models": []}})
    resp = client.put(f"/api/agents/{agent.id}/loop-settings",
                      json={"fallback_models": ["openai/does-not-exist"]})
    assert resp.status_code == 400
    assert "does-not-exist" in resp.json()["detail"]


def test_put_output_schema_round_trips(client, agent):
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    resp = client.put(f"/api/agents/{agent.id}/loop-settings", json={"output_schema": schema})
    assert resp.status_code == 200, resp.text
    assert resp.json()["output_schema"] == schema
    assert get_agent(agent.id).output_schema == schema


def test_put_rejects_invalid_json_schema(client, agent):
    resp = client.put(f"/api/agents/{agent.id}/loop-settings",
                      json={"output_schema": {"type": "not-a-real-type"}})
    assert resp.status_code == 400
    assert "JSON Schema" in resp.json()["detail"]


def test_put_clears_output_schema_with_null(client, agent):
    schema = {"type": "object"}
    client.put(f"/api/agents/{agent.id}/loop-settings", json={"output_schema": schema})
    resp = client.put(f"/api/agents/{agent.id}/loop-settings", json={"output_schema": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["output_schema"] is None
    assert get_agent(agent.id).output_schema is None


def test_put_tool_search_and_compaction_are_tri_state(client, agent):
    resp = client.put(f"/api/agents/{agent.id}/loop-settings",
                      json={"tool_search": True, "compaction": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["tool_search"] is True
    assert resp.json()["compaction"] is False

    resp = client.put(f"/api/agents/{agent.id}/loop-settings",
                      json={"tool_search": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["tool_search"] is None
    # compaction untouched by a request that omits it entirely.
    assert resp.json()["compaction"] is False


def test_put_leaves_omitted_fields_alone(client, agent):
    save_catalog_raw({"openai": {"default": "", "models": [{"id": "gpt-4o-mini", "enabled": True}]}})
    client.put(f"/api/agents/{agent.id}/loop-settings", json={"fallback_models": ["openai/gpt-4o-mini"]})
    resp = client.put(f"/api/agents/{agent.id}/loop-settings", json={"compaction": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["fallback_models"] == ["openai/gpt-4o-mini"]
    assert resp.json()["compaction"] is True
