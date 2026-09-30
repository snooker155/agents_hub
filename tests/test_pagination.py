"""
Pagination for the three big list endpoints: GET /api/tasks, /api/agents,
/api/flows.

Every one of them keeps its old shape — a bare list — when the caller sends
neither ``limit`` nor ``offset``; only the presence of either switches the
response to a page (``{items, total, limit, offset}``). The three endpoints
sit on different kinds of storage (tasks: SQLite; agents: assembled in memory
from the registry + factory; flows: one YAML/JSON pair per flow on disk), so
each is worth its own coverage of "the page is pushed into the store" versus
"the page is sliced after loading".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def _client(*routers):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    for r in routers:
        app.include_router(r)
    return TestClient(app)


# ── tasks (SQLite-backed) ────────────────────────────────────────────────────

@pytest.fixture
def tasks_client():
    from routes import tasks as tasks_routes
    return _client(tasks_routes.router)


def _make_tasks(n: int, workspace=None):
    from tasks import service as tasks_service
    return [tasks_service.create_task(f"task {i}", workspace=workspace) for i in range(n)]


def test_tasks_without_paging_params_returns_the_bare_list_unchanged(tasks_client):
    _make_tasks(3)
    body = tasks_client.get("/api/tasks").json()
    assert isinstance(body, list)
    assert len(body) == 3


def test_tasks_paging_slices_in_creation_order_and_reports_the_total(tasks_client):
    created = _make_tasks(5)
    body = tasks_client.get("/api/tasks", params={"limit": 2, "offset": 1}).json()
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert [t["id"] for t in body["items"]] == [str(created[1].id), str(created[2].id)]


def test_tasks_paging_with_only_offset_returns_the_rest(tasks_client):
    created = _make_tasks(4)
    body = tasks_client.get("/api/tasks", params={"offset": 3}).json()
    assert body["total"] == 4
    assert [t["id"] for t in body["items"]] == [str(created[3].id)]


def test_tasks_paging_respects_the_workspace_filter(tasks_client):
    _make_tasks(2, workspace="ws-a")
    b_tasks = _make_tasks(3, workspace="ws-b")
    body = tasks_client.get("/api/tasks", params={"workspace": "ws-b", "limit": 2}).json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert {t["id"] for t in body["items"]} <= {str(t.id) for t in b_tasks}


# ── agents (assembled in memory — sliced after load) ────────────────────────

@pytest.fixture
def agents_client(monkeypatch):
    from agents.registry import AgentSpec
    import agents.registry as registry_module
    import routes.agents as agents_routes

    specs = [
        AgentSpec(id=f"agent-{i}", name=f"Agent {i}", type="langchain",
                  entrypoint="agents.agent_factory:build_agent_executor")
        for i in range(4)
    ]
    monkeypatch.setattr(registry_module, "list_agents", lambda: list(specs))
    monkeypatch.setattr(registry_module, "get_agent",
                        lambda aid: next((s for s in specs if s.id == aid), None))

    class _EmptyFactory:
        def list_available_agents(self):
            return []

    monkeypatch.setattr(agents_routes, "get_factory", lambda: _EmptyFactory())
    return _client(agents_routes.router), specs


def test_agents_without_paging_params_returns_the_bare_list_unchanged(agents_client):
    client, specs = agents_client
    body = client.get("/api/agents").json()
    assert isinstance(body, list)
    assert len(body) == len(specs)


def test_agents_paging_slices_the_assembled_list_and_reports_the_total(agents_client):
    client, specs = agents_client
    body = client.get("/api/agents", params={"limit": 2, "offset": 1}).json()
    assert body["total"] == len(specs)
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert [a["id"] for a in body["items"]] == [specs[1].id, specs[2].id]


def test_agents_paging_past_the_end_is_an_empty_page(agents_client):
    client, specs = agents_client
    body = client.get("/api/agents", params={"offset": 100, "limit": 10}).json()
    assert body["total"] == len(specs)
    assert body["items"] == []


# ── flows (file-backed — sliced after load) ─────────────────────────────────

@pytest.fixture
def flows_client():
    from routes import flows as flows_routes
    return _client(flows_routes.router)


def _make_flows(n: int):
    from flow import store as flow_store
    ids = [f"flow-{i}" for i in range(n)]
    for fid in ids:
        flow_store.save_flow({
            "id": fid, "name": fid, "description": "", "nodes": [], "edges": [],
        })
    return ids


def test_flows_without_paging_params_returns_the_bare_list_unchanged(flows_client):
    _make_flows(3)
    body = flows_client.get("/api/flows").json()
    assert isinstance(body, list)
    assert len(body) == 3


def test_flows_paging_slices_the_loaded_list_and_reports_the_total(flows_client):
    ids = _make_flows(5)
    body = flows_client.get("/api/flows", params={"limit": 2, "offset": 2}).json()
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 2
    # flow_store.list_flows() sorts by id, so the page is deterministic
    # without re-deriving the store's own ordering.
    assert [f["id"] for f in body["items"]] == sorted(ids)[2:4]


def test_flows_with_an_explicit_allowlist_pages_the_ids_not_the_catalog(flows_client, monkeypatch):
    """An ``allowed_flows`` workspace only ever needs the ids it names, so the
    page is drawn from that list directly rather than the whole catalog."""
    from workspace import create_workspace_folder, update_workspace_metadata

    ids = _make_flows(5)
    create_workspace_folder("ws-allow")
    allowed = sorted(ids)[1:4]
    update_workspace_metadata("ws-allow", {"allowed_flows": allowed})

    body = flows_client.get(
        "/api/flows", params={"workspace": "ws-allow", "limit": 2}).json()
    assert body["total"] == len(allowed)
    assert [f["id"] for f in body["items"]] == allowed[:2]


# ── flow_store.list_flows / count_flows (store-level paging) ────────────────

def test_flow_store_list_flows_pages_without_parsing_the_rest(flows_client):
    """flows_client resets the store into a temp dir per test (see the
    ``routes.flows`` import); paging here only has to prove the slice and the
    file-count total agree with the unpaged list."""
    from flow import store as flow_store
    ids = _make_flows(5)
    total = flow_store.count_flows()
    assert total == 5
    page = flow_store.list_flows(limit=2, offset=3)
    assert [f["id"] for f in page] == sorted(ids)[3:5]


# ── agents.registry.list_agents (store-level paging) ─────────────────────────

def test_registry_list_agents_pages_the_cached_spec_list(monkeypatch):
    from agents.registry import AgentSpec
    import agents.registry as registry_module

    specs = [
        AgentSpec(id=f"agent-{i}", name=f"Agent {i}", type="langchain",
                  entrypoint="agents.agent_factory:build_agent_executor")
        for i in range(5)
    ]
    monkeypatch.setattr(registry_module, "_maybe_reload", lambda: list(specs))

    assert registry_module.list_agents() == specs
    assert registry_module.count_agents() == 5
    assert registry_module.list_agents(limit=2, offset=1) == specs[1:3]
    assert registry_module.list_agents(offset=4) == specs[4:]
