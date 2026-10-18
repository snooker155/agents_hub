"""
An agent works in its own workspace (docs/workspaces.md "One workspace per
run").

Every agent, and every tool it calls, reads and changes only the workspace its
run belongs to. Two rules carry that, each enforced where it can be:

* **A ``workspace`` argument names this workspace or nothing.** Every tool
  that takes one is pinned at build time (agents/isolation_guard.py
  ``pin_workspace``), so ``create_task(workspace="other")`` is refused rather
  than creating a task (or a whole workspace) somewhere else.
* **A record named by id belongs to this workspace.** A tool that looks up a
  task, a view, a flow, a scheduled job, a file by id answers "not found" for
  a record of another workspace (:func:`check_record`), the way the task tools
  always have (``common.workspace_context.task_in_workspace``).

A third rule is about workspaces themselves: a tool that creates or manages
workspaces (:data:`WORKSPACE_ADMIN_TOOLS`) works only for the main agent, and
only in a run of the ``default`` workspace.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, List, Optional

log = logging.getLogger(__name__)

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


class ScopeError(ValueError):
    """A tool set or a call that would reach outside the agent's workspace.
    A ValueError, so the routes' ``except ValueError -> 400`` surfaces it."""


def tool_allowed(agent_id: Optional[str], tool_id: str, workspace: Optional[str] = None) -> bool:
    """Whether ``agent_id`` may hold ``tool_id`` when it runs in ``workspace``
    (``None``: wherever it runs, the save time question)."""
    tid = str(tool_id or "")
    if tid in WORKSPACE_ADMIN_TOOLS:
        if str(agent_id or "") != WORKSPACE_ADMIN_AGENT:
            return False
        if workspace is not None:
            from common.workspace_context import normalize_workspace_name
            return (normalize_workspace_name(workspace) or "") == WORKSPACE_ADMIN_HOME
    return True


def offenders(agent_id: Optional[str], tool_ids: Iterable[str],
              workspace: Optional[str] = None) -> List[str]:
    out: List[str] = []
    for tid in tool_ids or []:
        tid = str(tid)
        if tid and not tool_allowed(agent_id, tid, workspace) and tid not in out:
            out.append(tid)
    return out


def check_agent_tools(agent_id: Optional[str], tool_ids: Iterable[str]) -> None:
    """Save time: raise :class:`ScopeError` naming the tools ``agent_id`` may
    not hold."""
    bad = offenders(agent_id, tool_ids)
    if bad:
        raise ScopeError(
            f"'{agent_id}' cannot hold {', '.join(bad)}: "
            f"workspace management belongs to {WORKSPACE_ADMIN_AGENT} in the "
            f"'{WORKSPACE_ADMIN_HOME}' workspace")


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
    ``record_workspace``, else the error a tool answers with: every agent
    reaches only the workspace of its run."""
    ws = workspace if workspace is not None else current_workspace()
    if ws is None or same_workspace(record_workspace, ws):
        return None
    return f"{what} is not in this workspace ('{ws}')"


__all__ = [
    "ScopeError", "WORKSPACE_ADMIN_AGENT",
    "WORKSPACE_ADMIN_HOME", "WORKSPACE_ADMIN_TOOLS", "check_agent_tools", "check_record",
    "current_agent", "current_workspace", "offenders", "same_workspace",
    "tool_allowed",
]
