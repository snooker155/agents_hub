"""The five new system agents: verifier, analyst, writer, sourcer, screener.

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


NEW_AGENT_IDS = ("verifier", "analyst", "writer", "sourcer", "screener")


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

def test_all_five_are_seeded_system_agents():
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
        "sourcer": "Sourcer",
        "screener": "Screener",
    }
    for aid, name in expected.items():
        assert seed[aid]["name"] == name


def test_descriptions_are_distinct_from_every_other_system_agent():
    """Descriptions are what the orchestrator routes on, so each of the five
    must read differently from every other system agent, not just from each
    other."""
    seed = _seed_agents()
    descriptions = [a["description"] for a in seed.values()]
    assert len(descriptions) == len(set(descriptions)), "two agents share one description"


def test_distinct_from_the_agents_named_in_the_brief():
    """Named explicitly in the brief as the agents these five must stay
    distinguishable from: researcher, web_searcher, code_reviewer,
    universal_agent, eval_agent."""
    seed = _seed_agents()
    reference_ids = {"researcher_agent", "web_searcher", "code_reviewer", "universal_agent", "eval_agent"}
    reference = {seed[rid]["description"] for rid in reference_ids if rid in seed}
    for aid in NEW_AGENT_IDS:
        assert seed[aid]["description"] not in reference


# ── the five roles, by tool shape ────────────────────────────────────────────

def test_verifier_has_code_reviewers_task_tools_plus_reads():
    """The non-code counterpart of the Code Reviewer: same task lifecycle
    tools, plus the reads a non-code review needs."""
    tools = set(_seed_agents()["verifier"]["tools"])
    assert {"get_task", "get_task_result", "update_task", "block_task"} <= tools
    assert {"read_file", "list_files", "search_text", "read_memory", "search_memory"} <= tools
    assert "calculator" in tools


def test_analyst_reads_data_sources_and_never_writes():
    tools = set(_seed_agents()["analyst"]["tools"])
    assert {"db_list_connections", "db_schema", "db_query", "calculator"} <= tools
    write_tools = {"write_file", "create_file", "write_memory", "delete_file", "apply_unified_diff"}
    assert not (write_tools & tools), "the Analyst must never hold a write tool"


def test_writer_can_save_files_and_has_no_database_or_web_access():
    tools = set(_seed_agents()["writer"]["tools"])
    assert {"write_file", "create_file"} <= tools
    assert not ({"db_query", "db_schema", "db_list_connections", "web_search", "fetch_url"} & tools)


def test_sourcer_holds_no_private_data_tool():
    """The brief's own caution: a finder that reaches the web must hold no
    private-data tool, or the combination is the lethal trifecta one hop
    away. Checked directly against the tool capability table rather than by
    name, so a future tool rename cannot silently reopen this."""
    from tools.capabilities import READS_PRIVATE, capabilities_of

    tools = _seed_agents()["sourcer"]["tools"]
    assert READS_PRIVATE not in capabilities_of(tools), (
        "sourcer must hold no tool that reads private data directly"
    )


def test_screener_is_read_only():
    tools = set(_seed_agents()["screener"]["tools"])
    write_tools = {"write_file", "create_file", "write_memory", "delete_file", "apply_unified_diff"}
    assert not (write_tools & tools), "the Screener reports a verdict, it does not write anything"
    assert "run_agent_tool" not in tools, "the Screener evaluates what it is handed, it does not delegate"


# ── delegation and handoff wiring ────────────────────────────────────────────

def test_verifier_delegates_only_to_the_web_searcher():
    seed = _seed_agents()
    assert seed["verifier"].get("delegates") == ["@web_search"]
    assert seed["verifier"].get("capability_override") is True


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


def test_sourcer_delegates_to_the_web_searcher_and_hands_off_to_the_screener():
    seed = _seed_agents()
    assert seed["sourcer"].get("delegates") == ["@web_search"]
    assert seed["sourcer"].get("handoffs") == ["screener"]
    assert not seed["sourcer"].get("capability_override")


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
    up a chain like writer -> verifier -> web_searcher if handoffs were
    delegations instead. Every one of the five must clear it, using
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


def test_verifiers_override_is_only_needed_for_the_delegated_hop():
    """Confirms the override exists for the researcher-shaped reason, not
    because the Verifier's own tool list is unsafe on its own: without the
    web_searcher delegation reachable, its own tools alone must already be
    clean."""
    from tools.capabilities import check_combination

    seed = _seed_agents()["verifier"]
    own = check_combination(seed["tools"])
    assert own is None or not own.blocking, "the Verifier's own tool list should need no override"


def test_writer_chain_to_verifier_would_close_the_trifecta_if_it_delegated():
    """Documents *why* the writer -> verifier link is a handoff and not a
    delegation: if it were a delegation, the Writer's own private-data reads
    plus the Verifier's reach into the Web Search Agent would close the
    lethal trifecta two hops out, the same shape ``effective_capabilities``
    exists to catch. This is what the handoff choice avoids."""
    from agents.capability_guard import check_agent_tools
    from agents.registry import get_agent

    writer = get_agent("writer")
    hypothetical_tools = list(writer.tools) + ["run_agent_tool"]
    violation = check_agent_tools(
        "writer", hypothetical_tools, override=False, delegates=["verifier"],
    )
    assert violation is not None and violation.blocking, (
        "a real delegation from writer to verifier should close the trifecta without an override"
    )
