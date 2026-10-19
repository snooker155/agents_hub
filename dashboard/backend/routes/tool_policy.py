"""
Routes: the per-tool permission policy of an agent, and the decisions it made.

An agent's ``tool_policy`` (``AgentSpec.tool_policy``) maps a tool id, or
``"*"`` for every other tool, to ``always_allow``, ``always_ask`` or ``auto``
(tools/permission_policy.py). The workspace has the same map under
``settings.tool_policy``, edited through ``/api/workspaces/{name}/policy``;
the agent's entries win over it.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents import registry
from tools import permission_policy as policy

router = APIRouter(tags=["tool_policy"])


class ToolPolicyUpdate(BaseModel):
    tool_policy: Dict[str, Any] = {}


def _agent_or_404(agent_id: str):
    spec = registry.get_agent(agent_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return spec


def _validated(raw: Any) -> Dict[str, str]:
    """The policy to store, or a 400 naming the entry that is wrong.

    Refused rather than silently cleaned: an operator who typed a mode the
    registry does not know should see that, not find the entry gone.
    """
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="tool_policy must be an object of tool id to mode")
    valid = registry.TOOL_POLICY_MODES
    out: Dict[str, str] = {}
    for key, mode in raw.items():
        tool = str(key or "").strip()
        value = str(mode or "").strip().lower()
        if not tool:
            raise HTTPException(status_code=400, detail="tool_policy: a tool id cannot be empty")
        if len(tool) > 200:
            raise HTTPException(status_code=400, detail="tool_policy: a tool id is at most 200 characters")
        if value not in valid:
            raise HTTPException(
                status_code=400,
                detail=f"tool_policy[{tool}]: mode must be one of {', '.join(valid)}",
            )
        out[tool] = value
    return out


def _payload(spec: Any, workspace: Optional[str]) -> Dict[str, Any]:
    """What the agent's Tool policy card shows.

    ``effective`` resolves every tool on the agent's record in *workspace*
    (the agent's own workspace when none is named), so the card can say which
    mode a call will get and why; ``default`` is the same for a tool the agent
    sets nothing for, which is what the ``"*"`` inherit option falls back to.
    """
    ws = workspace or getattr(spec, "owner_workspace", None) or None
    settings = policy.workspace_settings(ws)
    tools = [str(t) for t in (spec.tools or []) if str(t).strip()]
    provider, model = policy.classifier_model(spec, ws, settings=settings)
    # A tool id nobody could have configured, so resolution falls through to
    # the "*" entries and then to the legacy default.
    default_mode, default_source = policy.resolve_mode(
        "\x00any-other-tool", spec, ws, settings=settings,
        gate_enabled=bool(settings.get("require_tool_approval")))
    return {
        "agent_id": spec.id,
        "workspace": ws,
        "tool_policy": policy.clean_policy(getattr(spec, "tool_policy", None) or {}),
        "workspace_policy": policy.workspace_policy(settings),
        "effective": policy.effective_policy(tools, spec, ws),
        "default": {"mode": default_mode, "source": default_source},
        "modes": list(registry.TOOL_POLICY_MODES),
        "gate_enabled": bool(settings.get("require_tool_approval")),
        "classifier_model": (f"{provider}/{model}" if provider and model else provider) or None,
    }


@router.get("/api/agents/{agent_id}/tool-policy")
async def get_agent_tool_policy(agent_id: str, workspace: Optional[str] = None):
    """The agent's tool policy, the mode each of its tools resolves to, and
    the valid modes."""
    return _payload(_agent_or_404(agent_id), workspace)


@router.put("/api/agents/{agent_id}/tool-policy")
async def update_agent_tool_policy(agent_id: str, body: ToolPolicyUpdate,
                                   workspace: Optional[str] = None):
    """Replace the agent's tool policy. An empty object clears it, so the
    workspace policy and the approval list decide again."""
    spec = _agent_or_404(agent_id)
    cleaned = _validated(body.tool_policy)
    new_spec = dataclasses.replace(spec, tool_policy=cleaned)
    try:
        registry.add_agent(new_spec, note="tool policy")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _payload(registry.get_agent(agent_id) or new_spec, workspace)


@router.get("/api/tool-policy/decisions")
async def list_tool_policy_decisions(
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    workspace: Optional[str] = None,
    limit: int = 50,
):
    """Recent tool policy decisions, newest first.

    A run whose agent had no database
    access (a container run on the HTTP state transport) could not write the
    collection; its decisions still ride on the run record
    (``loop.tool_decisions``), so a query by run falls back to those.
    """
    rows = policy.list_decisions(run_id=run_id or None, agent_id=agent_id or None,
                                 workspace=workspace or None, limit=limit)
    if not rows and run_id:
        rows = _from_run_record(run_id, limit)
    return {"decisions": rows}


def _from_run_record(run_id: str, limit: int) -> List[Dict[str, Any]]:
    from managers import run_manager

    run = run_manager.get_run_by_id(run_id)
    if not run:
        return []
    loop = run.get("loop") or {}
    entries = loop.get("tool_decisions") if isinstance(loop, dict) else None
    out: List[Dict[str, Any]] = []
    for entry in reversed(entries or []):
        if not isinstance(entry, dict):
            continue
        out.append({
            **entry,
            "run_id": run_id,
            "agent_id": run.get("agent_id") or "",
            "task_id": str(run.get("task_id") or ""),
            "workspace": run.get("workspace"),
            "at": run.get("finished_at") or run.get("started_at"),
            "from_run": True,
        })
        if len(out) >= max(1, min(int(limit or 50), 500)):
            break
    return out
