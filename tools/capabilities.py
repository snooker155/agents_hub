"""
Tool capability model — the guard that keeps the "lethal trifecta" un-shippable.

An agent that can (a) ingest attacker-controllable text, (b) read private data
and (c) talk to the outside world is a data-exfiltration primitive, not an
assistant. This module classifies every tool by the *capabilities* it grants and
refuses combinations that compose into that primitive.

Why capabilities and not tool ids: tool names are a leaky proxy. ``run_shell``
alone is already the whole trifecta (``curl`` = ingest + exfiltrate, ``cat`` /
``>`` = private read/write), so a rule that only fires when three *named* tools
co-occur does nothing against the single most dangerous tool.

The module is deliberately pure and dependency-free at import time: it never
imports the tool registry at module level, so it can be unit-tested in
isolation and can never fail open because of an import cycle.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Set

log = logging.getLogger(__name__)


# ── The three capabilities ────────────────────────────────────────────────────

INGESTS_UNTRUSTED = "ingests_untrusted"
READS_PRIVATE = "reads_private"
CAN_EXFILTRATE = "can_exfiltrate"

CAPABILITIES: tuple[str, ...] = (INGESTS_UNTRUSTED, READS_PRIVATE, CAN_EXFILTRATE)

CAPABILITY_LABELS: Dict[str, str] = {
    INGESTS_UNTRUSTED: "ingests untrusted content",
    READS_PRIVATE: "reads private data",
    CAN_EXFILTRATE: "can send data outside",
}


# ── The grant table — the single source of truth ──────────────────────────────
#
# Only tools that grant something appear here; anything absent grants nothing.
# Keep this table explicit rather than derived from ``category``: a category is
# a UI grouping, a capability is a security claim, and the two must not drift.
#
# ``ingests_untrusted`` is granted at the *tool* layer only by tools that pull
# text from outside the operator's control (``run_shell`` via curl/wget, and the
# web tools). Telegram inbound and imported git issues also carry
# attacker-controllable text, but they arrive through the run's *channel*, not
# through a tool grant — see ``channel_capabilities`` below.

CAPABILITY_GRANTS: Dict[str, FrozenSet[str]] = {
    # ── execution: the whole trifecta in one tool ────────────────────────────
    "run_shell": frozenset({INGESTS_UNTRUSTED, READS_PRIVATE, CAN_EXFILTRATE}),

    # ── filesystem reads ─────────────────────────────────────────────────────
    "read_file": frozenset({READS_PRIVATE}),
    "list_files": frozenset({READS_PRIVATE}),
    "search_text": frozenset({READS_PRIVATE}),

    # ── memory reads ─────────────────────────────────────────────────────────
    "read_memory": frozenset({READS_PRIVATE}),
    "search_memory": frozenset({READS_PRIVATE}),
    "read_structured_memory": frozenset({READS_PRIVATE}),
    "recall": frozenset({READS_PRIVATE}),
    "recall_episodes": frozenset({READS_PRIVATE}),

    # ── task / db reads ──────────────────────────────────────────────────────
    "get_task": frozenset({READS_PRIVATE}),
    "list_tasks": frozenset({READS_PRIVATE}),
    "get_task_result": frozenset({READS_PRIVATE}),
    # A project record names the repo it is backed by and its path inside the
    # workspace — the same class of operator data a task carries, so it is read
    # under the same grant. Scenario/team/loop definitions are configuration the
    # operator wrote *for* the agents and grant nothing, like flows.
    "list_projects_tool": frozenset({READS_PRIVATE}),
    "get_project_tool": frozenset({READS_PRIVATE}),
    # A finished run carries what the agents produced — the team's answer, the
    # loop's result — which is operator data in the same sense a task result is.
    # Starting and stopping a run grant nothing: they spend money, which the
    # approval gate governs, not data, which is what this table is about.
    "get_scenario_run_tool": frozenset({READS_PRIVATE}),
    "get_team_run_tool": frozenset({READS_PRIVATE}),
    "get_loop_run_tool": frozenset({READS_PRIVATE}),

    # ── web ──────────────────────────────────────────────────────────────────
    # Search returns third-party text — ingest, nothing more: the query goes to
    # one fixed API endpoint, which is not a channel an attacker can aim.
    "web_search": frozenset({INGESTS_UNTRUSTED}),
    # Fetch also *sends*: the agent chooses the host, path and query string, so
    # a stolen secret can leave in the URL itself. Ingest and exfiltrate in one
    # tool, which is why it is the second, more restricted grant.
    "fetch_url": frozenset({INGESTS_UNTRUSTED, CAN_EXFILTRATE}),

    # ── evals ────────────────────────────────────────────────────────────────
    # A case carries operator-written input and the reference answer; a result
    # carries what the agent produced. Both are operator data. A result also
    # carries whatever the agent under test pulled in while producing it, which
    # can include fetched pages — the same reason a run log ingests untrusted
    # content. Building and running grant nothing: writing into the product's
    # own store is not exfiltration, and the spend is the approval gate's job.
    "get_eval_tool": frozenset({READS_PRIVATE}),
    "get_eval_run_tool": frozenset({READS_PRIVATE, INGESTS_UNTRUSTED}),

    # ── service operations ───────────────────────────────────────────────────
    # Two claims are being made here, and the second one is the interesting one.
    #
    # Anything that returns run, session or instance *content* reads private
    # data: a run record carries its input and output, an instance timeline
    # carries the conversation, the routing log carries task titles.
    #
    # Log-bearing tools also ingest untrusted content. A run log holds whatever
    # that run handled — pages it fetched, messages strangers sent it over
    # Telegram — so reading one pulls attacker-controllable text into the
    # reader's context just as surely as fetching the page would. Claiming that
    # honestly is what stops a log reader from ever being combined with an
    # outbound channel, which is exactly the shape that would turn the service's
    # own diagnostics into an exfiltration path.
    #
    # The action tools (stop_run, stop_node, restart_node, stop_container,
    # prune_run_logs) grant nothing: they stop and delete, which the approval
    # gate governs, rather than moving data, which is what this table is about.
    # Pure metadata tools (service_health, list_containers, list_nodes,
    # costs_summary) grant nothing either — counts, statuses and totals.
    "container_logs": frozenset({READS_PRIVATE, INGESTS_UNTRUSTED}),
    "node_logs": frozenset({READS_PRIVATE, INGESTS_UNTRUSTED}),
    "run_log": frozenset({READS_PRIVATE, INGESTS_UNTRUSTED}),
    "list_runs": frozenset({READS_PRIVATE, INGESTS_UNTRUSTED}),
    "search_errors": frozenset({READS_PRIVATE, INGESTS_UNTRUSTED}),
    "instance_timeline": frozenset({READS_PRIVATE, INGESTS_UNTRUSTED}),
    "web_log_recent": frozenset({READS_PRIVATE, INGESTS_UNTRUSTED}),
    "list_instances": frozenset({READS_PRIVATE}),
    "list_sessions": frozenset({READS_PRIVATE}),
    "routing_log": frozenset({READS_PRIVATE}),

    # ── outbound channels ────────────────────────────────────────────────────
    # These deliver free text to a chat transport (Telegram today). The
    # destination is the operator, but the *payload* is agent-controlled, which
    # is exactly the exfiltration shape.
    "notify_user": frozenset({CAN_EXFILTRATE}),
    "schedule_notification": frozenset({CAN_EXFILTRATE}),
    # Proxies a caller-supplied upstream URL through the view server.
    "view_serve": frozenset({CAN_EXFILTRATE}),

    # ── geometry engine ──────────────────────────────────────────────────────
    # The mesh_* tools drive a local Blender through a fixed command whitelist.
    # They grant nothing, and the reasoning is worth writing down because "it
    # runs a whole application" looks alarming: the engine takes named
    # operations with validated numeric arguments, never Blender Python, so an
    # agent cannot express a read or a send through it. Nothing enters agent
    # context from outside the system (geometry is authored, not fetched),
    # nothing private is read, and the files produced land in the view's own
    # assets or, for mesh_export, inside the agent's own workspace — writing
    # into a workspace the agent already writes to is not an outbound channel.
    "mesh_new": frozenset(), "mesh_select": frozenset(), "mesh_group": frozenset(),
    "mesh_extrude": frozenset(), "mesh_inset": frozenset(), "mesh_bevel": frozenset(),
    "mesh_transform": frozenset(), "mesh_subdivide": frozenset(), "mesh_delete": frozenset(),
    "mesh_merge": frozenset(), "mesh_normals": frozenset(), "mesh_validate": frozenset(),
    "mesh_stats": frozenset(), "mesh_preview": frozenset(), "mesh_history": frozenset(),
    "mesh_revert": frozenset(), "mesh_export": frozenset(),
}

# Tools that are reachable from an agent tool list but do not appear in
# ``TOOL_CATALOG`` (they are attached through a group alias or a subsystem
# builder). Listed explicitly so the audit does not warn about them: absence
# from this set *and* from the catalog means nobody has classified the tool.
REVIEWED_NO_GRANT: FrozenSet[str] = frozenset({
    # task_management group members outside the catalog
    "block_task", "stop_task", "create_sequence",
    # project-graph builders — write into the workspace's own structure graph
    "add_graph_node", "add_graph_edge", "delete_graph_node", "delete_graph_edge",
    "clear_graph",
    # memory writers — write into pools, never out of the system
    "write_structured_memory", "append_journal", "remember", "record_episode",
    # skills
    "get_skill", "create_skill",
})

# Reviewed and classified, but outside the catalog.
CAPABILITY_GRANTS_EXTRA: Dict[str, FrozenSet[str]] = {
    "read_graph_view": frozenset({READS_PRIVATE}),
    "get_project_graph": frozenset({READS_PRIVATE}),
}

# Legacy group aliases accepted in ``AgentSpec.tools`` (see
# ``AgentFactory._create_tools``). A group grants the union of its members.
ALIAS_GRANTS: Dict[str, FrozenSet[str]] = {
    "filesystem": frozenset({READS_PRIVATE}),
    "task_management": frozenset({READS_PRIVATE}),
    "schedule_management": frozenset({CAN_EXFILTRATE}),
    "agent_coordination": frozenset(),
    "agent_management": frozenset(),
    "agent_flows": frozenset(),
    "flow_management": frozenset(),
    "scenario_management": frozenset(),
    "world_management": frozenset(),
    "team_management": frozenset(),
    "loop_management": frozenset(),
    # The group's reads carry the grant its read tools do.
    "project_management": frozenset({READS_PRIVATE}),
    "entity_runs": frozenset({READS_PRIVATE}),
    # The service-ops group carries what its log readers carry: run and session
    # content is private, and a log holds whatever the run handled, which can be
    # a page it fetched or a message a stranger sent it.
    "service_ops": frozenset({READS_PRIVATE, INGESTS_UNTRUSTED}),
    # Reading a result pulls in what the agent under test produced.
    "evals": frozenset({READS_PRIVATE, INGESTS_UNTRUSTED}),
    # Product documentation the operator installed with the product. Public by
    # construction, so it is neither private data nor untrusted input.
    "docs": frozenset(),
    # Modelling operations on a local engine: see the mesh_* entries above.
    "geometry": frozenset(),
}


# ── Channel-level ingest ──────────────────────────────────────────────────────
#
# The exposure is already live, before any web tool exists: the Telegram runner
# and the git issue sync both pull attacker-controllable text into agent context.
# A tool-grant check cannot see this, so runs on these channels are evaluated
# with ``ingests_untrusted`` added.

UNTRUSTED_CHANNELS: FrozenSet[str] = frozenset({
    "telegram", "git_issue", "issue_sync", "webhook", "email", "slack",
})


def channel_capabilities(channel: Optional[str]) -> Set[str]:
    """Capabilities the *run channel* grants regardless of the tool list."""
    if channel and str(channel).strip().lower() in UNTRUSTED_CHANNELS:
        return {INGESTS_UNTRUSTED}
    return set()


# ── Blocked combinations ──────────────────────────────────────────────────────
#
# ``reads_private + can_exfiltrate`` and ``reads_private + ingests_untrusted``
# stay legal on purpose: the swe_agent needs filesystem access and must keep
# working, and neither pair closes the loop on its own.

@dataclass(frozen=True)
class Rule:
    id: str
    capabilities: FrozenSet[str]
    title: str
    explanation: str
    # "block" refuses the tool set; "warn" logs and surfaces it but allows it.
    severity: str = "block"


BLOCKED_COMBINATIONS: tuple[Rule, ...] = (
    Rule(
        id="lethal_trifecta",
        capabilities=frozenset({INGESTS_UNTRUSTED, READS_PRIVATE, CAN_EXFILTRATE}),
        title="Lethal trifecta",
        explanation=(
            "This agent can ingest attacker-controllable text, read private data "
            "and send data outside. Text it reads can instruct it to collect "
            "secrets and forward them, with nothing in the loop to stop it."
        ),
        severity="block",
    ),
    # Deliberately a warning, not a block. The backlog left this pair's policy
    # as an open question; enforcing it as a hard block makes ``fetch_url``
    # unusable on its own — the tool grants both halves by itself, since the
    # agent picks the host and query string — and a guard that bans the feature
    # it was written to enable is the guard that gets switched off wholesale.
    # Without ``reads_private`` the agent has no private data to leak: what
    # escapes is its own context, which is a real downgrade in severity, not
    # the exfiltration primitive the trifecta describes.
    Rule(
        id="exfiltration_path",
        capabilities=frozenset({INGESTS_UNTRUSTED, CAN_EXFILTRATE}),
        title="Exfiltration path",
        explanation=(
            "This agent can ingest attacker-controllable text and send data "
            "outside. Injected instructions have a direct outbound channel — "
            "anything already in the agent's context can leave through it. "
            "Grant it only when the outbound reach is the point."
        ),
        severity="warn",
    ),
)


@dataclass(frozen=True)
class Violation:
    """A flagged capability combination, with everything the UI needs to explain it."""
    rule_id: str
    title: str
    explanation: str
    capabilities: FrozenSet[str]
    # capability -> the tool ids (or "channel:<name>") that granted it
    sources: Dict[str, List[str]] = field(default_factory=dict)
    severity: str = "block"

    @property
    def blocking(self) -> bool:
        return self.severity == "block"

    @property
    def message(self) -> str:
        parts = []
        for cap in CAPABILITIES:
            if cap not in self.capabilities:
                continue
            granted_by = ", ".join(self.sources.get(cap, [])) or "?"
            parts.append(f"{CAPABILITY_LABELS[cap]} ({granted_by})")
        return f"{self.title}: {' + '.join(parts)}. {self.explanation}"

    def to_dict(self) -> Dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "explanation": self.explanation,
            "capabilities": sorted(self.capabilities),
            "sources": {k: list(v) for k, v in self.sources.items()},
            "severity": self.severity,
            "blocking": self.blocking,
            "message": self.message,
        }


# ── Public API ────────────────────────────────────────────────────────────────

def grants_of(tool_id: str) -> FrozenSet[str]:
    """Capabilities a single tool id (or group alias) grants.

    An id that is neither in the grant table nor in the tool catalog grants
    nothing — but is logged, because silently granting nothing to a typo'd or
    newly-added tool is how a guard fails open.
    """
    if tool_id in CAPABILITY_GRANTS:
        return CAPABILITY_GRANTS[tool_id]
    if tool_id in CAPABILITY_GRANTS_EXTRA:
        return CAPABILITY_GRANTS_EXTRA[tool_id]
    if tool_id in ALIAS_GRANTS:
        return ALIAS_GRANTS[tool_id]
    if tool_id in REVIEWED_NO_GRANT:
        return frozenset()
    if not _is_known_tool(tool_id):
        log.warning(
            "capability model: unknown tool id %r — treating as granting nothing. "
            "Add it to tools/capabilities.CAPABILITY_GRANTS if it grants any capability.",
            tool_id,
        )
    return frozenset()


def _is_known_tool(tool_id: str) -> bool:
    """True when the id exists in the tool catalog (imported lazily, never fatal)."""
    try:
        from tools.registry import get_tool_by_id
        return get_tool_by_id(tool_id) is not None
    except Exception:
        return False


def capabilities_of(tool_ids: Iterable[str]) -> Set[str]:
    """Union of the capabilities granted by a tool list."""
    out: Set[str] = set()
    for tid in tool_ids or []:
        out |= grants_of(str(tid))
    return out


def capability_sources(
    tool_ids: Iterable[str],
    extra: Optional[Dict[str, str]] = None,
) -> Dict[str, List[str]]:
    """Map each granted capability to the tool ids that granted it.

    ``extra`` maps a capability to a non-tool source label (e.g. a channel), so
    a violation can name "channel:telegram" alongside real tool ids.
    """
    sources: Dict[str, List[str]] = {}
    for tid in tool_ids or []:
        tid = str(tid)
        for cap in grants_of(tid):
            sources.setdefault(cap, [])
            if tid not in sources[cap]:
                sources[cap].append(tid)
    for cap, label in (extra or {}).items():
        sources.setdefault(cap, [])
        if label not in sources[cap]:
            sources[cap].append(label)
    return sources


def check_combination(
    tool_ids: Iterable[str],
    extra_capabilities: Optional[Dict[str, str]] = None,
) -> Optional[Violation]:
    """Return the first blocked combination this tool list forms, or None.

    ``extra_capabilities`` maps a capability to the label of a non-tool source
    that grants it (``{"ingests_untrusted": "channel:telegram"}``), so run-level
    ingest can be folded into the same check.
    """
    tool_ids = list(tool_ids or [])
    caps = capabilities_of(tool_ids) | set(extra_capabilities or {})
    matched = [r for r in BLOCKED_COMBINATIONS if r.capabilities <= caps]
    if not matched:
        return None
    # A blocking rule always wins over a warning one, so a tool set that forms
    # both the trifecta and the pair reports the trifecta.
    rule = next((r for r in matched if r.severity == "block"), matched[0])
    all_sources = capability_sources(tool_ids, extra_capabilities)
    return Violation(
        rule_id=rule.id,
        title=rule.title,
        explanation=rule.explanation,
        capabilities=rule.capabilities,
        sources={c: s for c, s in all_sources.items() if c in rule.capabilities},
        severity=rule.severity,
    )


def explain(tool_ids: Iterable[str]) -> Dict[str, object]:
    """Everything the agent editor needs to render the capability state of a tool set."""
    tool_ids = list(tool_ids or [])
    violation = check_combination(tool_ids)
    return {
        "capabilities": sorted(capabilities_of(tool_ids)),
        "sources": capability_sources(tool_ids),
        "violation": violation.to_dict() if violation else None,
    }


__all__ = [
    "INGESTS_UNTRUSTED", "READS_PRIVATE", "CAN_EXFILTRATE", "CAPABILITIES",
    "CAPABILITY_LABELS", "CAPABILITY_GRANTS", "CAPABILITY_GRANTS_EXTRA",
    "REVIEWED_NO_GRANT", "ALIAS_GRANTS",
    "UNTRUSTED_CHANNELS", "channel_capabilities",
    "BLOCKED_COMBINATIONS", "Rule", "Violation",
    "grants_of", "capabilities_of", "capability_sources",
    "check_combination", "explain",
]
