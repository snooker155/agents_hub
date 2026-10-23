"""The Tasks page planner (routes/projects/planner.py, /api/task-planner): the
same turn as a project's planner, over a whole workspace and without graphs."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from routes import projects as project_routes
    from routes import workspaces, entity_chats
    app = FastAPI()
    for r in (workspaces.router, project_routes.router, entity_chats.router):
        app.include_router(r)
    c = TestClient(app)
    assert c.post("/api/workspaces", json={"name": "plan-ws"}).status_code == 200
    return c


class FakePlanner:
    """Creates one task in the workspace, the way the real agent's tools would."""
    provider, model = "fake", "fake-1"

    def __init__(self):
        self.prompts = []

    async def arun(self, prompt, callbacks=None):
        from tasks import service as ts
        self.prompts.append(prompt)
        ts.create_task("Write the menu page", workspace="plan-ws")
        return SimpleNamespace(ok=True, agent_output="Added one task.")


def _events(resp):
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_an_unknown_workspace_has_no_planner(client):
    assert client.get("/api/task-planner/chat", params={"workspace": "nope"}).status_code == 404


def test_a_turn_plans_in_the_workspace_and_keeps_its_chat(client, monkeypatch):
    from projects import planner_service
    fake = FakePlanner()
    monkeypatch.setattr(planner_service, "ensure_planner_agent", lambda: True)
    monkeypatch.setattr(planner_service, "build_agent", lambda path: fake)

    resp = client.post("/api/task-planner/generate", params={"workspace": "plan-ws"},
                       json={"message": "Plan the menu page"})
    assert resp.status_code == 200, resp.text
    done = next(e for e in _events(resp) if e.get("type") == "done")
    assert done["created"] == 1
    # No project here, so the prompt does not send the agent to the graphs.
    assert "get_project_graph" not in fake.prompts[0]
    assert "workspace's task planner" in fake.prompts[0]

    chat = client.get("/api/task-planner/chat", params={"workspace": "plan-ws"}).json()
    assert chat["chat_ref"] == {"kind": "wstasks", "id": "plan-ws"}
    assert [m["content"] for m in chat["messages"]] == ["Plan the menu page", "Added one task."]

    assert client.delete("/api/task-planner/chat", params={"workspace": "plan-ws"}).status_code == 200
    assert client.get("/api/task-planner/chat", params={"workspace": "plan-ws"}).json()["messages"] == []


def test_stop_with_nothing_running(client):
    out = client.post("/api/task-planner/stop", params={"workspace": "plan-ws"}).json()
    assert out == {"stopped": False, "cancelled": 0}
