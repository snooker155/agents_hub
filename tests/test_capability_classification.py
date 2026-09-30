"""Coverage of the capability classification tables (tools/capabilities.py)
against the live tool catalog (tools/registry.py).

Every tool id the catalog can produce — including ids added by a later
regeneration of tools/registry.py from each tool's args_schema, since this
imports TOOL_CATALOG fresh rather than pinning a snapshot of ids — must end up
in exactly one of: CAPABILITY_GRANTS, REVIEWED_NO_GRANT, CAPABILITY_GRANTS_EXTRA
or DELEGATING_TOOLS. A tool absent from all four silently grants nothing,
which is the same failure mode as a typo'd id — just invisible until this
runs.
"""
from __future__ import annotations

from tools.capabilities import (
    CAN_EXFILTRATE,
    CAPABILITY_GRANTS,
    CAPABILITY_GRANTS_EXTRA,
    DELEGATING_TOOLS,
    INGESTS_UNTRUSTED,
    READS_PRIVATE,
    REVIEWED_NO_GRANT,
    grants_of,
)
from tools.registry import TOOL_CATALOG


def _known_ids() -> set:
    return (
        set(CAPABILITY_GRANTS)
        | set(REVIEWED_NO_GRANT)
        | set(CAPABILITY_GRANTS_EXTRA)
        | set(DELEGATING_TOOLS)
    )


# ── Coverage ──────────────────────────────────────────────────────────────────

def test_every_catalog_tool_id_is_classified():
    known = _known_ids()
    missing = [t.id for t in TOOL_CATALOG if t.id not in known]
    assert missing == [], f"unclassified catalog tools: {missing}"


def test_the_four_tables_do_not_overlap():
    """A tool id classified in two tables at once is a contradiction (e.g. it
    would be both "grants nothing" and "grants nothing directly but delegates"),
    and whichever table ``grants_of`` checks first would silently win."""
    tables = {
        "CAPABILITY_GRANTS": set(CAPABILITY_GRANTS),
        "REVIEWED_NO_GRANT": set(REVIEWED_NO_GRANT),
        "CAPABILITY_GRANTS_EXTRA": set(CAPABILITY_GRANTS_EXTRA),
        "DELEGATING_TOOLS": set(DELEGATING_TOOLS),
    }
    names = list(tables)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            overlap = tables[names[i]] & tables[names[j]]
            assert not overlap, f"{names[i]} and {names[j]} both classify {overlap}"


def test_audit_reports_zero_unclassified_tools():
    from agents.capability_guard import unclassified_tools
    assert unclassified_tools() == []


def test_delegating_tools_grant_nothing_directly():
    """DELEGATING_TOOLS' own capability lives entirely in the delegation graph
    (tools.capabilities.effective_capabilities) — a bare run_agent_tool with no
    target has nothing to read, ingest or send on its own."""
    for tool_id in DELEGATING_TOOLS:
        assert grants_of(tool_id) == frozenset(), tool_id


# ── Spot check: ten classifications and why they are what they are ──────────

def test_get_agent_tool_reads_private_because_it_returns_the_system_prompt():
    """get_agent_tool returns instructions.md/capabilities.md/usage.md in
    full — an agent's own instructions, which the task brief calls out
    explicitly as private content, the same class as a task description."""
    assert READS_PRIVATE in grants_of("get_agent_tool")


def test_list_agents_tool_grants_nothing_because_it_returns_only_id_and_name():
    """Unlike get_agent_tool, list_agents_tool's response carries only
    id/name/description — structural metadata, not the agent's instructions."""
    assert grants_of("list_agents_tool") == frozenset()


def test_list_scheduled_reads_private_like_list_tasks():
    """A scheduled job's title/message is operator- or agent-authored content
    for future delivery, the same class list_tasks already reads under."""
    assert READS_PRIVATE in grants_of("list_scheduled")


def test_create_task_grants_nothing_it_only_echoes_the_caller():
    """A write into the hub's own task store returns only what the caller
    already supplied — no private read, no exfiltration."""
    assert grants_of("create_task") == frozenset()


def test_filesystem_writes_grant_nothing_they_never_echo_file_content():
    """write_file/create_file return the path written; apply_unified_diff
    returns path+op pairs; delete_file returns the path removed. None of them
    read existing file content back into the caller's context."""
    for tool_id in ("write_file", "create_file", "apply_unified_diff", "delete_file"):
        assert grants_of(tool_id) == frozenset(), tool_id


def test_flow_and_scenario_definitions_grant_nothing_they_are_configuration():
    """A flow/scenario/world/team/loop is configuration authored *for* the
    agents, not operator data in the sense a task or an agent's prompt is."""
    for tool_id in ("get_flow_tool", "get_scenario_tool", "get_world_tool", "get_loop_tool"):
        assert grants_of(tool_id) == frozenset(), tool_id


def test_get_team_run_tool_reads_private_a_finished_run_carries_its_result():
    """Unlike a team's static definition, a finished run record carries what
    the team actually produced — operator data, not configuration."""
    assert READS_PRIVATE in grants_of("get_team_run_tool")


def test_run_shell_is_still_the_whole_trifecta():
    """Sanity anchor: nothing about the new tables touched the pre-existing
    single-tool trifecta case."""
    assert grants_of("run_shell") == frozenset(
        {INGESTS_UNTRUSTED, READS_PRIVATE, CAN_EXFILTRATE}
    )


def test_notify_user_and_view_serve_can_exfiltrate():
    """Both hand agent-chosen bytes to a destination outside the hub's own
    stores: a chat transport payload, or a caller-chosen proxied upstream."""
    assert CAN_EXFILTRATE in grants_of("notify_user")
    assert CAN_EXFILTRATE in grants_of("view_serve")


def test_run_agent_tool_and_wait_for_agent_tool_are_delegating_not_granting():
    """Their capability is not a static grant — see
    test_delegation_guard.py for what they actually compose into."""
    assert "run_agent_tool" in DELEGATING_TOOLS
    assert "wait_for_agent_tool" in DELEGATING_TOOLS
    assert grants_of("run_agent_tool") == frozenset()
    assert grants_of("wait_for_agent_tool") == frozenset()
