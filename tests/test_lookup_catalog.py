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
from common import identity, user_budget  # noqa: E402

PASSWORD = "hunter2-but-longer"


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    monkeypatch.setattr(settings, "user_spend_limit_usd", 0.0, raising=False)
    monkeypatch.delenv(user_budget.DEFAULT_LIMIT_ENV, raising=False)


@pytest.fixture
def single(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def hub():
    from common.bootstrap import seed_registry_from_bootstrap
    from workspace import create_workspace_folder
    seed_registry_from_bootstrap()
    for ws in ("default", "team", "other"):
        create_workspace_folder(ws)


def _user(username: str, role: str = "member") -> str:
    return identity.create_user(username, PASSWORD, role=role)["id"]


def _member_of(user_id: str, *workspaces: str) -> None:
    for ws in workspaces:
        identity.set_member(ws, user_id, "editor")


# ── connections ──────────────────────────────────────────────────────────────

def test_connection_list_card_and_visibility(multi, hub):
    from connections import store as connection_store
    bob = _user("bob")
    _member_of(bob, "team")
    connection_store.create_connection(connection_id="billing", name="Billing Bot", kind="http",
                                       workspace="team")
    connection_store.create_connection(connection_id="other-conn", name="Other", workspace="other")

    listed = lookup.lookup("connection", workspace="team", user_id=bob)
    assert [r["id"] for r in listed["items"]] == ["billing"]
    assert listed["items"][0]["url"] == "/connections/billing"

    card = lookup.lookup("connection", entity_id="billing", workspace="team", user_id=bob)
    assert card["url"] == "/connections/billing" and card["fields"]["disabled"] is False

    with pytest.raises(lookup.LookupError_) as err:
        lookup.lookup("connection", entity_id="other-conn", workspace="team", user_id=bob)
    assert err.value.code == "not_found"


def test_connection_enable_disable_actions(multi, hub):
    from connections import store as connection_store
    bob = _user("bob")
    carol = _user("carol")
    _member_of(bob, "team")
    identity.set_member("team", carol, "viewer")
    connection_store.create_connection(connection_id="billing", name="Billing Bot", workspace="team")
    connection_store.create_connection(connection_id="other-conn", name="Other", workspace="other")

    sentence = actions.describe("connection", "disable", "billing", workspace="team", user_id=bob)
    assert "Billing Bot" in sentence and "team" in sentence

    result = actions.perform("connection", "disable", "billing", workspace="team", user_id=bob)
    assert result["done"] and connection_store.get_connection("billing")["disabled"] is True

    with pytest.raises(lookup.LookupError_) as again:
        actions.perform("connection", "disable", "billing", workspace="team", user_id=bob)
    assert again.value.code == "conflict"

    with pytest.raises(lookup.LookupError_) as refused:
        actions.perform("connection", "enable", "billing", workspace="team", user_id=carol)
    assert refused.value.code == "forbidden"

    with pytest.raises(lookup.LookupError_) as not_found:
        actions.perform("connection", "enable", "other-conn", workspace="team", user_id=bob)
    assert not_found.value.code == "not_found"

    ok = actions.perform("connection", "enable", "billing", workspace="team", user_id=bob)
    assert ok["done"] and connection_store.get_connection("billing")["disabled"] is False


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
    carol = _user("carol")
    _member_of(bob, "team")
    identity.set_member("team", carol, "viewer")
    mcp_store.create_server("team", {"id": "github", "name": "GitHub", "transport": "stdio",
                                     "command": "mcp-github", "enabled": True})

    sentence = actions.describe("mcp", "disable", "team/github", workspace="team", user_id=bob)
    assert "GitHub" in sentence and "team" in sentence

    result = actions.perform("mcp", "disable", "team/github", workspace="team", user_id=bob)
    assert result["done"] and mcp_store.get_server("team", "github")["enabled"] is False

    with pytest.raises(lookup.LookupError_) as again:
        actions.perform("mcp", "disable", "team/github", workspace="team", user_id=bob)
    assert again.value.code == "conflict"

    with pytest.raises(lookup.LookupError_) as refused:
        actions.perform("mcp", "enable", "team/github", workspace="team", user_id=carol)
    assert refused.value.code == "forbidden"

    with pytest.raises(lookup.LookupError_) as not_found:
        actions.perform("mcp", "enable", "other/github", workspace="team", user_id=bob)
    assert not_found.value.code == "not_found"


# ── the embeddable widget ────────────────────────────────────────────────────

def test_widget_list_card_and_visibility(multi, hub):
    from widgets import store
    from widgets.service import create_widget
    bob = _user("bob")
    _member_of(bob, "team")
    mine = create_widget({"workspace": "team", "name": "Support widget", "agent_id": "assistant",
                          "enabled": True}, owner_id=bob)
    theirs = create_widget({"workspace": "other", "name": "Other widget", "agent_id": "assistant",
                            "enabled": True}, owner_id="local")

    listed = lookup.lookup("widget", workspace="team", user_id=bob)
    assert [r["id"] for r in listed["items"]] == [mine["widget_id"]]

    card = lookup.lookup("widget", entity_id=mine["widget_id"], workspace="team", user_id=bob)
    assert card["url"] == "/widgets" and card["fields"]["enabled"] is True
    assert "public_key" not in card["fields"]

    with pytest.raises(lookup.LookupError_) as err:
        lookup.lookup("widget", entity_id=theirs["widget_id"], workspace="team", user_id=bob)
    assert err.value.code == "not_found"
    _ = store  # used for symmetry with the action test below


def test_widget_enable_disable_actions(multi, hub):
    from widgets import store
    from widgets.service import create_widget
    bob = _user("bob")
    carol = _user("carol")
    _member_of(bob, "team")
    identity.set_member("team", carol, "viewer")
    mine = create_widget({"workspace": "team", "name": "Support widget", "agent_id": "assistant",
                          "enabled": True}, owner_id=bob)
    theirs = create_widget({"workspace": "other", "name": "Other widget", "agent_id": "assistant",
                            "enabled": True}, owner_id="local")

    sentence = actions.describe("widget", "disable", mine["widget_id"], workspace="team", user_id=bob)
    assert "Support widget" in sentence and "team" in sentence

    result = actions.perform("widget", "disable", mine["widget_id"], workspace="team", user_id=bob)
    assert result["done"] and store.get_widget(mine["widget_id"])["enabled"] is False

    with pytest.raises(lookup.LookupError_) as again:
        actions.perform("widget", "disable", mine["widget_id"], workspace="team", user_id=bob)
    assert again.value.code == "conflict"

    with pytest.raises(lookup.LookupError_) as refused:
        actions.perform("widget", "enable", mine["widget_id"], workspace="team", user_id=carol)
    assert refused.value.code == "forbidden"

    with pytest.raises(lookup.LookupError_) as not_found:
        actions.perform("widget", "enable", theirs["widget_id"], workspace="team", user_id=bob)
    assert not_found.value.code == "not_found"


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
    assert card["url"] == "/registry" and card["fields"]["name"] == "Team Flow"

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

def test_account_is_the_persons_own_never_anothers(multi, hub):
    from common import api_keys
    bob = _user("bob")
    carol = _user("carol")
    _member_of(bob, "team")
    identity.set_spend_limit(bob, 25.0)
    api_keys.create_key(bob, name="My key", workspaces=["team"])

    listed = lookup.lookup("account", user_id=bob)
    assert [r["id"] for r in listed["items"]] == ["me"]

    card = lookup.lookup("account", entity_id="me", user_id=bob)
    assert card["fields"]["role"] == "member"
    assert card["fields"]["workspaces"]["team"]["role"] == "editor"
    assert card["fields"]["monthly_limit_usd"] == 25.0
    assert card["fields"]["api_keys"][0]["name"] == "My key"
    assert all("key" != k for k in card["fields"]["api_keys"][0])

    with pytest.raises(lookup.LookupError_):
        lookup.lookup("account", entity_id=bob, user_id=bob)
    with pytest.raises(lookup.LookupError_):
        lookup.lookup("account", entity_id=carol, user_id=bob)
    assert actions.actions_of("account") == []


def test_account_outside_multi_mode_is_brief(single, hub):
    card = lookup.lookup("account", entity_id="me")
    assert card["fields"]["mode"] == "single"
    assert "username" not in card["fields"] and "api_keys" not in card["fields"]
