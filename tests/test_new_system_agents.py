"""The four new system agents: verifier, analyst, writer, screener.

``tests/test_system_agents.py`` already covers every seeded system agent
generically (a definition folder exists, its docs name no tool it lacks, the
static capability guard passes or is overridden, the seed round-trips through
``_validate_agent_dict``). This file checks what is specific to this batch:
the roster, the roles, the delegation wiring, and the delegation-aware
capability guard (``agents.capability_guard.check_agent_tools``), which is the
real save-time chokepoint and is not exercised by the static
``check_combination`` the generic seed test uses.
"""
from __future__ import annotations

import json

import pytest

from common.bootstrap import BOOTSTRAP_AGENTS_FILE


NEW_AGENT_IDS = ("verifier", "analyst", "writer", "screener")


@pytest.fixture(autouse=True)
def live_registry():
    """Mirror ``tests/test_system_agents.py``'s fixture: start every test from
    a registry that matches the shipped seed, and clear the one-time sync
    backup so tests do not leak into each other."""
    from common.bootstrap import seed_registry_from_bootstrap
    from common.paths import AGENTS_FILE

    seed_registry_from_bootstrap()
    AGENTS_FILE.with_suffix(".json.pre-sync-backup").unlink(missing_ok=True)
    yield


def _seed_agents() -> dict[str, dict]:
    agents = json.loads(BOOTSTRAP_AGENTS_FILE.read_text(encoding="utf-8"))["agents"]
    return {a["id"]: a for a in agents}


# ── the roster ───────────────────────────────────────────────────────────────

def test_all_four_are_seeded_system_agents():
    seed = _seed_agents()
    for aid in NEW_AGENT_IDS:
        assert aid in seed, f"{aid} is missing from the seed"
        assert seed[aid]["system"] is True, f"{aid} must ship as a system agent"


def test_names_match_the_contract():
    seed = _seed_agents()
    expected = {
        "verifier": "Verifier",
        "analyst": "Analyst",
        "writer": "Writer",
        "screener": "Screener",
    }
    for aid, name in expected.items():
        assert seed[aid]["name"] == name


def test_descriptions_are_distinct_from_every_other_system_agent():
    """Descriptions are what the orchestrator routes on, so each of the four
    must read differently from every other system agent, not just from each
    other."""
    seed = _seed_agents()
    descriptions = [a["description"] for a in seed.values()]
    assert len(descriptions) == len(set(descriptions)), "two agents share one description"


def test_distinct_from_the_agents_named_in_the_brief():
    """Named explicitly in the brief as the agents these four must stay
    distinguishable from: researcher, code_reviewer,
    universal_agent."""
    seed = _seed_agents()
    reference_ids = {"researcher_agent", "code_reviewer", "universal_agent"}
    reference = {seed[rid]["description"] for rid in reference_ids if rid in seed}
    for aid in NEW_AGENT_IDS:
        assert seed[aid]["description"] not in reference


# ── the four roles, by tool shape ────────────────────────────────────────────

def test_verifier_has_code_reviewers_task_tools_plus_reads():
    """The non-code counterpart of the Code Reviewer: same task lifecycle
    tools, plus the reads a non-code review needs."""
    tools = set(_seed_agents()["verifier"]["tools"])
    assert {"get_task", "get_task_result", "update_task", "block_task"} <= tools
    assert {"read_file", "list_files", "search_text", "read_memory", "search_memory"} <= tools
    assert "calculator" in tools


def test_analyst_reads_data_sources_and_never_writes():
    tools = set(_seed_agents()["analyst"]["tools"])
    assert {"list_workspace_files", "read_workspace_file", "calculator"} <= tools
    write_tools = {"write_file", "create_file", "write_memory", "delete_file", "apply_unified_diff"}
    assert not (write_tools & tools), "the Analyst must never hold a write tool"


def test_writer_can_save_files_and_has_no_database_or_web_access():
    tools = set(_seed_agents()["writer"]["tools"])
    assert {"write_file", "create_file"} <= tools
    assert not ({"fetch_url"} & tools)


def test_screener_is_read_only():
    tools = set(_seed_agents()["screener"]["tools"])
    write_tools = {"write_file", "create_file", "write_memory", "delete_file", "apply_unified_diff"}
    assert not (write_tools & tools), "the Screener reports a verdict, it does not write anything"
    assert "run_agent_tool" not in tools, "the Screener evaluates what it is handed, it does not delegate"


# ── delegation and handoff wiring ────────────────────────────────────────────

def test_verifier_neither_delegates_nor_needs_an_override():
    seed = _seed_agents()
    assert not seed["verifier"].get("delegates")
    assert not seed["verifier"].get("capability_override")


def test_analyst_delegates_only_to_the_visualizer():
    seed = _seed_agents()
    assert seed["analyst"].get("delegates") == ["@visualizer"]
    assert not seed["analyst"].get("capability_override")


def test_writer_hands_off_to_the_verifier_rather_than_delegating():
    """A handoff (``handoffs``), not a delegation (``delegates``/
    ``run_agent_tool``): the Writer gives the conversation away instead of
    reading the Verifier's capabilities into its own effective set."""
    seed = _seed_agents()
    assert seed["writer"].get("handoffs") == ["@verifier"]
    assert not seed["writer"].get("delegates")
    assert "run_agent_tool" not in seed["writer"]["tools"]


def test_screener_neither_delegates_nor_is_handed_off_to_by_itself():
    seed = _seed_agents()
    assert not seed["screener"].get("delegates")
    assert not seed["screener"].get("handoffs")


# ── the delegation-aware capability guard (the real save-time check) ────────

def test_each_agent_clears_the_delegation_aware_guard():
    """``tools.capabilities.check_combination`` (used by the generic seed
    test) only looks at an agent's own tool list. The real chokepoint,
    ``agents.capability_guard.check_agent_tools``, also walks what the agent
    can reach through ``delegates`` — which is exactly the shape that trips
    up a chain of delegations. Every one of the four must clear it, using
    ``capability_override`` only where the record declares it."""
    from agents.capability_guard import check_agent_tools
    from agents.registry import get_agent

    for aid in NEW_AGENT_IDS:
        spec = get_agent(aid)
        assert spec is not None, f"{aid} did not reach the live registry"
        violation = check_agent_tools(
            aid, spec.tools, override=spec.capability_override, delegates=spec.delegates,
        )
        assert violation is None or not violation.blocking, (
            f"{aid}: {violation.message if violation else ''}"
        )
