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
# interrupted call the resumed model is told about instead of retrying.

NON_IDEMPOTENT_TOOLS: FrozenSet[str] = frozenset({
    "run_shell", "run_code", "git_publish", "git_commit", "git_push",
    "write_file", "delete_file", "apply_unified_diff", "create_file", "move_file",
    "create_task", "add_subtask", "update_task", "set_task_dependencies",
    "assign_agent", "start_agent", "run_agent", "run_flow", "trigger_flow", "run_new_flow_tool",
    "send_message", "notify", "create_notification", "post_webhook",
    "schedule_job", "create_view", "view_serve", "delegate", "run_scheduled_now",
    # A workspace created or deleted from the chat (tools/workspace_management.py).
    "create_workspace", "delete_workspace",
})


def is_idempotent(tool_id: str) -> bool:
    return tool_id not in NON_IDEMPOTENT_TOOLS


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
# web tools). Imported git and tracker issues also carry attacker-controllable
# text, but they arrive through the run's *channel*, not through a tool grant;
# see ``channel_capabilities`` below.

CAPABILITY_GRANTS: Dict[str, FrozenSet[str]] = {
    # ── execution: the whole trifecta in one tool ────────────────────────────
    "run_shell": frozenset({INGESTS_UNTRUSTED, READS_PRIVATE, CAN_EXFILTRATE}),
    # run_code's real grant depends on configuration and is answered by
    # ``run_code_grants`` below: reads_private only in a no-network sandbox,
    # ingests_untrusted and can_exfiltrate in a run with a limited network
    # (no workspace mount then), the whole trifecta when
    # CODE_RUNNER_FALLBACK=local. This entry is the worst case, which is what
    # a static reading of the table should see.
    "run_code": frozenset({INGESTS_UNTRUSTED, READS_PRIVATE, CAN_EXFILTRATE}),

    # ── filesystem reads ─────────────────────────────────────────────────────
    "read_file": frozenset({READS_PRIVATE}),
    "list_files": frozenset({READS_PRIVATE}),
    "search_text": frozenset({READS_PRIVATE}),
    # The workspace file objects (tools/workspace_files.py): listing names
    # them and reading returns their text, the same class of operator data
    # as a file in the working directory.
    "list_workspace_files": frozenset({READS_PRIVATE}),
    "read_workspace_file": frozenset({READS_PRIVATE}),

    # ── memory reads ─────────────────────────────────────────────────────────
    "read_memory": frozenset({READS_PRIVATE}),
    "search_memory": frozenset({READS_PRIVATE}),
    "read_structured_memory": frozenset({READS_PRIVATE}),
    "recall": frozenset({READS_PRIVATE}),
    # A memory block is pool content the agent reads back in full.
    "memory_block_read": frozenset({READS_PRIVATE}),
    "recall_episodes": frozenset({READS_PRIVATE}),
    # Walks the pool's knowledge graph and returns what it finds.
    "traverse": frozenset({READS_PRIVATE}),

    # ── task / db reads ──────────────────────────────────────────────────────
    "get_task": frozenset({READS_PRIVATE}),
    "list_tasks": frozenset({READS_PRIVATE}),
    "get_task_result": frozenset({READS_PRIVATE}),
    # A project record names the repo it is backed by and its path inside the
    # workspace — the same class of operator data a task carries, so it is read
    # under the same grant. Team/loop definitions are configuration the
    # operator wrote *for* the agents and grant nothing, like flows.
    "list_projects_tool": frozenset({READS_PRIVATE}),
    "get_project_tool": frozenset({READS_PRIVATE}),
    # A finished run carries what the agents produced — the team's answer, the
    # loop's result — which is operator data in the same sense a task result is.
    # Starting and stopping a run grant nothing: they spend money, which the
    # approval gate governs, not data, which is what this table is about.
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

    # ── outbound channels ────────────────────────────────────────────────────
    # These deliver free text to the person's notification inbox. The
    # destination is the operator, but the *payload* is agent-controlled, so
    # they stay classed as an outbound path.
    "notify_user": frozenset({CAN_EXFILTRATE}),
    "schedule_notification": frozenset({CAN_EXFILTRATE}),
    # Proxies a caller-supplied upstream URL through the view server.
    "view_serve": frozenset({CAN_EXFILTRATE}),
    # git_publish pushes a branch to GitLab and opens a merge request: the
    # payload is the workspace's own tracked (and newly added) files, chosen by
    # the agent's commit, leaving the system over a real network destination.
    # Reading issues/PRs with the same token (connectors/git/providers.py)
    # grants nothing: this is the one direction that moves bytes outward.
    "git_publish": frozenset({CAN_EXFILTRATE}),

    # ── connectors (tools/connector_tools.py aggregates these) ──────────────
    # The issue tracker (connectors/trackers: Jira). Reading an issue
    # pulls text strangers wrote into context; a comment, a transition or a
    # new issue carries agent-written text out.
    "tracker_list_issues": frozenset({INGESTS_UNTRUSTED, READS_PRIVATE}),
    "tracker_get_issue": frozenset({INGESTS_UNTRUSTED, READS_PRIVATE}),
    "tracker_sync": frozenset({INGESTS_UNTRUSTED, READS_PRIVATE}),
    "tracker_comment": frozenset({CAN_EXFILTRATE}),
    "tracker_transition": frozenset({CAN_EXFILTRATE}),
    "tracker_create_issue": frozenset({CAN_EXFILTRATE}),

    # ── agent and job reads ──────────────────────────────────────────────────
    # get_agent_tool returns instructions.md, capabilities.md and usage.md in
    # full — an agent's system prompt is operator-authored private content in
    # exactly the sense a task description is, so reading it carries the same
    # grant as get_task. list_agents_tool, by contrast, returns only id, name
    # and description, so it stays in REVIEWED_NO_GRANT.
    "get_agent_tool": frozenset({READS_PRIVATE}),
    # Workspace management (tools/workspace_management.py), the main agent's
    # in the default workspace only (common/workspace_scope.py). The reads
    # return workspace metadata the acting user owns: descriptions, the
    # workspace instructions (operator-authored, like an agent's prompt), the
    # agent roster and the default model, so they carry the get_agent_tool
    # grant. The writes grant nothing: they change that metadata inside the
    # hub and echo back the fields the caller passed. None of them reads or
    # changes a secret, isolation, hooks, members or connectors.
    "list_workspaces": frozenset({READS_PRIVATE}),
    "get_workspace": frozenset({READS_PRIVATE}),
    "create_workspace": frozenset(),
    "update_workspace": frozenset(),
    "delete_workspace": frozenset(),
    "add_workspace_agent": frozenset(),
    "remove_workspace_agent": frozenset(),
    # A scheduled job carries a title and message the operator or an agent
    # wrote for later delivery — the same class of authored content a task's
    # title and description carry, so list_scheduled is read under the same
    # grant as list_tasks.
    "list_scheduled": frozenset({READS_PRIVATE}),
    # One job with its firing journal: the same authored content, plus the
    # errors its firings hit.
    "get_scheduled": frozenset({READS_PRIVATE}),
    # Fires a job now: a notification job delivers its message at once, the
    # same channel schedule_notification opens.
    "run_scheduled_now": frozenset({CAN_EXFILTRATE}),

    # slides_export writes a .pptx of a slides view the agent authored into the
    # agent's own workspace: nothing is read from outside the system (remote
    # slide images are never fetched) and nothing leaves it.
    "slides_export": frozenset(),
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
    # and their counterparts: forget deletes from the pool, link adds a graph edge
    "forget", "link",
    # skills. read_skill_file reads only the files listed in one attached
    # skill's own folder (.claude/skills/<name>/), material the workspace
    # published as part of that skill, the same way get_skill returns its
    # SKILL.md body; it cannot reach any other workspace file.
    "get_skill", "create_skill", "read_skill_file",
    # conversation handoff (tools/handoff.py): ends the turn and names the
    # next agent, which then runs on its own tool set (checked at its own
    # build). Nothing comes back to the caller, so unlike run_agent_tool it
    # opens no path for the caller to read through another agent.
    "handoff_to_agent",
    # consult_advisor (tools/advisor.py): sends a question and context the
    # agent writes to the advisor model the operator chose on the Models
    # page, the same provider path the agent's own model calls take, and
    # returns that model's answer. It reads nothing the agent does not
    # already hold and reaches no destination the run does not already use.
    "consult_advisor",
    # The consent portal (connectors/consent/tools.py). request_account_access
    # hands the person in the conversation a link to a page they open
    # themselves; revoke_account_access drops that person's own grant. Neither
    # reads data nor sends anything out; what a grant lets a connector's
    # tools do is classified on those tools.
    "request_account_access", "revoke_account_access",

    # ── filesystem writes ────────────────────────────────────────────────────
    # write_file, create_file and apply_unified_diff all return only the
    # workspace-relative path(s) touched (apply_unified_diff also echoes the
    # op per path); delete_file returns the path it removed. None of them read
    # existing file content back into the caller's context, so unlike
    # read_file/list_files/search_text they grant nothing.
    "write_file", "delete_file", "apply_unified_diff", "create_file",
    # save_workspace_file stores text the agent already holds as a workspace
    # file and returns only the new record (id, name, size), like write_file.
    "save_workspace_file",

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
    # list_models_tool returns catalog ids and context windows, configuration
    # the Models page shows to everyone, never a key or a prompt.
    "list_models_tool",

    # ── schedule management: writes ──────────────────────────────────────────
    # schedule_task creates a job from caller-supplied fields; cancel_scheduled
    # and update_scheduled report/change a job the caller named by id. Reading
    # the roster (list_scheduled) is classified above.
    "schedule_task", "cancel_scheduled", "update_scheduled",
    # schedule_agent_run, schedule_flow, schedule_loop and
    # schedule_memory_consolidation create a job for an agent, a flow, a loop
    # or a pool of the workspace; pause/resume change a job's status. None
    # can set a deployment's secrets, the one resource that is a grant.
    "schedule_agent_run", "schedule_flow", "schedule_loop", "schedule_memory_consolidation", "pause_scheduled",
    "resume_scheduled",

    # ── flow / team / loop management ─────────────────────
    # All of these are configuration the operator authored *for* the agents —
    # a graph of nodes and edges, a roster, an exit criterion — never operator data in the sense a
    # task, a project record or an agent's instructions are. Reading, writing,
    # deleting and validating any of them grants nothing.
    "create_flow_tool", "get_flow_tool", "modify_flow_tool", "delete_flow_tool",
    "validate_flow_tool",
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
    # (run_team_tool / run_loop_tool) is a delegation edge
    # instead — see DELEGATING_TOOLS below, which is where their real
    # capability lives (the run's content is read back later through
    # get_team_run_tool / get_loop_run_tool, already
    # classified READS_PRIVATE above).
    "stop_team_run_tool", "stop_loop_run_tool",

    # ── visualization / views / scene tools ────────────────────────────────
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
    "sim_configure", "math_plot", "view_annotate", "slides_add", "slides_style",
    "document_set", "view_compute", "view_serve_stop",

    # ── documentation ─────────────────────────────────────────────────────────
    # Read-only over files the product ships — public by construction, so
    # neither private data nor untrusted input.
    "search_docs", "read_doc",

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
    "team_management": frozenset(),
    "loop_management": frozenset(),
    # The group's reads carry the grant its read tools do.
    "project_management": frozenset({READS_PRIVATE}),
    "entity_runs": frozenset({READS_PRIVATE}),
    # Product documentation the operator installed with the product. Public by
    # construction, so it is neither private data nor untrusted input.
    "docs": frozenset(),
}


# ── Configuration-dependent grants ────────────────────────────────────────────
#
# run_code (tools/run_code.py) runs a snippet in a sandbox (sandbox/) with no
# network and at most a read-only workspace mount: it can read private files
# but has no way in for untrusted text and no way out. With
# CODE_RUNNER_FALLBACK=local the same snippet runs as a host subprocess with
# the network and the host's filesystem, which is run_shell's whole trifecta.
# The claim is looked up lazily and fails closed: if the setting cannot be read, the worst
# case applies.

_RUN_CODE_SANDBOXED: FrozenSet[str] = frozenset({READS_PRIVATE})


def run_code_grants() -> FrozenSet[str]:
    """What ``run_code`` grants under the current configuration."""
    try:
        from common.config import settings
        fallback = str(getattr(settings, "code_runner_fallback", "") or "").strip().lower()
    except Exception:  # noqa: BLE001 - an unreadable setting falls back to the default grant
        log.debug("code runner fallback setting unreadable", exc_info=True)
        return CAPABILITY_GRANTS["run_code"]
    if fallback == "local":
        return CAPABILITY_GRANTS["run_code"]
    return _RUN_CODE_SANDBOXED


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
    "run_agent_tool", "delegate_task_tool", "wait_for_agent_tool", "run_flow_tool",
    "run_new_flow_tool", "run_team_tool", "run_loop_tool",
})

# create_agent_tool / modify_agent_tool are NOT in DELEGATING_TOOLS. They can
# hand another agent an arbitrary tool list, which looks like the same shape —
# but whatever they create or change is itself re-validated by the save-time
# guard (``agents.registry.add_agent`` -> ``check_agent_tools``) before it can
# be persisted. An agent holding create/modify can therefore only ever reach a
# tool set the guard would already have accepted on its own; there is no
# additional reach for this graph to add on top of that.


# ── Declared secrets ─────────────────────────────────────────────────────────
#
# An agent that declares workspace secrets (``AgentSpec.secrets``, see
# common/secrets.py) receives them as environment variables, which is reading
# private data whatever its tools are. Rather than a second input to every
# check, a declared secret travels as a pseudo tool id ``secrets:<NAME>``
# that grants reads_private, so it shows up in ``capability_sources`` and in a
# Violation under exactly that label.

SECRET_GRANT_PREFIX = "secrets:"


def secret_grant_ids(names: Optional[Iterable[str]]) -> List[str]:
    """The pseudo tool ids for an agent's declared secrets."""
    out: List[str] = []
    for name in names or []:
        label = f"{SECRET_GRANT_PREFIX}{str(name).strip()}"
        if str(name).strip() and label not in out:
            out.append(label)
    return out


def _secrets_of(agent_id: str) -> List[str]:
    """A registered agent's declared secrets as pseudo tool ids. Never raises."""
    try:
        from agents.registry import get_agent  # lazy: avoid an import cycle
        spec = get_agent(agent_id)
    except Exception:  # noqa: BLE001 - an agent lookup that fails grants no secrets
        log.debug("agent secrets lookup failed for %s", agent_id, exc_info=True)
        return []
    return secret_grant_ids(getattr(spec, "secrets", None)) if spec is not None else []


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
    except Exception:  # noqa: BLE001 - an agent lookup that fails means no delegation allow-list
        log.debug("agent delegation lookup failed for %s", agent_id, exc_info=True)
        return None
    if spec is None:
        return None
    allow = list(getattr(spec, "delegates", None) or [])
    return _expand_roles(allow) or None


def _expand_roles(ids: Sequence[str]) -> List[str]:
    """``ids`` with each workspace role reference (``@coder``) replaced by
    every agent that may hold it in some workspace (agents/roles.py). The
    guard judges an agent without a workspace, so it must see every path a
    workspace's binding can open. Never raises: without the roles module the
    ids stay as they are."""
    if not any(str(i).startswith("@") for i in ids or []):
        return list(ids or [])
    try:
        from agents.roles import expand_all  # lazy: avoid an import cycle
        return expand_all(ids)
    except Exception:  # noqa: BLE001 - a reference left unexpanded reaches no agent at run time either
        log.debug("capability guard: could not expand role references", exc_info=True)
        return list(ids or [])


def _all_agent_ids() -> List[str]:
    """Every agent id currently in the registry. Lazy import, never fatal."""
    try:
        from agents.registry import list_agents  # lazy: avoid an import cycle
        return [spec.id for spec in list_agents()]
    except Exception:  # noqa: BLE001 - an unreadable registry lists no agents
        log.debug("agent registry listing failed", exc_info=True)
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
            allow = _expand_roles(list(root_delegates))
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
                # A delegate's declared secrets are part of what it can read,
                # so they are reached through delegation like its tools are.
                target_tools = list(resolve_agent_tools(target_id) or [])
                target_tools += [s for s in _secrets_of(target_id) if s not in target_tools]
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
# The exposure is already live, before any web tool exists: the git issue sync
# and the tracker sync pull attacker-controllable text into agent context, and
# a flow's trigger route carries whatever a caller posted. A tool-grant
# check cannot see this, so runs on these channels are evaluated with
# ``ingests_untrusted`` added.

UNTRUSTED_CHANNELS: FrozenSet[str] = frozenset({
    "git_issue", "issue_sync", "webhook", "tracker_sync",
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
    if tool_id == "run_code":
        return run_code_grants()
    if tool_id in CAPABILITY_GRANTS:
        return CAPABILITY_GRANTS[tool_id]
    if tool_id in CAPABILITY_GRANTS_EXTRA:
        return CAPABILITY_GRANTS_EXTRA[tool_id]
    if tool_id in ALIAS_GRANTS:
        return ALIAS_GRANTS[tool_id]
    if tool_id in REVIEWED_NO_GRANT:
        return frozenset()
    if tool_id.startswith(SECRET_GRANT_PREFIX):
        return frozenset({READS_PRIVATE})
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
    except Exception:  # noqa: BLE001 - a registry that cannot load makes the tool id unknown
        log.debug("tool registry lookup failed for %s", tool_id, exc_info=True)
        return False


def is_recognised_tool_id(tool_id: str) -> bool:
    """True when ``grants_of`` would classify the id rather than warn about it.

    Exactly the recognition path of :func:`grants_of`: a classification table,
    a group alias, a secret grant
    or a catalog tool. ``False`` is the id ``grants_of`` logs as unknown, and
    the one a stored tool list no longer has any use for: a renamed or retired
    tool grants nothing and resolves to nothing at build time.
    """
    if tool_id == "run_code":
        return True
    if (tool_id in CAPABILITY_GRANTS or tool_id in CAPABILITY_GRANTS_EXTRA
            or tool_id in ALIAS_GRANTS or tool_id in REVIEWED_NO_GRANT):
        return True
    if tool_id.startswith(SECRET_GRANT_PREFIX):
        return True
    return _is_known_tool(tool_id)


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
    a violation can name "channel:git_issue" alongside real tool ids.
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
    that grants it (``{"ingests_untrusted": "channel:git_issue"}``), so run-level
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
    "is_recognised_tool_id",
    "UNTRUSTED_CHANNELS", "channel_capabilities",
    "BLOCKED_COMBINATIONS", "Rule", "Violation",
    "grants_of", "capabilities_of", "capability_sources",
    "check_combination", "explain",
    "effective_capabilities", "effective_capability_sources",
    "check_effective_combination",
    "SECRET_GRANT_PREFIX", "secret_grant_ids",
    "run_code_grants",
]
