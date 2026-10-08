"""The registry's hub-wide view belongs to the default workspace (multi mode).

``GET /api/registry`` without a workspace, or with ``default``, lists every
workspace's agents, flows, skills and MCP servers. Only an administrator or a
member of ``default`` gets that; a member of another workspace asks for their
own and sees only it.
"""
from __future__ import annotations

import pytest

PASSWORD = "hunter2-but-longer"
WS, OTHER = "shop", "elsewhere"


@pytest.fixture
def hub(monkeypatch):
    """A multi-mode app with a member of ``default`` and a member of ``shop``
    only, and a flow in each of ``shop`` and ``elsewhere``; returns headers."""
    from fastapi.testclient import TestClient

    from common import identity
    from common.config import settings
    from dashboard.backend.main import app
    from flow import store as flow_store
    from workspace import create_workspace_folder

    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    create_workspace_folder(WS)
    create_workspace_folder(OTHER)
    for flow_id, workspace in (("flow-shop", WS), ("flow-elsewhere", OTHER)):
        flow_store.save_flow({"id": flow_id, "name": flow_id, "nodes": [], "edges": [], "workspace": workspace})
    client = TestClient(app)
    client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    for name, workspace in (("home", "default"), ("shopper", WS)):
        user = identity.create_user(name, PASSWORD)
        identity.set_member(workspace, user["id"], "viewer")

    def login(name):
        token = client.post("/api/auth/login", json={"username": name, "password": PASSWORD})
        return {"Authorization": f"Bearer {token.json()['token']}"}

    yield client, {name: login(name) for name in ("root", "home", "shopper")}
    for flow_id in ("flow-shop", "flow-elsewhere"):
        flow_store.delete_flow(flow_id)


def _flows(response):
    assert response.status_code == 200, response.text
    return {f["id"] for f in response.json()["flows"]}


def test_hub_wide_view_needs_default_or_admin(hub):
    client, h = hub
    for who in ("root", "home"):
        for params in ({}, {"workspace": "default"}):
            assert {"flow-shop", "flow-elsewhere"} <= _flows(client.get("/api/registry", params=params, headers=h[who]))
    assert client.get("/api/registry", headers=h["shopper"]).status_code == 403
    assert client.get("/api/registry", params={"workspace": "default"}, headers=h["shopper"]).status_code == 403


def test_a_workspace_member_sees_only_their_workspace(hub):
    client, h = hub
    seen = _flows(client.get("/api/registry", params={"workspace": WS}, headers=h["shopper"]))
    assert "flow-shop" in seen and "flow-elsewhere" not in seen
    assert client.get("/api/registry", params={"workspace": OTHER}, headers=h["shopper"]).status_code == 403
