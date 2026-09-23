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

A tool's own grant is only half the picture, though: an agent that holds none
of the three but can delegate to one that does effectively holds all three.
``DELEGATING_TOOLS`` and ``effective_capabilities`` cover that — see the
"Delegation" section below.

The module is deliberately pure and dependency-free at import time: it never
imports the tool registry at module level, so it can be unit-tested in
isolation and can never fail open because of an import cycle.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Set

log = logging.getLogger(__name__)


# ── Idempotency ───────────────────────────────────────────────────────────────
#
# A run that died mid-tool-call is resumed from its checkpoint
# (agents/checkpoint.py). A read, a search or a listing can simply be issued
# again; a call that changes something outside the run cannot be repeated
# blindly, because it may already have happened. These are the tools whose
# interrupted call the resumed model is told about instead of retrying. Every
# MCP tool (``mcp__*``) is treated the same way: a foreign server's side
# effects are unknown here.

NON_IDEMPOTENT_TOOLS: FrozenSet[str] = frozenset({
    "run_shell", "git_publish", "git_commit", "git_push",
    "write_file", "delete_file", "apply_unified_diff", "create_file", "move_file",
    "create_task", "add_subtask", "update_task", "set_task_dependencies",
    "assign_agent", "start_agent", "run_agent", "run_flow", "trigger_flow",
    "send_telegram", "send_message", "notify", "create_notification", "post_webhook",
    "schedule_job", "create_view", "view_serve", "delegate",
    "stop_run", "stop_node", "restart_node", "stop_container", "prune_run_logs",
})


def is_idempotent(tool_id: str) -> bool:
    return tool_id not in NON_IDEMPOTENT_TOOLS and not tool_id.startswith(("mcp__", "mcp:"))


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
    # A memory block is pool content the agent reads back in full.
    "memory_block_read": frozenset({READS_PRIVATE}),
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
    # git_publish pushes a branch to GitHub/GitLab and opens a PR/MR — the
    # payload is the workspace's own tracked (and newly added) files, chosen by
    # the agent's commit, leaving the system over a real network destination.
    # Reading issues/PRs with the same token (connectors/git/providers.py)
    # grants nothing: this is the one direction that moves bytes outward.
    "git_publish": frozenset({CAN_EXFILTRATE}),

    # ── agent and job reads ──────────────────────────────────────────────────
    # get_agent_tool returns instructions.md, capabilities.md and usage.md in
    # full — an agent's system prompt is operator-authored private content in
    # exactly the sense a task description is, so reading it carries the same
    # grant as get_task. list_agents_tool, by contrast, returns only id, name
    # and description, so it stays in REVIEWED_NO_GRANT.
    "get_agent_tool": frozenset({READS_PRIVATE}),
    # A scheduled job carries a title and message the operator or an agent
    # wrote for later delivery — the same class of authored content a task's
    # title and description carry, so list_scheduled is read under the same
    # grant as list_tasks.
    "list_scheduled": frozenset({READS_PRIVATE}),

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
    "memory_block_append", "memory_block_replace",
    # skills
    "get_skill", "create_skill",

    # ── filesystem writes ────────────────────────────────────────────────────
    # write_file, create_file and apply_unified_diff all return only the
    # workspace-relative path(s) touched (apply_unified_diff also echoes the
    # op per path); delete_file returns the path it removed. None of them read
    # existing file content back into the caller's context, so unlike
    # read_file/list_files/search_text they grant nothing.
    "write_file", "delete_file", "apply_unified_diff", "create_file",

    # ── memory writes / derivations ──────────────────────────────────────────
    # write_memory persists into a pool, like write_structured_memory above.
    # extract_from_text distills text the caller already supplied into a
    # reviewable proposal without touching the memory store; save_extraction
    # then persists that already-seen proposal. Neither reads anything the
    # caller did not already hand it.
    "write_memory", "extract_from_text", "save_extraction",

    # ── human-in-the-loop ─────────────────────────────────────────────────────
    # ask_user's return value is the question it was given, tagged so the
    # runner ends the turn — not the user's answer. The answer re-enters
    # context later as ordinary conversation history, over the same trusted
    # surface the rest of the run happens on, not through this tool's result.
    "ask_user",

    # ── task writes ───────────────────────────────────────────────────────────
    # These persist into the task store, like create_task; whatever they echo
    # back is content the calling agent already supplied or already had.
    "create_task", "add_subtask", "update_task", "set_task_dependencies",

    # ── pure computation / control flow ──────────────────────────────────────
    # think is a scratchpad that returns its own input unchanged. calculator
    # evaluates an expression the caller supplied. Neither touches a store, the
    # network or another agent.
    "think", "calculator",

    # ── agent coordination: task-based delegation and status ────────────────
    # assign_agent_tool and start_agent_tool set up and kick off a task-bound
    # agent run but return only confirmation, not the run's output — the
    # output is read later through get_task_result, which already carries its
    # own grant. get_agent_status_tool and stop_agent_tool report/change run
    # state (booleans, status strings), never run content. list_flows_tool
    # lists flow ids/names for the user to pick from, not a flow's graph
    # (that is get_flow_tool, itself configuration — see below).
    "assign_agent_tool", "start_agent_tool", "get_agent_status_tool",
    "stop_agent_tool", "list_flows_tool",
    # reject_assignment_tool clears a pending assignment and resets task
    # status; same shape as stop_agent_tool, control only.
    "reject_assignment_tool",

    # ── agent management: writes and structural listing ──────────────────────
    # list_agents_tool returns id/name/description only, never a system prompt
    # (that is get_agent_tool, classified above). create_agent_tool and
    # delete_agent_tool report what was created/removed; modify_agent_tool
    # echoes back the fields the caller supplied or merged. See
    # ``effective_capabilities`` for why create/modify granting nothing extra
    # still holds once delegation is considered.
    "list_agents_tool", "create_agent_tool", "modify_agent_tool", "delete_agent_tool",

    # ── schedule management: writes ──────────────────────────────────────────
    # schedule_task creates a job from caller-supplied fields; cancel_scheduled
    # and update_scheduled report/change a job the caller named by id. Reading
    # the roster (list_scheduled) is classified above.
    "schedule_task", "cancel_scheduled", "update_scheduled",

    # ── flow / world / scenario / team / loop management ─────────────────────
    # All of these are configuration the operator authored *for* the agents —
    # a graph of nodes and edges, a simulated place, an environment plus a
    # cast, a roster, an exit criterion — never operator data in the sense a
    # task, a project record or an agent's instructions are. Reading, writing,
    # deleting and validating any of them grants nothing.
    "create_flow_tool", "get_flow_tool", "modify_flow_tool", "delete_flow_tool",
    "validate_flow_tool",
    "list_worlds_tool", "list_world_templates_tool", "get_world_tool",
    "create_world_tool", "modify_world_tool", "validate_world_tool",
    "delete_world_tool",
    "list_environments_tool", "list_scenarios_tool", "create_scenario_tool",
    "get_scenario_tool", "modify_scenario_tool", "delete_scenario_tool",
    "validate_scenario_tool",
    "list_teams_tool", "create_team_tool", "get_team_tool", "modify_team_tool",
    "delete_team_tool",
    "list_loops_tool", "create_loop_tool", "get_loop_tool", "modify_loop_tool",
    "delete_loop_tool", "validate_loop_tool",

    # ── project management: writes ───────────────────────────────────────────
    # list_projects_tool / get_project_tool are classified READS_PRIVATE
    # (CAPABILITY_GRANTS above) because a project record names a real repo and
    # an on-disk path. These writes echo back only what the caller supplied or
    # merged, the same reasoning as modify_agent_tool.
    "create_project_tool", "modify_project_tool", "delete_project_tool",

    # ── entity runs: stopping ────────────────────────────────────────────────
    # stop_*_run_tool just interrupts a run in progress; the approval gate
    # governs the spend/interruption, not this table. Starting one
    # (run_scenario_tool / run_team_tool / run_loop_tool) is a delegation edge
    # instead — see DELEGATING_TOOLS below, which is where their real
    # capability lives (the run's content is read back later through
    # get_scenario_run_tool / get_team_run_tool / get_loop_run_tool, already
    # classified READS_PRIVATE above).
    "stop_scenario_run_tool", "stop_team_run_tool", "stop_loop_run_tool",

    # ── visualization / views / geometry-adjacent scene tools ───────────────
    # Every view_*, graph_*, scene_*, slides_*, document_*, sim_configure,
    # math_plot and view_compute tool operates on a view the agent itself is
    # authoring, rendered through the hub's own view server — not a stored
    # user document, not an external destination. view_get reads back only
    # element ids, control values and a live user's control changes, which is
    # the same trusted, same-session surface ask_user's answer arrives over,
    # not attacker-controllable or private stored content. view_add_asset
    # binds a workspace file into a view and returns an ``asset://`` ref, never
    # the file's content. (view_serve is the one exception — it proxies an
    # upstream URL and is classified CAN_EXFILTRATE above.)
    "create_view", "view_apply_ops", "view_get", "view_add_control",
    "view_remove_control", "view_revert", "view_snapshot", "view_link",
    "graph_add_node", "graph_add_edge",
    "graph_remove", "graph_set_layout", "scene_environment", "scene_camera",
    "scene_light", "view_add_asset", "suggest_view", "view_set_timeline",
    "sim_configure", "math_plot", "view_annotate", "slides_add",
    "document_set", "view_compute", "view_serve_stop",

    # ── evals: building and running ──────────────────────────────────────────
    # list_evals_tool and list_graders_tool are structural metadata (counts,
    # names, which graders cost money). create_eval_tool, modify_eval_tool,
    # add_eval_case_tool and remove_eval_case_tool build a dataset from fields
    # the caller supplies. estimate_eval_tool and run_eval_tool project or
    # spend against that dataset; writing into the product's own store is not
    # exfiltration, and the spend is the approval gate's job. list_eval_runs_tool
    # returns only status and aggregate score, never the cases or the matrix
    # (that is get_eval_run_tool, classified READS_PRIVATE + INGESTS_UNTRUSTED
    # above, since a result can carry whatever the agent under test pulled in).
    "list_evals_tool", "list_graders_tool", "create_eval_tool", "modify_eval_tool",
    "add_eval_case_tool", "remove_eval_case_tool", "estimate_eval_tool",
    "run_eval_tool", "list_eval_runs_tool",

    # ── documentation ─────────────────────────────────────────────────────────
    # Read-only over files the product ships — public by construction, so
    # neither private data nor untrusted input.
    "search_docs", "read_doc",

    # ── service ops: pure metadata and destructive actions ───────────────────
    # service_health, list_containers, list_nodes and costs_summary are counts,
    # statuses and totals, not content. stop_run, stop_node, restart_node,
    # stop_container and prune_run_logs stop and delete, which the approval
    # gate governs, not data.
    "service_health", "list_containers", "list_nodes", "costs_summary",
    "stop_run", "stop_node", "restart_node", "stop_container", "prune_run_logs",
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


# ── External MCP servers ──────────────────────────────────────────────────────
#
# An MCP server is a collection of tools defined somewhere else, attached per
# workspace (see mcp_client/). Its tools cannot appear in the tables above:
# they are not in this repository, they differ per workspace, and a remote
# server is free to rename them between two agent builds.
#
# They are still classified, just from a different source. The operator ticks
# the three capabilities once per server when attaching it, and every tool from
# that server inherits them. Per server rather than per tool because a per-tool
# claim would have to be derived from the tool's own name or description, and a
# server that calls its exfiltration endpoint ``get_weather`` would then classify
# itself. The one party who can make an honest claim is the person who attached
# the server.

MCP_TOOL_PREFIX = "mcp__"
MCP_ALIAS_PREFIX = "mcp:"


def _mcp_server_of(tool_id: str) -> Optional[str]:
    """The server id behind an MCP tool id or group alias, else ``None``.

    ``mcp__<server>__<tool>`` names one tool, ``mcp:<server>`` names the whole
    server the way ``filesystem`` names the filesystem group. Server ids are
    validated (``mcp_client.store.validate_id``) to contain no double
    underscore, so this split has exactly one reading.
    """
    text = str(tool_id or "")
    if text.startswith(MCP_ALIAS_PREFIX):
        return text[len(MCP_ALIAS_PREFIX):].strip().lower() or None
    if text.startswith(MCP_TOOL_PREFIX):
        server, sep, tool = text[len(MCP_TOOL_PREFIX):].partition("__")
        if sep and server and tool:
            return server.strip().lower()
    return None


def mcp_grants(tool_id: str) -> Optional[FrozenSet[str]]:
    """Capabilities an MCP tool id or alias grants, or ``None`` if it is neither.

    ``None`` and ``frozenset()`` mean different things here: the first says
    "this is not an MCP id, keep looking", the second says "it is, and its
    server grants nothing". A server that is not configured in the active
    workspace also reads as granting nothing, and that is accurate rather than
    lenient: with no configuration there is no server to connect to, so the
    agent gets no tool from it either (``mcp_client.client.expand_ids``).

    The lookup is lazy and defensive by design — this module must stay
    importable without the MCP package, and a capability check must never be
    the thing that raises.
    """
    server = _mcp_server_of(tool_id)
    if server is None:
        return None
    try:
        from common.workspace_context import resolve_active_workspace
        from mcp_client.store import server_capabilities
        return server_capabilities(resolve_active_workspace(), server)
    except Exception:
        log.warning(
            "capability model: could not read the configuration of MCP server %r "
            "— treating it as granting nothing.", server,
        )
        return frozenset()


# ── Delegation ────────────────────────────────────────────────────────────────
#
# A tool set's *own* grants (above) are only half the picture. An agent that
# holds none of the three capabilities but can call ``run_agent_tool`` on an
# agent that holds all three effectively holds all three: it can ask that
# agent to do the reading/ingesting/sending on its behalf. These tools are the
# edges of that delegation graph — calling one hands the request (and, for the
# poll/wait tools, the eventual result) to another agent, whose own tool set
# is not visible in the caller's tool list at all.
#
# They are deliberately NOT given a grant in ``CAPABILITY_GRANTS``: a bare
# ``run_agent_tool`` grants nothing *on its own* (there is nothing to read,
# ingest or send without a target), and folding its real effect into a static
# per-tool grant would either overclaim for a caller with a harmless
# delegation allowlist or underclaim for one with none. Instead
# ``effective_capabilities`` below walks the graph these tools open and unions
# in what is actually reachable.
DELEGATING_TOOLS: FrozenSet[str] = frozenset({
    "run_agent_tool", "wait_for_agent_tool", "run_flow_tool",
    "run_team_tool", "run_loop_tool", "run_scenario_tool",
})

# create_agent_tool / modify_agent_tool are NOT in DELEGATING_TOOLS. They can
# hand another agent an arbitrary tool list, which looks like the same shape —
# but whatever they create or change is itself re-validated by the save-time
# guard (``agents.registry.add_agent`` -> ``check_agent_tools``) before it can
# be persisted. An agent holding create/modify can therefore only ever reach a
# tool set the guard would already have accepted on its own; there is no
# additional reach for this graph to add on top of that.


def _delegates_of(agent_id: str) -> Optional[List[str]]:
    """Delegation allowlist of a registered agent, or ``None`` for "no restriction".

    Mirrors ``tools.langchain_tools._caller_delegates``: a non-empty
    ``AgentSpec.delegates`` restricts reachability to exactly that list; an
    empty list, a missing field, or an agent record that does not exist yet
    (the save-time check for a brand-new agent, before its first write) all
    mean unrestricted — the agent can reach every agent in the registry.
    Never raises: a lookup failure is treated the same as "not found".
    """
    try:
        from agents.registry import get_agent  # lazy: avoid an import cycle
        spec = get_agent(agent_id)
    except Exception:
        return None
    if spec is None:
        return None
    allow = list(getattr(spec, "delegates", None) or [])
    return allow or None


def _all_agent_ids() -> List[str]:
    """Every agent id currently in the registry. Lazy import, never fatal."""
    try:
        from agents.registry import list_agents  # lazy: avoid an import cycle
        return [spec.id for spec in list_agents()]
    except Exception:
        return []


def _delegation_targets(agent_id: str) -> List[str]:
    allow = _delegates_of(agent_id)
    return allow if allow is not None else _all_agent_ids()


def _walk_delegation_graph(
    agent_id: str,
    tools: Sequence[str],
    *,
    resolve_agent_tools,
    depth: int,
    root_delegates: Optional[Sequence[str]] = None,
) -> tuple:
    """Shared traversal behind ``effective_capabilities`` / ``effective_capability_sources``.

    Returns ``(capabilities, sources)`` where ``sources`` follows the same
    shape as ``capability_sources`` — a capability mapped to the labels that
    granted it — except a label reached through delegation reads
    ``"via <tool> -> <agent id>: <tool ids>"`` instead of a bare tool id, so a
    violation can name the path rather than just the fact of reachability.

    Breadth-first, cycle-safe (a ``visited`` set seeded with ``agent_id``) and
    depth-limited (at most ``depth`` delegation hops from the root).

    ``root_delegates``: the reachability of every hop is normally resolved
    from the registry (``_delegation_targets`` -> ``_delegates_of`` ->
    ``agents.registry.get_agent``), which is exactly right for an agent
    already on disk. It is wrong for the root of a save-time check: the spec
    being validated (a brand-new agent, or an existing one with its
    ``delegates`` field being changed) is not the one the registry would
    return yet, so a lookup there sees either nothing or the stale prior
    value, not the allowlist actually being saved. Pass the spec's own
    ``delegates`` here to resolve the *first* hop from it instead of the
    registry; every later hop (an already-persisted agent) still goes through
    the registry as usual. ``None`` (the default) leaves the root on the
    registry lookup too, unchanged from before this parameter existed.
    """
    tools = list(tools or [])
    caps: Set[str] = set(capabilities_of(tools))
    sources: Dict[str, List[str]] = capability_sources(tools)

    if not any(t in DELEGATING_TOOLS for t in tools):
        return caps, sources

    def _targets_of(aid: str) -> List[str]:
        if aid == agent_id and root_delegates is not None:
            allow = list(root_delegates)
            return allow if allow else _all_agent_ids()
        return _delegation_targets(aid)

    visited: Set[str] = {agent_id}
    frontier: List[tuple] = [(agent_id, tools, "")]
    hops = 0
    while frontier and hops < max(0, int(depth)):
        hops += 1
        next_frontier: List[tuple] = []
        for aid, atools, prefix in frontier:
            delegating_tool = next((t for t in atools if t in DELEGATING_TOOLS), None)
            if delegating_tool is None:
                continue
            for target_id in _targets_of(aid):
                if target_id in visited:
                    continue
                visited.add(target_id)
                target_tools = list(resolve_agent_tools(target_id) or [])
                hop = f"via {delegating_tool} -> {target_id}"
                path = f"{prefix} -> {hop}" if prefix else hop
                for cap in capabilities_of(target_tools):
                    granting = sorted(t for t in target_tools if cap in grants_of(t))
                    label = f"{path}: {', '.join(granting) or '?'}"
                    caps.add(cap)
                    sources.setdefault(cap, [])
                    if label not in sources[cap]:
                        sources[cap].append(label)
                next_frontier.append((target_id, target_tools, path))
        frontier = next_frontier
    return caps, sources


def effective_capabilities(
    agent_id: str,
    tools: Iterable[str],
    *,
    resolve_agent_tools,
    depth: int = 4,
    root_delegates: Optional[Sequence[str]] = None,
) -> Set[str]:
    """The capabilities this agent's tool set grants, directly or by delegation.

    An agent that cannot itself exfiltrate but can ``run_agent_tool`` an agent
    that can, effectively can — the trifecta composes across the delegation
    graph, not just within one tool list. This is the agent's own grants
    (``capabilities_of(tools)``) unioned with the grants of every agent
    reachable by following a tool in ``DELEGATING_TOOLS``.

    Reachability: if ``agent_id`` (or an agent reached from it) has a non-empty
    ``AgentSpec.delegates`` allowlist, only the ids on that list are reachable
    from it. If the list is empty, absent, or the record does not exist yet,
    it is unrestricted and every agent currently in the registry is reachable
    — see ``_delegates_of``. ``create_agent_tool`` / ``modify_agent_tool`` add
    no reach beyond that: see the comment above ``DELEGATING_TOOLS``.

    ``resolve_agent_tools(agent_id) -> Sequence[str]`` looks up another
    agent's current tool list — the caller wires this to
    ``agents.registry.get_agent(id).tools`` with a lazy import, so this module
    stays import-cycle-free and unit-testable with a fake resolver. The walk
    is cycle-safe and stops after ``depth`` delegation hops (default 4).

    ``root_delegates``, when given, resolves the root's own reachability
    instead of a registry lookup — see ``_walk_delegation_graph``.
    """
    caps, _ = _walk_delegation_graph(
        agent_id, tools, resolve_agent_tools=resolve_agent_tools, depth=depth,
        root_delegates=root_delegates,
    )
    return caps


def effective_capability_sources(
    agent_id: str,
    tools: Iterable[str],
    *,
    resolve_agent_tools,
    depth: int = 4,
    root_delegates: Optional[Sequence[str]] = None,
) -> Dict[str, List[str]]:
    """``effective_capabilities``, but with each capability's source path.

    A direct grant is labelled with the granting tool id, exactly like
    ``capability_sources``. A grant that only exists through delegation is
    labelled ``"via <delegating tool> -> <agent id>: <tool ids>"`` (chained
    with ``" -> "`` across multiple hops), so a :class:`Violation` can point at
    the actual path instead of just the fact that it exists.

    ``root_delegates``: see ``_walk_delegation_graph``.
    """
    _, sources = _walk_delegation_graph(
        agent_id, tools, resolve_agent_tools=resolve_agent_tools, depth=depth,
        root_delegates=root_delegates,
    )
    return sources


def check_effective_combination(
    capabilities: Iterable[str],
    sources: Dict[str, List[str]],
) -> Optional["Violation"]:
    """Like :func:`check_combination`, but over an already-computed capability
    set and source map — the delegation-aware pair from
    ``effective_capabilities`` / ``effective_capability_sources`` — rather
    than a raw tool list. Shares the rule-matching logic with
    ``check_combination`` so the two never drift.
    """
    caps = set(capabilities)
    rule = _matching_rule(caps)
    if rule is None:
        return None
    return Violation(
        rule_id=rule.id,
        title=rule.title,
        explanation=rule.explanation,
        capabilities=rule.capabilities,
        sources={c: s for c, s in sources.items() if c in rule.capabilities},
        severity=rule.severity,
    )


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
    # An MCP tool is classified by its server's configuration rather than by a
    # table in this file — see ``mcp_grants``. Checked before the unknown-tool
    # warning below, which would otherwise fire for every external tool.
    external = mcp_grants(tool_id)
    if external is not None:
        return external
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


def _matching_rule(caps: Set[str]) -> Optional[Rule]:
    """The blocked combination a capability set forms, or None.

    A blocking rule always wins over a warning one, so a set that forms both
    the trifecta and the pair reports the trifecta. Shared by
    ``check_combination`` and ``check_effective_combination`` so the two rule
    checks — over a raw tool list and over a delegation-expanded set — can
    never drift apart.
    """
    matched = [r for r in BLOCKED_COMBINATIONS if r.capabilities <= caps]
    if not matched:
        return None
    return next((r for r in matched if r.severity == "block"), matched[0])


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
    rule = _matching_rule(caps)
    if rule is None:
        return None
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
    "REVIEWED_NO_GRANT", "ALIAS_GRANTS", "DELEGATING_TOOLS",
    "MCP_TOOL_PREFIX", "MCP_ALIAS_PREFIX", "mcp_grants",
    "UNTRUSTED_CHANNELS", "channel_capabilities",
    "BLOCKED_COMBINATIONS", "Rule", "Violation",
    "grants_of", "capabilities_of", "capability_sources",
    "check_combination", "explain",
    "effective_capabilities", "effective_capability_sources",
    "check_effective_combination",
]
