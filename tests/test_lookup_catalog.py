"""
``hub_lookup``'s catalog kinds (chat/lookup_kinds/catalog.py; the assistant
plan, stage 5, wave 2): connections, skills, MCP servers, the embeddable
widget, the registry/marketplace and the person's own account.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from chat import actions, lookup  # noqa: E402

PASSWORD = "hunter2-but-longer"


@pytest.fixture
def multi(monkeypatch):
    pass


@pytest.fixture
def single(monkeypatch):
    pass


@pytest.fixture
def hub():
    from common.bootstrap import seed_registry_from_bootstrap
    from workspace import create_workspace_folder
    seed_registry_from_bootstrap()
    for ws in ("default", "team", "other"):
        create_workspace_folder(ws)


def _user(username: str, role: str = "member") -> str:
    """One operator: every name is the local operator."""
    return "local"


def _member_of(user_id: str, *workspaces: str) -> None:
    """One operator holds every workspace: nothing to add."""


# ── connections ──────────────────────────────────────────────────────────────

# ── skills ───────────────────────────────────────────────────────────────────

def test_skill_list_card_and_visibility(single, hub):
    from memory.procedural import Procedure, ProcedureStore
    ProcedureStore("team").add(Procedure(name="Weekly report", description="when asked for the weekly report",
                                        steps=["Draft it"], workspace="team"))
    ProcedureStore("other").add(Procedure(name="Elsewhere", description="d", steps=["x"], workspace="other"))

    listed = lookup.lookup("skill", workspace="team")
    assert [r["label"] for r in listed["items"]] == ["Weekly report"]
    skill_id = listed["items"][0]["id"]

    card = lookup.lookup("skill", entity_id=skill_id, workspace="team")
    assert card["url"] == "/skills" and card["fields"]["steps_count"] == 1
    assert "body" not in card["fields"] and "steps" not in card["fields"]

    other_id = lookup.lookup("skill", workspace="other")["items"][0]["id"]
    with pytest.raises(lookup.LookupError_) as err:
        lookup.lookup("skill", entity_id=other_id, workspace="team")
    assert err.value.code == "not_found"


# ── MCP servers ──────────────────────────────────────────────────────────────

def test_mcp_list_card_and_visibility(multi, hub):
    from mcp_client import store as mcp_store
    bob = _user("bob")
    _member_of(bob, "team")
    mcp_store.create_server("team", {"id": "github", "name": "GitHub", "transport": "stdio",
                                     "command": "mcp-github", "enabled": True})
    mcp_store.create_server("other", {"id": "github", "name": "Other GitHub", "transport": "stdio",
                                      "command": "x", "enabled": True})

    listed = lookup.lookup("mcp", workspace="team", user_id=bob)
    assert [r["id"] for r in listed["items"]] == ["team/github"]

    card = lookup.lookup("mcp", entity_id="team/github", workspace="team", user_id=bob)
    assert card["url"] == "/mcp" and card["fields"]["enabled"] is True
    for secret_field in ("command", "args", "url", "headers", "env"):
        assert secret_field not in card["fields"]

    with pytest.raises(lookup.LookupError_) as err:
        lookup.lookup("mcp", entity_id="other/github", workspace="team", user_id=bob)
    assert err.value.code == "not_found"


def test_mcp_enable_disable_actions(multi, hub):
    from mcp_client import store as mcp_store
    bob = _user("bob")
    _member_of(bob, "team")
    mcp_store.create_server("team", {"id": "github", "name": "GitHub", "transport": "stdio",
                                     "command": "mcp-github", "enabled": True})

    sentence = actions.describe("mcp", "disable", "team/github", workspace="team", user_id=bob)
    assert "GitHub" in sentence and "team" in sentence

    result = actions.perform("mcp", "disable", "team/github", workspace="team", user_id=bob)
    assert result["done"] and mcp_store.get_server("team", "github")["enabled"] is False

    with pytest.raises(lookup.LookupError_) as again:
        actions.perform("mcp", "disable", "team/github", workspace="team", user_id=bob)
    assert again.value.code == "conflict"


    with pytest.raises(lookup.LookupError_) as not_found:
        actions.perform("mcp", "enable", "other/github", workspace="team", user_id=bob)
    assert not_found.value.code == "not_found"


# ── the embeddable widget ────────────────────────────────────────────────────

# ── registry / marketplace ───────────────────────────────────────────────────

def test_registry_list_card_and_sharing_crosses_workspaces(multi, hub):
    from flow import store as flow_store
    from mcp_client import catalog as mcp_catalog
    from memory.procedural import Procedure, ProcedureStore
    bob = _user("bob")
    _member_of(bob, "team")

    flow_store.save_flow({"id": "flow-team", "name": "Team Flow", "workspace": "team",
                          "nodes": [], "edges": []})
    flow_store.save_flow({"id": "flow-other", "name": "Other Flow", "workspace": "other",
                          "nodes": [], "edges": []})
    skill = Procedure(name="Weekly report", description="d", steps=["s"], workspace="team")
    ProcedureStore("team").add(skill)
    mcp_catalog.create({"id": "vetted", "name": "Vetted Server", "transport": "stdio", "command": "x"},
                       owner_user=bob, is_admin=False)

    listed = lookup.lookup("registry", workspace="team", user_id=bob)
    ids = {r["id"] for r in listed["items"]}
    assert "flow:flow-team" in ids
    assert "flow:flow-other" not in ids
    assert f"skill:{skill.id}" in ids
    # the MCP allowlist has no workspace of its own: visible from anywhere.
    assert "mcp_catalog:vetted" in ids

    card = lookup.lookup("registry", entity_id="flow:flow-team", workspace="team", user_id=bob)
    assert card["url"] == "/marketplace" and card["fields"]["name"] == "Team Flow"

    with pytest.raises(lookup.LookupError_) as err:
        lookup.lookup("registry", entity_id="flow:flow-other", workspace="team", user_id=bob)
    assert err.value.code == "not_found"

    # publishing (sharing) is meant to cross workspaces, same as the
    # Marketplace page: it becomes visible from "team" without bob joining
    # "other".
    other_flow = flow_store.get_flow("flow-other")
    other_flow["shared"] = True
    flow_store.save_flow(other_flow)
    now_visible = lookup.lookup("registry", entity_id="flow:flow-other", workspace="team", user_id=bob)
    assert now_visible["fields"]["shared"] is True


def test_registry_has_no_actions():
    assert actions.actions_of("registry") == []


# ── the person's own account ─────────────────────────────────────────────────

