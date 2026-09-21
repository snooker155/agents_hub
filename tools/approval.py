"""
Which tool calls need a human's approval, and what a refused call says back.

Two things live here, deliberately together because they are one policy:

* **The list.** :data:`NEEDS_APPROVAL` names the tool ids that always need an
  explicit human yes: the ones that delete, stop, restart or write over
  something. :func:`needs_approval` layers the per-agent overrides on top
  (``AgentSpec.approval_tools`` adds, ``AgentSpec.approval_exempt`` removes).
* **The refusal text.** ``tools/service_ops.py`` has refused destructive calls
  with an ``approval_required`` JSON error since long before this gate existed,
  and that convention is what the agents are prompted for. The gate reuses it
  verbatim in chat (:func:`approval_required_text`) instead of inventing a
  second wording, and service_ops delegates its own helper here so there is one
  message shape to keep.

The gate itself is opt-in per workspace (``settings.require_tool_approval``,
default off), so an installation that never turns it on behaves exactly as
before: the list is inert and service_ops stays the only, advisory, check.

Where a call is *held* depends on the surface, and that split is the whole point
(see agents/hooks.py for the gate that applies it):

- In a tracked task run the call parks the task in ``awaiting_approval`` with the
  tool and its arguments on it, and the operator answers from the task page. The
  approved call is recorded on the task as a fingerprint, so the resumed run may
  make that one call and no other.
- In chat there is no task to park, so the gate refuses the call and tells the
  agent to say what it wanted to run. That is advisory, exactly like service_ops.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

# Tool ids that always need a human yes. Every one of them ends something, drops
# something, or writes over something a person may not get back: the cost of a
# wrong call is not symmetric with the cost of asking. Ids not registered in
# tools/registry.py are simply never matched, so the set can name a tool that
# only some installations have.
NEEDS_APPROVAL: frozenset[str] = frozenset({
    # Arbitrary execution and destructive filesystem writes.
    "run_shell",
    "delete_file",
    "apply_unified_diff",
    # Deleting a hub entity: the record and its history go with it.
    "delete_agent_tool",
    "delete_flow_tool",
    "delete_team_tool",
    "delete_loop_tool",
    "delete_project_tool",
    "delete_world_tool",
    "delete_scenario_tool",
    "delete_eval_tool",
    "remove_eval_case_tool",
    # Stopping or restarting something that is running: whatever it was
    # producing is lost.
    "stop_run",
    "stop_node",
    "restart_node",
    "stop_container",
    "prune_run_logs",
})

# Reasoning scratchpad tools are never gated: they have no effect outside the
# run. Kept in sync with ``reasoning.think_gate._REASONING_TOOL_NAMES`` (the
# same copy ``agents.callbacks.guards`` keeps, for the same reason: importing a
# private name across modules is worse than a named duplicate).
REASONING_TOOL_NAMES = frozenset({
    "think",
    "plan",
    "save_plan",
    "get_plan",
    "list_plans",
    "update_plan_status",
    "delete_plan",
    "assess_complexity",
})

# ``ask_user`` is how an agent reaches the human in the first place. Gating it
# would need approval to ask for approval.
NEVER_GATED = REASONING_TOOL_NAMES | frozenset({"ask_user"})


def needs_approval(tool_id: str, agent_spec: Any = None) -> bool:
    """True when a call to *tool_id* needs a human yes before it runs.

    The default list is :data:`NEEDS_APPROVAL`. An agent record may widen it
    (``approval_tools``) or narrow it (``approval_exempt``); the exemption wins,
    so an operator can hand one agent a gated tool without turning the gate off
    for everybody. Both fields are optional and read defensively, so a spec that
    predates them (or is not an AgentSpec at all) behaves as the default.
    """
    name = str(tool_id or "").strip()
    if not name or name in NEVER_GATED:
        return False

    extra = {str(x) for x in (getattr(agent_spec, "approval_tools", None) or [])}
    exempt = {str(x) for x in (getattr(agent_spec, "approval_exempt", None) or [])}
    if name in exempt:
        return False
    return name in NEEDS_APPROVAL or name in extra or _mcp_needs_approval(name)


def _mcp_needs_approval(tool_id: str) -> bool:
    """True when this MCP tool's server says calls to it need a human yes.

    The list above cannot name these: the tools live on somebody else's server,
    they differ per workspace, and their ids are only known once a server is
    attached. So the claim is made where the server is configured — ``approval``
    is ``"none"``, ``"all"``, or the names of the tools to gate — and read back
    here, which keeps one gate rather than two.

    The stored list may name a tool either way round: ``search`` as the remote
    server calls it, or ``mcp__tickets__search`` as the hub does. Both are the
    same tool, and an operator reading either the page or an agent record should
    not have to know which spelling this file wanted.
    """
    if not tool_id.startswith("mcp__"):
        return False
    try:
        from common.workspace_context import resolve_active_workspace
        from mcp_client.client import split_tool_id
        from mcp_client.store import get_server

        split = split_tool_id(tool_id)
        if split is None:
            return False
        server_id, remote_name = split
        record = get_server(resolve_active_workspace(), server_id)
        if record is None:
            return False
        approval = record.get("approval")
        if isinstance(approval, (list, tuple, set)):
            return tool_id in approval or remote_name in approval
        return str(approval or "none").strip().lower() == "all"
    except Exception:
        # Same failure posture as ``approval_gate_enabled``: an unreadable
        # config must not make every tool call look gated, which would stop the
        # run outright.
        return False


def workspace_name(value: Optional[str] = None) -> Optional[str]:
    """The bare workspace name for a value that may be an operating path.

    An agent is built with its *operating* path, which for a task is
    ``<workspace>/<project>`` — so the basename is the project, not the
    workspace, and a settings lookup keyed on it would read the wrong record (or
    none). ``workspace_name_from_path`` recovers the workspace for both shapes;
    with nothing passed, the active workspace of the run is used.
    """
    from common.workspace_context import resolve_active_workspace, workspace_name_from_path
    if value:
        name = workspace_name_from_path(value)
        if name:
            return name
    return resolve_active_workspace()


def approval_gate_enabled(workspace: Optional[str] = None) -> bool:
    """True when the workspace asked for tool calls to be gated.

    Opt-in and off by default, so existing runs are unchanged until an operator
    sets ``require_tool_approval`` in the workspace settings. Read live (the same
    way ``agent_mode`` is read in managers/node_manager.py) so flipping it
    applies to the next run without a restart.
    """
    try:
        ws = workspace_name(workspace)
        if not ws:
            return False
        from workspace import get_workspace_metadata
        settings = get_workspace_metadata(ws).get("settings") or {}
        return bool(settings.get("require_tool_approval"))
    except Exception:
        return False


def call_fingerprint(tool_id: str, tool_input: Any) -> str:
    """A stable id for one (tool, arguments) pair.

    What the operator approves is a specific call, not a tool: the resumed run
    may make *that* call and nothing else. Arguments are JSON-encoded with sorted
    keys so the same call fingerprints the same way in the backend (which hashes
    what it stored on the task) and in the agent subprocess (which hashes what
    the model just asked for).
    """
    try:
        payload = json.dumps(tool_input, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        payload = str(tool_input)
    raw = f"{str(tool_id or '').strip()}\x00{payload}"
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def approval_required_text(
    action: str,
    target: str,
    effect: str,
    *,
    guidance: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """The JSON refusal a gated call gets when there is no task to park.

    ``guidance`` replaces the default "call again with user_approved=True" line
    for tools that have no such argument: those cannot be re-driven by the agent
    at all, so it is told to describe the call and wait instead of retrying.
    """
    tail = guidance or (
        "Tell the user exactly this, and only call again with user_approved=True "
        "once they have agreed."
    )
    body: Dict[str, Any] = {
        "ok": False,
        "error": f"{action} needs the user's approval. It would {effect}. {tail}",
        "code": "approval_required",
        "action": action,
        "target": target,
        "effect": effect,
    }
    if extra:
        body.update(extra)
    return json.dumps(body, ensure_ascii=False, indent=2, default=str)


def gate_refusal_text(tool_id: str, tool_input: Any, reason: str = "") -> str:
    """The refusal the gate hands back in chat, where nothing can park.

    Worded for a tool that has no ``user_approved`` argument to retry with: the
    agent's next move is to tell the user what it wanted to run and wait for an
    answer in the conversation, not to call again.
    """
    try:
        pretty = json.dumps(tool_input, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        pretty = str(tool_input)
    effect = f"run `{tool_id}` with {pretty}"
    if reason:
        effect = f"{effect} ({reason})"
    return approval_required_text(
        tool_id,
        pretty,
        effect,
        guidance=(
            "Do not call it again. Tell the user exactly what you wanted to run "
            "and why, and wait for their answer before doing anything else."
        ),
    )


__all__ = [
    "NEEDS_APPROVAL",
    "NEVER_GATED",
    "REASONING_TOOL_NAMES",
    "approval_gate_enabled",
    "approval_required_text",
    "call_fingerprint",
    "gate_refusal_text",
    "needs_approval",
    "workspace_name",
]
