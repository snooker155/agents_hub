"""
Which agents a workspace may run: the rule the chat's agent list applies
(``GET /api/agents?workspace=``, routes/agents.py), as a predicate over one
registry entry.

Kept here rather than imported from the route module because two surfaces
outside the web chat need it and neither may drift from it: a widget (its
agent, and the agent a handoff moves a thread to, must be runnable in the
widget's workspace) and ``/v1``'s ``agent:<id>`` models (an API client sees
and runs only what the chat would offer it in that workspace).

The rule, in order:

1. System agents run everywhere.
2. An agent owned by a workspace and not shared runs only there.
3. A default-workspace-only agent runs only in ``default``.
4. Outside ``default``, a workspace with an ``allowed_agents`` list runs only
   the agents on it, plus its own agents and the system ones.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional, Set


def _system_ids() -> Set[str]:
    try:
        from workspace.storage import system_agent_ids
        return set(system_agent_ids())
    except Exception:  # noqa: BLE001 - an unreadable registry means no system agents, never a crash
        return set()


def _allowed(workspace: str) -> Optional[List[str]]:
    if not workspace or workspace == "default":
        return None
    try:
        from workspace import get_workspace_metadata
        allowed = (get_workspace_metadata(workspace) or {}).get("allowed_agents")
    except Exception:  # noqa: BLE001 - unreadable metadata reads as "no restriction", as in routes/agents.py
        return None
    return list(allowed) if isinstance(allowed, list) else None


def usable_in(spec: Any, workspace: Optional[str], *, allowed: Any = ...,
              system_ids: Optional[Set[str]] = None) -> bool:
    """Whether the registry entry ``spec`` may run in ``workspace``.

    ``allowed`` and ``system_ids`` let a caller checking many agents against
    one workspace read the workspace metadata and the system list once.
    """
    if spec is None:
        return False
    ws = (workspace or "default").strip() or "default"
    system_ids = _system_ids() if system_ids is None else system_ids
    agent_id = str(getattr(spec, "id", "") or "")
    if getattr(spec, "system", False) or agent_id in system_ids:
        return True
    owner = getattr(spec, "owner_workspace", None)
    if owner and not getattr(spec, "shared", False) and owner != ws:
        return False
    if ws != "default":
        if getattr(spec, "default_workspace_only", False):
            return False
        allowed_list = _allowed(ws) if allowed is ... else allowed
        if allowed_list is not None and agent_id not in allowed_list and owner != ws:
            return False
    return True


def agent_usable(agent_id: str, workspace: Optional[str]) -> bool:
    from agents import registry
    return usable_in(registry.get_agent(agent_id), workspace)


def usable_agents(workspace: Optional[str]) -> List[Any]:
    """Every registry agent that may run in ``workspace``, registry order."""
    from agents import registry
    ws = (workspace or "default").strip() or "default"
    allowed = _allowed(ws)
    system_ids = _system_ids()
    return [spec for spec in registry.list_agents()
            if usable_in(spec, ws, allowed=allowed, system_ids=system_ids)]


def usable_in_any(workspaces: Optional[Iterable[str]]) -> List[Any]:
    """Agents that may run in at least one of ``workspaces``; ``None`` means
    no restriction (every registry agent runs somewhere: its owner workspace,
    or ``default``)."""
    from agents import registry
    if workspaces is None:
        return list(registry.list_agents())
    seen: Set[str] = set()
    out: List[Any] = []
    for ws in sorted(set(workspaces)):
        for spec in usable_agents(ws):
            if spec.id not in seen:
                seen.add(spec.id)
                out.append(spec)
    return out


__all__ = ["agent_usable", "usable_agents", "usable_in", "usable_in_any"]
