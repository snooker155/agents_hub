"""
An agent works in its own workspace (docs/workspaces.md "One workspace per
run").

Every agent, and every tool it calls, reads and changes only the workspace its
run belongs to. Three rules carry that, each enforced where it can be:

* **A ``workspace`` argument names this workspace or nothing.** Every tool
  that takes one is pinned at build time (agents/isolation_guard.py
  ``pin_workspace``), so ``create_task(workspace="other")`` is refused rather
  than creating a task (or a whole workspace) somewhere else.
* **A record named by id belongs to this workspace.** A tool that looks up a
  task, a view, a flow, a scheduled job, a file by id answers "not found" for
  a record of another workspace (:func:`check_record`), the way the task tools
  always have (``common.workspace_context.task_in_workspace``).
* **Tools that see the whole service are for the service's own agents.**
  :data:`SERVICE_WIDE_TOOLS` (every run, session, container and instance of
  the hub, its costs and logs, the system workspace's repository) are held only
  by :data:`SERVICE_WIDE_AGENTS`: the service agent, and the maintenance
  loop's agents of the system workspace. Refused at save time for anybody
  else, taken off at build time.

A fourth rule is about workspaces themselves: a tool that creates or manages
workspaces (:data:`WORKSPACE_ADMIN_TOOLS`) works only for the main agent, and
only in a run of the ``default`` workspace.

The assistant (:data:`ASSISTANT_AGENT`, routes/assistant.py) is the one agent
whose reach depends on who talks to it. It runs each turn in the workspace the
person picked (one they can see), pinned there like any other agent. In an
administrator's service thread, which runs in ``default``, it also holds
:data:`ASSISTANT_SERVICE_TOOLS` and the workspace management tools; the route
asks for that with ``service_mode`` and the factory grants it only then.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, List, Optional

log = logging.getLogger(__name__)

#: Agents with access to the whole service. The service agent watches and
#: operates the hub; the system workspace's loop agents (common/system_workspace.py)
#: diagnose it from its runs and errors and work on its repository copy.
SERVICE_WIDE_AGENTS = frozenset({"service_agent", "system_doctor", "system_engineer"})

#: Tools whose answer or effect spans every workspace.
SERVICE_WIDE_TOOLS = frozenset({
    # runs, sessions and their logs, of every workspace
    "list_runs", "run_log", "list_sessions", "search_errors", "routing_log", "web_log_recent",
    "costs_summary", "prune_run_logs", "stop_run",
    # the hub's containers and resident instances
    "list_containers", "container_logs", "stop_container", "list_instances", "instance_logs",
    "instance_timeline", "stop_instance", "restart_instance",
    # users, the audit trail, health and the rest (tools/hub_lookup.py)
    "service_lookup",
    # the system workspace's repository copy
    "system_repo_sync", "system_run_tests", "system_commit", "system_attach_patch",
    "system_prune_branches",
})

#: Tools that list, create, configure or delete workspaces
#: (tools/workspace_management.py). They work only for
#: :data:`WORKSPACE_ADMIN_AGENT` in a run of :data:`WORKSPACE_ADMIN_HOME`, and
#: name their target with ``name`` (a ``workspace`` argument would be pinned
#: to the run's own, agents/isolation_guard.py).
WORKSPACE_ADMIN_TOOLS: frozenset = frozenset({
    "list_workspaces", "get_workspace", "create_workspace", "update_workspace",
    "delete_workspace", "add_workspace_agent", "remove_workspace_agent",
})
WORKSPACE_ADMIN_AGENT = "main-agent"
WORKSPACE_ADMIN_HOME = "default"

#: The service's assistant (agents/definitions/assistant).
ASSISTANT_AGENT = "assistant"
#: What the assistant holds only in an administrator's service thread: the
#: hub's health, the doctor, and the service agent's lists and stop buttons.
#: Not the run, error and container logs: they carry text anyone could have
#: written, and the assistant can also send messages out (the capability
#: guard's trifecta); reading logs stays with the service agent.
ASSISTANT_SERVICE_TOOLS: frozenset = frozenset({
    "service_health", "run_diagnostics", "list_sessions", "routing_log", "costs_summary",
    "stop_run", "list_containers", "stop_container", "list_instances", "stop_instance",
    "restart_instance", "service_lookup",
})


class ScopeError(ValueError):
    """A tool set or a call that would reach outside the agent's workspace.
    A ValueError, so the routes' ``except ValueError -> 400`` surfaces it."""


def is_service_wide(agent_id: Optional[str]) -> bool:
    return str(agent_id or "") in SERVICE_WIDE_AGENTS


def _assistant_tool_allowed(workspace: Optional[str], service_mode: Optional[bool]) -> bool:
    """An assistant's service tool: always storable; at build time only in a
    service thread, which runs in ``default``."""
    if workspace is None and service_mode is None:
        return True
    if not service_mode:
        return False
    from common.workspace_context import normalize_workspace_name
    return (normalize_workspace_name(workspace or "") or "") == WORKSPACE_ADMIN_HOME


def tool_allowed(agent_id: Optional[str], tool_id: str, workspace: Optional[str] = None,
                 *, service_mode: Optional[bool] = None) -> bool:
    """Whether ``agent_id`` may hold ``tool_id`` when it runs in ``workspace``
    (``None``: wherever it runs, the save time question). ``service_mode``
    is the assistant's build in an administrator's service thread."""
    tid = str(tool_id or "")
    if str(agent_id or "") == ASSISTANT_AGENT and (
            tid in ASSISTANT_SERVICE_TOOLS or tid in WORKSPACE_ADMIN_TOOLS):
        return _assistant_tool_allowed(workspace, service_mode)
    if tid in SERVICE_WIDE_TOOLS:
        return is_service_wide(agent_id)
    if tid in WORKSPACE_ADMIN_TOOLS:
        if str(agent_id or "") != WORKSPACE_ADMIN_AGENT:
            return False
        if workspace is not None:
            from common.workspace_context import normalize_workspace_name
            return (normalize_workspace_name(workspace) or "") == WORKSPACE_ADMIN_HOME
    return True


def offenders(agent_id: Optional[str], tool_ids: Iterable[str],
              workspace: Optional[str] = None, *, service_mode: Optional[bool] = None) -> List[str]:
    out: List[str] = []
    for tid in tool_ids or []:
        tid = str(tid)
        if tid and not tool_allowed(agent_id, tid, workspace, service_mode=service_mode) and tid not in out:
            out.append(tid)
    return out


def check_agent_tools(agent_id: Optional[str], tool_ids: Iterable[str]) -> None:
    """Save time: raise :class:`ScopeError` naming the tools ``agent_id`` may
    not hold."""
    bad = offenders(agent_id, tool_ids)
    if bad:
        raise ScopeError(
            f"'{agent_id}' cannot hold {', '.join(bad)}: "
            + ("these tools see every workspace of the service and belong to the service's own "
               "agents (" + ", ".join(sorted(SERVICE_WIDE_AGENTS)) + ")"
               if any(b in SERVICE_WIDE_TOOLS for b in bad) else
               f"workspace management belongs to {WORKSPACE_ADMIN_AGENT} in the "
               f"'{WORKSPACE_ADMIN_HOME}' workspace"))


def current_agent() -> Optional[str]:
    """The agent of the running tool call, when known."""
    try:
        from agents.agent_loop import current_state
        state = current_state()
        if state is not None and getattr(state, "agent_id", ""):
            return str(state.agent_id)
    except Exception:  # noqa: BLE001 - outside the loop: the context variable below
        log.debug("workspace scope: no loop state", exc_info=True)
    try:
        from common.agent_context import current_agent_id
        return current_agent_id.get() or None
    except Exception:  # noqa: BLE001 - not known
        return None


def current_workspace() -> Optional[str]:
    try:
        from common.workspace_context import resolve_active_workspace
        return resolve_active_workspace() or None
    except Exception:  # noqa: BLE001 - not known
        return None


def same_workspace(record_workspace: Any, workspace: Optional[str]) -> bool:
    from common.workspace_context import normalize_workspace_name
    have = normalize_workspace_name(str(record_workspace)) if record_workspace not in (None, "") else None
    want = normalize_workspace_name(workspace) if workspace else None
    # A record that names no workspace is the default one's.
    return (have or "default") == (want or "default")


def check_record(record_workspace: Any, *, what: str = "record",
                 workspace: Optional[str] = None) -> Optional[str]:
    """``None`` when the running agent may touch a record of
    ``record_workspace``, else the error a tool answers with. The service's
    own agents reach every workspace; everybody else, only their own."""
    if is_service_wide(current_agent()):
        return None
    ws = workspace if workspace is not None else current_workspace()
    if ws is None or same_workspace(record_workspace, ws):
        return None
    return f"{what} is not in this workspace ('{ws}')"


__all__ = [
    "ASSISTANT_AGENT", "ASSISTANT_SERVICE_TOOLS", "SERVICE_WIDE_AGENTS", "SERVICE_WIDE_TOOLS", "ScopeError", "WORKSPACE_ADMIN_AGENT",
    "WORKSPACE_ADMIN_HOME", "WORKSPACE_ADMIN_TOOLS", "check_agent_tools", "check_record",
    "current_agent", "current_workspace", "is_service_wide", "offenders", "same_workspace",
    "tool_allowed",
]
