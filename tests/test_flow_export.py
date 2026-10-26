"""
Exporting a flow as YAML: the download name comes from the flow name, and a
name outside latin-1 (Cyrillic, say) must not break the response header.
"""
from __future__ import annotations

import asyncio
from urllib.parse import quote

import pytest

from dashboard.backend.routes import flows as flow_routes
from flow import store as flow_store


@pytest.fixture
def flow_named(monkeypatch):
    def make(name):
        flow = {"id": "f1", "name": name, "nodes": [], "edges": []}
        monkeypatch.setattr(flow_store, "export_flow_yaml", lambda fid: "name: x\n")
        monkeypatch.setattr(flow_store, "get_flow", lambda fid: flow)
    return make


def _export():
    return asyncio.run(flow_routes.export_flow("f1"))


def test_a_latin_name_is_the_file_name(flow_named):
    flow_named("Weekly report")
    disposition = _export().headers["content-disposition"]
    assert 'filename="Weekly-report.yaml"' in disposition


def test_a_cyrillic_name_goes_in_filename_star(flow_named):
    flow_named("Тренды кофе 2026")
    response = _export()
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert f"filename*=UTF-8''{quote('Тренды-кофе-2026')}.yaml" in disposition
    assert disposition.isascii()


# ── what the YAML carries ────────────────────────────────────────────────────

def _flow():
    """researcher → writer → switch, the switch sending the run to one of two."""
    def agent(nid, agent_id, label, **extra):
        return {"id": nid, "type": "flowNode",
                "data": {"agent_id": agent_id, "category": "agent", "label": label, **extra}}
    switch = {"id": "condition-3", "type": "flowNode", "data": {
        "entity_id": "state_switch", "category": "condition", "label": "Is it cold?",
        "config": {"routes": [{"target": "agent-4", "match": "all", "conditions": [
                       {"key": "post", "op": "contains", "value": "cold"},
                       {"key": "tone", "op": "not_empty"}]}],
                   "default": "agent-5"}}}
    return {
        "id": "f1", "name": "Coffee", "workspace": "zerno", "shared": True, "running": False,
        "created_at": "2026-10-22", "updated_at": "2026-10-23", "owner_user": "u1",
        "state": {"tone": "warm"},
        "nodes": [
            agent("agent-1", "researcher", "Find trends"),
            agent("agent-2", "writer", "Write the post", output=["post"], input=["tone"]),
            switch,
            agent("agent-4", "visualizer", "Picture"),
            agent("agent-5", "writer", "Rewrite", input=["post"]),
        ],
        "edges": [
            {"id": "e1", "source": "agent-1", "target": "agent-2"},
            {"id": "e2", "source": "agent-2", "target": "condition-3"},
            {"id": "e3", "source": "condition-3", "target": "agent-4"},
            {"id": "e4", "source": "condition-3", "target": "agent-5"},
        ],
    }


@pytest.fixture
def stored(monkeypatch):
    flow = _flow()
    monkeypatch.setattr(flow_store, "get_flow", lambda fid: flow if fid == "f1" else None)
    monkeypatch.setattr(flow_store, "_check_entities_exist", lambda fid, merged: None)
    return flow


def _exported():
    import yaml
    return yaml.safe_load(flow_store.export_flow_yaml("f1"))


def test_hub_bookkeeping_stays_out(stored):
    doc = _exported()
    for field in ("workspace", "shared", "running", "created_at", "updated_at", "owner_user", "id"):
        assert field not in doc
    assert doc["name"] == "Coffee"


def test_switch_targets_name_nodes_of_the_flow(stored):
    switch = _exported()["nodes"][2]
    assert switch["config"]["routes"][0]["target"] == "visualizer"
    assert switch["config"]["default"] == "writer#2"
    # The stored flow keeps its canvas ids.
    assert stored["nodes"][2]["data"]["config"]["default"] == "agent-5"


def test_nodes_keep_their_names_not_descriptions(stored):
    stored["nodes"][0]["data"]["description"] = "Agent specialized in deep analysis"
    nodes = _exported()["nodes"]
    assert [n.get("name") for n in nodes] == [
        "Find trends", "Write the post", "Is it cold?", "Picture", "Rewrite"]
    assert not any("description" in n for n in nodes)


def test_state_is_only_what_the_flow_declares(stored):
    doc = _exported()
    # Keys the nodes write appear while the flow runs, not in its start state.
    assert doc["state"] == {"tone": "warm"}
    assert "state_schema" not in doc


def test_an_empty_user_request_is_not_declared_state(stored):
    stored["state"] = {"user_request": "", "tone": "warm"}
    assert _exported()["state"] == {"tone": "warm"}
    stored["state"] = {"user_request": ""}
    assert "state" not in _exported()
    stored["state"] = {"user_request": "Coffee trends"}
    assert _exported()["state"] == {"user_request": "Coffee trends"}


def test_import_lays_the_nodes_out(stored):
    flow = flow_store.import_flow_yaml(flow_store.export_flow_yaml("f1"), "f2")
    at = {n["data"]["label"]: (n["position"]["x"], n["position"]["y"]) for n in flow["nodes"]}
    assert len(set(at.values())) == len(at)
    # A column per step: the switch's two targets share the column after it.
    assert at["Find trends"][0] < at["Write the post"][0] < at["Is it cold?"][0] < at["Picture"][0]
    assert at["Picture"][0] == at["Rewrite"][0]


def test_import_brings_back_names_and_switch_targets(stored):
    text = flow_store.export_flow_yaml("f1")
    flow = flow_store.import_flow_yaml(text, "f2")
    ids = {n["data"]["label"]: n["id"] for n in flow["nodes"]}
    switch = next(n for n in flow["nodes"] if n["data"].get("entity_id") == "state_switch")
    assert switch["data"]["config"]["routes"][0]["target"] == ids["Picture"]
    assert switch["data"]["config"]["default"] == ids["Rewrite"]
    assert {(e["source"], e["target"]) for e in flow["edges"]} >= {
        (switch["id"], ids["Picture"]), (switch["id"], ids["Rewrite"])}
    assert "state_schema" not in flow and "workspace" not in flow
    assert flow["state"] == {"tone": "warm"}
    from flow import validate
    assert validate._branching_errors(flow["nodes"], flow["edges"]) == []


# ── workspaces ──────────────────────────────────────────────────────────────

OWNED = {"writer": "zerno"}  # agent → the workspace that owns it


@pytest.fixture
def owned_agents(monkeypatch):
    from flow import registry as flow_registry

    def visible(agent_id, workspace):
        owner = OWNED.get(agent_id)
        return owner is None or owner == (workspace or "default")
    monkeypatch.setattr(flow_registry, "_agent_visible_in_workspace", visible)
    # The test registry is empty: an agent node resolves to its agent here.
    from types import SimpleNamespace
    from flow import dispatch as flow_dispatch

    def resolve(node):
        data = node.get("data") or {}
        if data.get("agent_id"):
            return SimpleNamespace(id=data["agent_id"], category="agent")
        return SimpleNamespace(id=data.get("entity_id"), category=data.get("category"))
    monkeypatch.setattr(flow_dispatch, "resolve_entity", resolve)


def _import(workspace, monkeypatch, saved):
    from dashboard.backend.routes.flows import FlowImport
    monkeypatch.setattr(flow_store, "save_flow", lambda f: saved.append(f))
    monkeypatch.setattr(flow_routes, "_authorize_flow_in_workspace", lambda ws, fid: saved.append(("auth", ws)))
    text = flow_store.export_flow_yaml("f1")
    return asyncio.run(flow_routes.import_flow(FlowImport(yaml=text, workspace=workspace)))


def test_import_lands_in_the_workspace_it_is_imported_into(stored, owned_agents, monkeypatch):
    saved = []
    flow = _import("zerno", monkeypatch, saved)
    assert flow["workspace"] == "zerno"
    assert ("auth", "zerno") in saved


def test_import_refuses_an_agent_of_another_workspace(stored, owned_agents, monkeypatch):
    from fastapi import HTTPException
    saved = []
    with pytest.raises(HTTPException) as err:
        _import("other", monkeypatch, saved)
    assert err.value.status_code == 422
    assert "agent 'writer' is not available in workspace 'other'" in err.value.detail
    assert saved == []


def test_import_with_no_workspace_lands_in_default(stored, owned_agents, monkeypatch):
    monkeypatch.setitem(OWNED, "writer", None)
    assert _import(None, monkeypatch, [])["workspace"] == "default"


def test_a_new_flow_belongs_to_the_workspace_it_is_created_in(monkeypatch):
    from dashboard.backend.routes.flows import FlowCreate
    saved, authorized = [], []
    monkeypatch.setattr(flow_routes, "_load", lambda: [])
    monkeypatch.setattr(flow_routes, "_save", lambda flows: saved.extend(flows))
    monkeypatch.setattr(flow_routes, "_authorize_flow_in_workspace", lambda ws, fid: authorized.append(ws))
    flow = asyncio.run(flow_routes.create_flow(FlowCreate(name="Coffee", workspace="zerno")))
    assert flow["workspace"] == "zerno" and saved[0]["workspace"] == "zerno"
    assert authorized == ["zerno"]
    assert asyncio.run(flow_routes.create_flow(FlowCreate(name="Tea")))["workspace"] == "default"


def test_binding_adds_to_an_allowlist_but_starts_none(monkeypatch):
    meta = {"open": {}, "listed": {"allowed_flows": ["other"]}}
    updates = []
    monkeypatch.setattr(flow_routes, "get_workspace_metadata", lambda ws: meta[ws])
    monkeypatch.setattr(flow_routes, "update_workspace_metadata", lambda ws, u: updates.append((ws, u)))
    flow_routes._authorize_flow_in_workspace("open", "f1")
    flow_routes._authorize_flow_in_workspace("listed", "f1")
    assert updates == [("listed", {"allowed_flows": ["other", "f1"]})]
