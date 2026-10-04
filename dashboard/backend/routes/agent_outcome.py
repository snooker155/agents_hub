"""
Routes: an agent's own default outcome (tasks/outcome.py's rubric shape, kept
on the agent instead of only on a task).

``GET/PUT /api/agents/{agent_id}/default-outcome`` reads and writes
``AgentSpec.default_outcome``. A task assigned to the agent inherits it when
the task has no outcome of its own yet (``tasks.service.assign_executor``),
so a kit's agents (``kits/``, docs/kits.md) carry a rubric that applies to
every task they are given without the caller spelling it out each time.
Saved like the other per-field agent routes: ``dataclasses.replace`` plus
``registry.add_agent``, which snapshots version history.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agents import registry
from common import audit, identity

log = logging.getLogger(__name__)

router = APIRouter(tags=["agent_outcome"])


class DefaultOutcomeUpdate(BaseModel):
    rubric: Optional[str] = None
    max_iterations: Optional[int] = None
    grader: Optional[Any] = None
    threshold: Optional[float] = None


def _payload(spec: Any) -> Dict[str, Any]:
    return {"default_outcome": dict(getattr(spec, "default_outcome", None) or {}) or None}


@router.get("/api/agents/{agent_id}/default-outcome")
async def get_default_outcome(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _payload(spec)


@router.put("/api/agents/{agent_id}/default-outcome")
async def update_default_outcome(agent_id: str, data: DefaultOutcomeUpdate, request: Request):
    from tasks.outcome import OutcomeError, normalize_outcome
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    if getattr(spec, "system", False):
        raise HTTPException(status_code=403, detail="A system agent's outcome cannot be edited")
    raw = data.model_dump(exclude_unset=True)
    new_outcome: Optional[Dict[str, Any]] = None
    if raw:
        try:
            new_outcome = normalize_outcome(raw)
        except OutcomeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    new_spec = dataclasses.replace(spec, default_outcome=new_outcome)
    try:
        registry.add_agent(new_spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        audit.record("agent.default_outcome", principal=identity.request_principal(request),
                     object_type="agent", object_id=agent_id,
                     workspace=getattr(new_spec, "owner_workspace", None),
                     ip=identity.client_ip(request), details={"cleared": new_outcome is None})
    except Exception:  # noqa: BLE001 - an audit failure must not undo the save
        log.warning("could not record the default-outcome audit entry for %s", agent_id, exc_info=True)
    return _payload(new_spec)
