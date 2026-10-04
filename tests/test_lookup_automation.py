"""
``hub_lookup`` / ``hub_action``, automation kinds: watcher, pulse, eval,
guardrail, tool (chat/lookup_kinds/automation.py; the assistant plan, stage 5,
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
from common import identity  # noqa: E402

PASSWORD = "hunter2-but-longer"


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


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


def _member_of(user_id: str, workspace: str, role: str = "editor") -> None:
    identity.set_member(workspace, user_id, role)


# ── watcher ──────────────────────────────────────────────────────────────────

def _watcher(workspace: str, **extra):
    from watchers import service as watchers_service
    data = {"name": "Inbox watch", "kind": "http", "config": {"url": "https://example.com/status"}}
    data.update(extra)
    return watchers_service.create(workspace, data)


def test_watcher_list_card_url_and_reach(multi, hub):
    bob = _user("bob")
    _member_of(bob, "team")
    mine = _watcher("team")
    theirs = _watcher("other")

    listed = lookup.lookup("watcher", workspace="team", user_id=bob)
    assert [r["id"] for r in listed["items"]] == [mine.id]
    assert listed["items"][0]["url"] == "/watchers"

    card = lookup.lookup("watcher", entity_id=mine.id, workspace="team", user_id=bob)
    assert card["fields"]["kind"] == "http"
    assert card["fields"]["target"] == "example.com"
    assert card["url"] == "/watchers"

    with pytest.raises(lookup.LookupError_) as err:
        lookup.lookup("watcher", entity_id=theirs.id, workspace="team", user_id=bob)
    assert err.value.code == "not_found"


def test_watcher_card_never_leaks_fetched_content_or_secrets(single, hub):
    from watchers import service as watchers_service
    w = _watcher("team", config={"url": "https://example.com/status", "headers_secret": "api-token"})
    watchers_service.probe_once(w.id)  # takes a baseline; would store a preview of the body
    card = lookup.lookup("watcher", entity_id=w.id, workspace="team")
    blob = str(card)
    assert "headers_secret" not in blob or "api-token" not in blob
    assert "preview" not in card["fields"]
    assert "state" in card["fields"] and isinstance(card["fields"]["state"], str)


def test_watcher_pause_and_resume_actions(multi, hub):
    bob = _user("bob")
    carol = _user("carol")
    _member_of(bob, "team", "editor")
    _member_of(carol, "team", "viewer")
    w = _watcher("team")

    sentence = actions.describe("watcher", "pause", w.id, workspace="team", user_id=bob)
    assert w.name in sentence and "team" in sentence

    with pytest.raises(lookup.LookupError_) as refused:
        actions.perform("watcher", "pause", w.id, workspace="team", user_id=carol)
    assert refused.value.code == "forbidden"

    out = actions.perform("watcher", "pause", w.id, workspace="team", user_id=bob)
    assert out["done"] is True
    from watchers import service as watchers_service
    assert watchers_service.require(w.id).paused_reason == "manual"

    with pytest.raises(lookup.LookupError_) as again:
        actions.perform("watcher", "pause", w.id, workspace="team", user_id=bob)
    assert again.value.code == "conflict"

    actions.perform("watcher", "resume", w.id, workspace="team", user_id=bob)
    assert watchers_service.require(w.id).paused_reason is None

    with pytest.raises(lookup.LookupError_) as not_paused:
        actions.perform("watcher", "resume", w.id, workspace="team", user_id=bob)
    assert not_paused.value.code == "conflict"


def test_watcher_action_on_another_workspace_is_not_found(multi, hub):
    bob = _user("bob")
    _member_of(bob, "team")
    theirs = _watcher("other")
    with pytest.raises(lookup.LookupError_) as err:
        actions.perform("watcher", "pause", theirs.id, workspace="all", user_id=bob)
    assert err.value.code == "not_found"


# ── pulse ────────────────────────────────────────────────────────────────────

def _pulse_agent(workspace: str, agent_id: str = "pulsebot"):
    from agents.registry import AgentSpec, add_agent
    from proactive import service as proactive_service
    spec = AgentSpec(id=agent_id, name="Pulse Bot", type="langchain",
                     entrypoint="agents.definitions.demo:build", owner_workspace=workspace)
    add_agent(spec)
    proactive_service.save_profile(agent_id, {
        "enabled": True, "interval_minutes": 60, "workspace": workspace, "brief": "Check the inbox.",
    })
    return spec


def test_pulse_list_card_and_hides_the_brief(multi, hub):
    bob = _user("bob")
    _member_of(bob, "team")
    _pulse_agent("team")

    listed = lookup.lookup("pulse", workspace="team", user_id=bob)
    assert [r["id"] for r in listed["items"]] == ["pulsebot"]
    assert listed["items"][0]["url"] == "/agents/pulsebot"

    card = lookup.lookup("pulse", entity_id="pulsebot", workspace="team", user_id=bob)
    assert card["fields"]["enabled"] is True
    assert card["fields"]["job_status"] == "scheduled"
    blob = str(card)
    assert "Check the inbox" not in blob


def test_pulse_invisible_from_another_workspace(multi, hub):
    bob = _user("bob")
    _member_of(bob, "team")
    _pulse_agent("other", "otherbot")
    with pytest.raises(lookup.LookupError_) as err:
        lookup.lookup("pulse", entity_id="otherbot", workspace="team", user_id=bob)
    assert err.value.code == "not_found"


def test_pulse_pause_and_resume_actions(multi, hub):
    bob = _user("bob")
    carol = _user("carol")
    _member_of(bob, "team", "editor")
    _member_of(carol, "team", "viewer")
    _pulse_agent("team")

    sentence = actions.describe("pulse", "pause", "pulsebot", workspace="team", user_id=bob)
    assert "Pulse Bot" in sentence

    with pytest.raises(lookup.LookupError_) as refused:
        actions.perform("pulse", "pause", "pulsebot", workspace="team", user_id=carol)
    assert refused.value.code == "forbidden"

    out = actions.perform("pulse", "pause", "pulsebot", workspace="team", user_id=bob)
    assert out["done"] is True
    from agents.registry import get_agent
    from proactive import service as proactive_service
    from plans import service as plans
    profile = proactive_service.profile_of(get_agent("pulsebot"))
    assert plans.get_job(profile["job_id"]).status.value == "paused"

    actions.perform("pulse", "resume", "pulsebot", workspace="team", user_id=bob)
    assert plans.get_job(profile["job_id"]).status.value == "scheduled"

    with pytest.raises(lookup.LookupError_) as not_paused:
        actions.perform("pulse", "resume", "pulsebot", workspace="team", user_id=bob)
    assert not_paused.value.code == "conflict"


# ── eval ─────────────────────────────────────────────────────────────────────

def _eval_set(workspace: str, name: str = "Smoke set"):
    from evals.models import EvalSet
    from evals import store as eval_store
    e = EvalSet(name=name, workspace=workspace, target={"kind": "agent", "id": "assistant"})
    return eval_store.save_eval_set(e)


def _eval_run(eval_set_id: str, workspace: str, status: str = "completed", mode: str = "live"):
    from evals.models import EvalRun
    from evals import store as eval_store
    r = EvalRun(eval_set_id=eval_set_id, workspace=workspace, status=status, mode=mode,
               summary={"baseline": {"score": 0.8, "passed": 4, "total": 5, "cost": 0.02}}, total_cost=0.02)
    return eval_store.save_eval_run(r)


def test_eval_set_and_run_cards_by_either_id_shape(multi, hub):
    bob = _user("bob")
    _member_of(bob, "team")
    e = _eval_set("team")
    r = _eval_run(e.eval_set_id, "team")

    listed = lookup.lookup("eval", workspace="team", user_id=bob)
    assert [row["id"] for row in listed["items"]] == [e.eval_set_id]

    set_card = lookup.lookup("eval", entity_id=e.eval_set_id, workspace="team", user_id=bob)
    assert set_card["fields"]["name"] == "Smoke set"
    assert set_card["fields"]["recent_runs"][0]["eval_run_id"] == r.eval_run_id
    assert set_card["url"] == "/evals"

    run_card = lookup.lookup("eval", entity_id=r.eval_run_id, workspace="team", user_id=bob)
    assert run_card["fields"]["eval_set_name"] == "Smoke set"
    assert run_card["fields"]["status"] == "completed"
    assert run_card["fields"]["scores"]["baseline"]["score"] == 0.8


def test_eval_run_elsewhere_is_not_found(multi, hub):
    bob = _user("bob")
    _member_of(bob, "team")
    e = _eval_set("other")
    r = _eval_run(e.eval_set_id, "other")
    with pytest.raises(lookup.LookupError_) as err:
        lookup.lookup("eval", entity_id=r.eval_run_id, workspace="team", user_id=bob)
    assert err.value.code == "not_found"


def test_eval_cancel_action(multi, hub):
    bob = _user("bob")
    carol = _user("carol")
    _member_of(bob, "team", "editor")
    _member_of(carol, "team", "viewer")
    e = _eval_set("team")
    pending = _eval_run(e.eval_set_id, "team", status="batch_pending", mode="batch")

    sentence = actions.describe("eval", "cancel", pending.eval_run_id, workspace="team", user_id=bob)
    assert "Smoke set" in sentence

    with pytest.raises(lookup.LookupError_) as refused:
        actions.perform("eval", "cancel", pending.eval_run_id, workspace="team", user_id=carol)
    assert refused.value.code == "forbidden"

    out = actions.perform("eval", "cancel", pending.eval_run_id, workspace="team", user_id=bob)
    assert out["done"] is True

    done = _eval_run(e.eval_set_id, "team", status="completed")
    with pytest.raises(lookup.LookupError_) as conflict:
        actions.perform("eval", "cancel", done.eval_run_id, workspace="team", user_id=bob)
    assert conflict.value.code == "conflict"


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

    with pytest.raises(lookup.LookupError_) as refused:
        actions.perform("guardrail", "disable", g.id, workspace="team", user_id=carol)
    assert refused.value.code == "forbidden"

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
                 agent_id="assistant", workspace="team", tool_input={"kind": "watcher", "action": "pause"})
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
