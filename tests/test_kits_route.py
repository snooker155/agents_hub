"""The kits route end to end (dashboard/backend/routes/kits.py, kits/):

``GET /api/kits``, ``GET /api/kits/{id}`` (manifest + plan), and
``POST /api/kits/{id}/install`` (dry run, a real install, a reinstall that
reports unchanged, and a second workspace getting its own namespaced
agents instead of colliding with the first).
"""
from __future__ import annotations

import uuid

import pytest

from agents.registry import get_agent, replace_all_raw


@pytest.fixture(autouse=True)
def fresh_registry():
    replace_all_raw([])
    yield
    replace_all_raw([])


@pytest.fixture(autouse=True)
def isolated_definitions(tmp_path, monkeypatch):
    from agents import prompt_assembly
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    from agents.agent_factory import get_factory
    monkeypatch.setattr(get_factory(), "definitions_dir", defs)
    return defs


@pytest.fixture(autouse=True)
def single_auth(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture
def ws():
    return f"kits-{uuid.uuid4().hex[:8]}"


def test_list_kits_includes_the_three_shipped_kits_with_connector_status():
    from dashboard.backend.routes.kits import list_kits_route
    import asyncio

    rows = asyncio.run(list_kits_route())
    ids = {r["id"] for r in rows}
    assert {"support", "finance", "recruiting"} <= ids
    support = next(r for r in rows if r["id"] == "support")
    assert support["connectors"]["required"] == [{"name": "mail", "configured": False}]


def test_get_unknown_kit_is_404(client):
    r = client.get("/api/kits/no-such-kit")
    assert r.status_code == 404


def test_get_kit_shows_the_plan_for_a_workspace(client, ws):
    r = client.get(f"/api/kits/support?workspace={ws}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "support"
    assert {"kind": "agent", "key": f"support_triage@{ws}"} in body["resources"]
    actions = {c["address"]: c["action"] for c in body["plan"]["changes"]}
    assert actions[f"agent/support_triage@{ws}"] == "create"


def test_install_dry_run_does_not_create_anything(client, ws):
    r = client.post("/api/kits/support/install", json={"workspace": ws, "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "result" not in body
    actions = {c["address"]: c["action"] for c in body["plan"]["changes"]}
    assert actions[f"agent/support_triage@{ws}"] == "create"
    assert get_agent(f"support_triage@{ws}") is None


def test_install_then_reinstall_is_unchanged(client, ws):
    r = client.post("/api/kits/support/install", json={"workspace": ws})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"]["ok"] is True
    namespaced_id = f"support_triage@{ws}"
    created = get_agent(namespaced_id)
    assert created is not None
    assert created.owner_workspace == ws
    # handoffs were rewritten to the namespaced sibling, not left as "support_resolver"
    assert created.handoffs == [f"support_resolver@{ws}"]

    r2 = client.post("/api/kits/support/install", json={"workspace": ws})
    assert r2.status_code == 200, r2.text
    actions2 = {c["address"]: c["action"] for c in r2.json()["plan"]["changes"]}
    assert set(actions2.values()) == {"unchanged"}


def test_two_workspaces_installing_the_same_kit_do_not_collide(client):
    ws_a, ws_b = f"kits-a-{uuid.uuid4().hex[:6]}", f"kits-b-{uuid.uuid4().hex[:6]}"
    ra = client.post("/api/kits/support/install", json={"workspace": ws_a})
    rb = client.post("/api/kits/support/install", json={"workspace": ws_b})
    assert ra.status_code == 200 and rb.status_code == 200
    assert get_agent(f"support_triage@{ws_a}") is not None
    assert get_agent(f"support_triage@{ws_b}") is not None
    assert get_agent(f"support_triage@{ws_a}").owner_workspace == ws_a
    assert get_agent(f"support_triage@{ws_b}").owner_workspace == ws_b


def test_install_into_default_workspace_keeps_the_kits_own_ids(client):
    r = client.post("/api/kits/finance/install", json={"workspace": None})
    assert r.status_code == 200, r.text
    assert get_agent("finance_analyst") is not None
    assert get_agent("finance_reviewer") is not None


def test_installed_agent_carries_its_default_outcome(client, ws):
    client.post("/api/kits/recruiting/install", json={"workspace": ws})
    screener = get_agent(f"recruiting_screener@{ws}")
    assert screener is not None
    assert screener.default_outcome is not None
    assert screener.default_outcome["rubric"]


def test_databases_counts_as_configured_only_with_a_connection(monkeypatch):
    """The databases connector has no credentials (its generic config always
    reads configured); for a kit it is configured once the workspace has a
    connection."""
    import kits
    from connectors.databases import store as db_store

    monkeypatch.setattr(db_store, "list_connections", lambda workspace=None: [])
    assert kits.connector_status("databases", "w") is False
    monkeypatch.setattr(db_store, "list_connections",
                        lambda workspace=None: [{"id": "c1", "workspace": workspace}])
    assert kits.connector_status("databases", "w") is True
