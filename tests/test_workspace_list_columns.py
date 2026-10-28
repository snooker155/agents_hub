"""``GET /api/workspaces`` carries the list page's columns: how many agents
the Agents page lists in each workspace and the workspace's project names."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from routes import projects as project_routes
    from routes import workspaces, agents
    from projects.storage import ProjectStore
    monkeypatch.setattr(project_routes, "_store", ProjectStore(path=tmp_path / "projects.json"))
    app = FastAPI()
    for r in (workspaces.router, project_routes.router, agents.router):
        app.include_router(r)
    c = TestClient(app)
    assert c.post("/api/workspaces", json={"name": "cols-ws"}).status_code == 200
    return c


def _row(client, name):
    rows = client.get("/api/workspaces").json()
    return next(r for r in rows if r["name"] == name)


def test_list_counts_agents_the_agents_page_lists_there(client):
    row = _row(client, "cols-ws")
    listed = client.get("/api/agents", params={"workspace": "cols-ws"}).json()
    assert row["agents_count"] == len(listed)
    assert isinstance(row["agents_count"], int)


def test_list_names_the_projects_of_each_workspace(client):
    for name in ("alpha", "beta", "gamma", "delta"):
        resp = client.post("/api/projects", json={"name": name, "workspace": "cols-ws"})
        assert resp.status_code == 200, resp.text
    # A project elsewhere stays out of this row.
    assert client.post("/api/workspaces", json={"name": "cols-other"}).status_code == 200
    assert client.post("/api/projects", json={"name": "other-proj", "workspace": "cols-other"}).status_code == 200

    row = _row(client, "cols-ws")
    assert row["projects"] == ["alpha", "beta", "gamma", "delta"]
    assert row["projects_count"] == 4
    other = _row(client, "cols-other")
    assert other["projects"] == ["other-proj"]
