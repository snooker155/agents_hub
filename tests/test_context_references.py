"""
Context references: hub records the user attaches to a chat message.

Covers the three things the feature promises — the picker lists what exists,
the pick is rendered into the prompt even when the agent owns no tool for that
kind, and a reference that has gone stale drops out instead of breaking the
send.
"""
import pytest

from chat.context import build_chat_context
from chat.models import ChatReference, ChatRequest
from chat.references import (
    KINDS,
    agent_can_load,
    build_reference_lines,
    kind_catalog,
    list_entities,
    render_entity,
    resolve_references,
)


@pytest.fixture
def a_task():
    from tasks.service import create_task
    return create_task(title="Ship the report", description="With charts", workspace="dev")


@pytest.fixture
def a_scenario():
    from playground.models import Role, Scenario
    from playground.store import save_scenario
    return save_scenario(Scenario(
        name="Market day",
        description="Traders haggle",
        workspace="dev",
        environment="market",
        roles=[Role(agent_id="basic_agent", name="Trader", goal="Sell high")],
    ))


@pytest.fixture
def agent_with_tools(monkeypatch):
    """Register a stub agent spec so the tool-hint rule can be exercised without
    the suite depending on which agents happen to be bootstrapped."""
    from types import SimpleNamespace
    import agents.registry as registry

    specs: dict = {}
    monkeypatch.setattr(registry, "get_agent", lambda agent_id: specs.get(agent_id))

    def _register(agent_id, tools):
        specs[agent_id] = SimpleNamespace(id=agent_id, name=agent_id, tools=list(tools))
        return agent_id

    return _register


def _request(*refs, agent_id="orchestrator", message="look at this"):
    return ChatRequest(
        agent_id=agent_id,
        message=message,
        references=[ChatReference(kind=k, id=str(i)) for k, i in refs],
    )


# ── the picker's catalog ─────────────────────────────────────────────────────

def test_every_catalogued_kind_is_offered_to_the_picker():
    offered = {k["kind"] for k in kind_catalog()}
    assert offered == set(KINDS)
    assert {"task", "view", "project", "scenario", "loop"} <= offered


def test_listing_finds_an_entity_and_links_to_its_page(a_task):
    rows = list_entities("task", workspace="dev")
    row = next(r for r in rows if r["id"] == str(a_task.id))
    assert "Ship the report" in row["label"]
    assert row["url"] == f"/tasks/{a_task.id}"


def test_listing_filters_by_query_and_workspace(a_task):
    assert [r["id"] for r in list_entities("task", workspace="dev", query="ship")] == [str(a_task.id)]
    assert list_entities("task", workspace="dev", query="nothing matches this") == []
    assert list_entities("task", workspace="other-workspace") == []


# ── rendering ────────────────────────────────────────────────────────────────

def test_render_carries_the_fields_an_agent_needs(a_task):
    rendered = render_entity("task", str(a_task.id))
    assert rendered["title"] == "Ship the report"
    assert "With charts" in rendered["body"]
    assert str(a_task.id) in rendered["body"]


def test_render_returns_none_for_a_deleted_entity(a_task):
    from tasks.service import delete_task
    delete_task(a_task.id)
    assert render_entity("task", str(a_task.id)) is None


def test_render_returns_none_for_an_unknown_kind():
    assert render_entity("unicorn", "whatever") is None


# ── the prompt block ─────────────────────────────────────────────────────────

def test_an_attached_entity_reaches_the_prompt(a_task):
    request = _request(("task", a_task.id))
    resolve_references(request)
    prompt, _ = build_chat_context(request)

    assert "Attached context entities" in prompt
    assert "Ship the report" in prompt
    assert "With charts" in prompt


def test_a_kind_with_no_agent_tool_is_still_attached(a_scenario, agent_with_tools):
    """The point of the explicit pick: a scenario has no loading tool at all, so
    inlining it is the only way it can reach the agent."""
    agent_with_tools("orchestrator", ["task_management", "get_flow_tool"])
    assert agent_can_load("orchestrator", "scenario") is False

    request = _request(("scenario", a_scenario.scenario_id))
    resolve_references(request)
    prompt, _ = build_chat_context(request)

    assert "Market day" in prompt
    assert "Traders haggle" in prompt
    # No tool to point at, so no tool hint is offered for it.
    assert "call `" not in prompt


def test_a_kind_the_agent_can_load_also_gets_a_tool_hint(a_task, agent_with_tools):
    # The group alias counts: the factory expands task_management into get_task.
    agent_with_tools("orchestrator", ["task_management"])
    assert agent_can_load("orchestrator", "task") is True

    request = _request(("task", a_task.id))
    resolve_references(request)
    lines = build_reference_lines(request.references, "orchestrator")

    assert any("call `get_task`" in line for line in lines)


def test_no_tool_hint_when_the_agent_lacks_the_tool(a_task, agent_with_tools):
    """Same task, an agent without task tools: the record is still inlined, but
    it is not told to call a tool it does not have."""
    agent_with_tools("basic_agent", ["filesystem"])
    request = _request(("task", a_task.id), agent_id="basic_agent")
    resolve_references(request)
    lines = build_reference_lines(request.references, "basic_agent")

    assert any("Ship the report" in line for line in lines)
    assert not any("call `" in line for line in lines)


def test_a_stale_reference_is_dropped_and_the_message_still_sends(a_task):
    from tasks.service import delete_task
    delete_task(a_task.id)

    request = _request(("task", a_task.id))
    resolve_references(request)

    assert request.references == []
    prompt, _ = build_chat_context(request)
    assert prompt.strip() == "look at this"


def test_references_lead_the_attached_files(a_task):
    from chat.models import ChatAttachment

    request = _request(("task", a_task.id))
    request.attachments = [ChatAttachment(filename="notes.txt", content="raw material")]
    resolve_references(request)
    prompt, _ = build_chat_context(request)

    assert prompt.index("Attached context entities") < prompt.index("Attached files")


def test_the_per_message_budget_is_enforced(monkeypatch, a_task):
    from chat import references

    monkeypatch.setattr(references, "MAX_TOTAL_REFERENCE_CHARS", 40)
    request = _request(("task", a_task.id), ("task", a_task.id))
    resolve_references(request)

    total = sum(len(r.content) for r in request.references)
    assert total <= 40
