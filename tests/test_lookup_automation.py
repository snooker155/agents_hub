"""
``hub_lookup`` / ``hub_action``, automation kinds: guardrail, tool (chat/lookup_kinds/automation.py; the assistant plan, stage 5,
wave 2).

Fixtures copied from tests/test_hub_lookup.py so this file stands on its own.
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


def _member_of(user_id: str, workspace: str, role: str = "editor") -> None:
    """One operator holds every workspace: nothing to add."""


# ── guardrail ────────────────────────────────────────────────────────────────

def _guardrail(workspace, name="Block SSNs", **extra):
    from guardrails import service as guardrails_service
    data = {"name": name, "workspace": workspace, "kind": "regex", "config": {"pattern": r"\d{3}-\d{2}-\d{4}"}}
    data.update(extra)
    return guardrails_service.create_guardrail(data)


def test_guardrail_list_card_and_events_are_counts_only(multi, hub):
    from guardrails import store as guardrails_store
    bob = _user("bob")
    _member_of(bob, "team")
    g = _guardrail("team")
    guardrails_store.add_event({"workspace": "team", "guardrail_id": g.id, "guardrail_name": g.name,
                                "kind": "regex", "action": "block", "reason": "matched",
                                "excerpt": "123-45-6789 is secret", "at": "2026-10-04T10:00:00+00:00"})

    listed = lookup.lookup("guardrail", workspace="team", user_id=bob)
    assert [row["id"] for row in listed["items"]] == [g.id]

    card = lookup.lookup("guardrail", entity_id=g.id, workspace="team", user_id=bob)
    assert card["fields"]["recent_events_by_action"] == {"block": 1}
    blob = str(card)
    assert "123-45-6789" not in blob and "secret" not in blob


def test_global_guardrail_is_visible_everywhere_but_not_actioned_here(multi, hub):
    bob = _user("bob")
    _member_of(bob, "team")
    g = _guardrail(None, name="Global PII guard", kind="pii", config={})

    card = lookup.lookup("guardrail", entity_id=g.id, workspace="team", user_id=bob)
    assert card["fields"]["workspace"] == "global"

    with pytest.raises(lookup.LookupError_) as err:
        actions.perform("guardrail", "disable", g.id, workspace="team", user_id=bob)
    assert err.value.code == "not_found"


def test_guardrail_enable_and_disable_actions(multi, hub):
    bob = _user("bob")
    carol = _user("carol")
    _member_of(bob, "team", "editor")
    _member_of(carol, "team", "viewer")
    g = _guardrail("team")

    sentence = actions.describe("guardrail", "disable", g.id, workspace="team", user_id=bob)
    assert g.name in sentence


    actions.perform("guardrail", "disable", g.id, workspace="team", user_id=bob)
    from guardrails import service as guardrails_service
    assert guardrails_service.require_guardrail(g.id).enabled is False

    with pytest.raises(lookup.LookupError_) as conflict:
        actions.perform("guardrail", "disable", g.id, workspace="team", user_id=bob)
    assert conflict.value.code == "conflict"

    actions.perform("guardrail", "enable", g.id, workspace="team", user_id=bob)
    assert guardrails_service.require_guardrail(g.id).enabled is True


def test_guardrail_elsewhere_is_not_found(multi, hub):
    bob = _user("bob")
    _member_of(bob, "team")
    g = _guardrail("other")
    with pytest.raises(lookup.LookupError_) as err:
        lookup.lookup("guardrail", entity_id=g.id, workspace="team", user_id=bob)
    assert err.value.code == "not_found"


# ── tool ─────────────────────────────────────────────────────────────────────

def _add_agent_to_workspace(workspace: str, agent_id: str) -> None:
    from workspace import get_workspace_metadata, update_workspace_metadata
    allowed = list(get_workspace_metadata(workspace).get("allowed_agents") or [])
    if agent_id not in allowed:
        allowed.append(agent_id)
    update_workspace_metadata(workspace, {"allowed_agents": allowed})


def test_tool_list_and_card(single, hub):
    from agents.registry import AgentSpec, add_agent
    add_agent(AgentSpec(id="holder", name="Holder", type="langchain",
                        entrypoint="agents.definitions.demo:build", owner_workspace="team",
                        tools=["hub_action"]))
    _add_agent_to_workspace("team", "holder")

    listed = lookup.lookup("tool", query="hub_action", workspace="team")
    assert any(r["id"] == "hub_action" for r in listed["items"])

    card = lookup.lookup("tool", entity_id="hub_action", workspace="team")
    assert card["fields"]["always_needs_approval"] is True
    assert card["fields"]["category"] == "service_ops"
    assert "holder" in card["fields"]["held_by"].get("team", [])
    assert card["url"] == "/tools"


def test_tool_card_counts_recent_decisions_without_their_input(multi, hub):
    from tools import permission_policy as policy
    bob = _user("bob")
    _member_of(bob, "team")
    policy.record(policy.Decision(tool="hub_action", mode="always_ask", decision="allow", by="policy"),
                 agent_id="assistant", workspace="team", tool_input={"kind": "service", "action": "pause"})
    policy.record(policy.Decision(tool="hub_action", mode="always_ask", decision="deny", by="policy"),
                 agent_id="assistant", workspace="team")

    card = lookup.lookup("tool", entity_id="hub_action", workspace="team", user_id=bob)
    assert card["fields"]["recent_decisions_by_outcome"] == {"allow": 1, "deny": 1}
    assert "input" not in str(card["fields"]["recent_decisions_by_outcome"])
    assert set(card["fields"]) == {
        "tool_id", "category", "description", "requires_workspace", "capabilities",
        "always_needs_approval", "needs_approval_when_gate_on", "default_mode", "default_mode_source", "held_by",
        "recent_decisions_by_outcome",
    }


def test_tools_that_ask_first_are_found_by_approval(single, hub):
    rows = lookup.lookup("tool", query="approval", limit=30)["items"]
    ids = {r["id"] for r in rows}
    assert "hub_action" in ids and "delete_workspace" in ids
    assert next(r for r in rows if r["id"] == "hub_action")["subtitle"].endswith("always needs approval")
