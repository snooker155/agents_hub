"""One name per kind on the marketplace (common/marketplace_names.py): a
second agent, flow or skill cannot be published, or a published one renamed,
under a name another published item of its kind already carries."""
from __future__ import annotations

import pytest

from agents import registry

ENTRY = "agents.agent_factory:build_agent_executor"


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.agents import router as agents_router
    from routes.flows import router as flows_router
    from routes.skills import router as skills_router

    registry.replace_all_raw([
        {"id": "reviewer_a", "name": "Code Review", "type": "langchain", "entrypoint": ENTRY,
         "owner_workspace": "ws_a", "shared": True},
        {"id": "reviewer_b", "name": "code  review", "type": "langchain", "entrypoint": ENTRY,
         "owner_workspace": "ws_b"},
        {"id": "writer_b", "name": "Writer", "type": "langchain", "entrypoint": ENTRY,
         "owner_workspace": "ws_b"},
        {"id": "writer_c", "name": "writer", "type": "langchain", "entrypoint": ENTRY,
         "owner_workspace": "ws_b"},
    ])
    app = FastAPI()
    for router in (agents_router, flows_router, skills_router):
        app.include_router(router)
    yield TestClient(app)
    registry.replace_all_raw([])


def _flow(fid, name, ws, agents=(), shared=False):
    from flow import store as flow_store
    flow_store.save_flow({
        "id": fid, "name": name, "description": "", "workspace": ws, "task_id": None,
        "shared": shared, "edges": [],
        "nodes": [{"id": f"n{i}", "type": "flowNode", "position": {"x": 0, "y": 0},
                   "data": {"label": a, "agent_id": a, "description": ""}}
                  for i, a in enumerate(agents)],
    })


def test_an_agent_is_not_published_under_a_taken_name(client):
    r = client.post("/api/agents/reviewer_b/sharing", json={"shared": True})
    assert r.status_code == 409 and "Code Review" in r.json()["detail"]
    assert not registry.get_agent("reviewer_b").shared
    # Renamed, it publishes; the published one cannot take its old name back.
    assert client.put("/api/agents/reviewer_b/identity", json={"name": "Review B"}).status_code == 200
    assert client.post("/api/agents/reviewer_b/sharing", json={"shared": True}).status_code == 200
    r = client.put("/api/agents/reviewer_b/identity", json={"name": "CODE REVIEW"})
    assert r.status_code == 409
    assert registry.get_agent("reviewer_b").name == "Review B"
    # Unpublished agents share names freely.
    assert client.put("/api/agents/writer_b/identity", json={"name": "Code Review"}).status_code == 200


def test_a_flow_is_not_published_under_a_taken_name(client):
    from flow import store as flow_store
    _flow("flow-pub", "Triage", "ws_a", shared=True)
    _flow("flow-new", " triage ", "ws_b")
    try:
        r = client.post("/api/flows/flow-new/sharing", json={"shared": True})
        assert r.status_code == 409 and "Triage" in r.json()["detail"]
        assert not flow_store.get_flow("flow-new").get("shared")
        assert client.put("/api/flows/flow-new", json={"name": "Triage B"}).status_code == 200
        assert client.post("/api/flows/flow-new/sharing", json={"shared": True}).status_code == 200
        assert client.put("/api/flows/flow-new", json={"name": "TRIAGE"}).status_code == 409
        assert flow_store.get_flow("flow-new")["name"] == "Triage B"
    finally:
        flow_store.delete_flow("flow-pub")
        flow_store.delete_flow("flow-new")


def test_publishing_a_flow_refuses_agents_whose_names_are_taken(client):
    from flow import store as flow_store
    _flow("flow-clash", "Clash", "ws_b", agents=["reviewer_b"])
    _flow("flow-twins", "Twins", "ws_b", agents=["writer_b", "writer_c"])
    try:
        r = client.post("/api/flows/flow-clash/sharing", json={"shared": True})
        assert r.status_code == 400 and "reviewer_b" in r.json()["detail"]
        r = client.post("/api/flows/flow-twins/sharing", json={"shared": True})
        assert r.status_code == 400 and "both named" in r.json()["detail"]
        assert not registry.get_agent("writer_b").shared
        assert not flow_store.get_flow("flow-twins").get("shared")
    finally:
        flow_store.delete_flow("flow-clash")
        flow_store.delete_flow("flow-twins")


def test_a_skill_is_not_published_under_a_taken_name(client):
    from memory.procedural import Procedure, ProcedureStore
    published = Procedure(name="Summarize", description="d", steps=["x"], workspace="ws_a", shared=True)
    other = Procedure(name="summarize", description="d", steps=["y"], workspace="ws_b")
    ProcedureStore("ws_a").add(published)
    ProcedureStore("ws_b").add(other)
    r = client.post(f"/api/skills/{other.id}/sharing", json={"shared": True})
    assert r.status_code == 409 and "Summarize" in r.json()["detail"]
    assert client.patch(f"/api/skills/{other.id}", json={"name": "Summarize B"}).status_code == 200
    assert client.post(f"/api/skills/{other.id}/sharing", json={"shared": True}).status_code == 200
    assert client.patch(f"/api/skills/{other.id}", json={"name": "SUMMARIZE"}).status_code == 409
