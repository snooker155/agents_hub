"""Guardrails over REST (guardrails/, docs/guardrails.md).

A guardrail is a workspace object like an environment (routes/environments.py,
which this module mirrors): a small, named profile that a run is checked
against by ``guardrails.runtime`` rather than launched inside. These routes
are the Guardrails page's backend, the Test box on it, the events table, and
the per-agent picker on the agent's Config tab.

A trip or warn from a run itself is recorded by guardrails/runtime.py in
the events table, not here.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from guardrails import checks, service, store
from guardrails.models import Guardrail

router = APIRouter(tags=["guardrails"])


class GuardrailCreate(BaseModel):
    name: str
    description: str = ""
    workspace: Optional[str] = None
    stage: str = "input"
    kind: str = "regex"
    config: Dict[str, Any] = {}
    action: str = "block"
    applies_to: str = "all"
    enabled: bool = True
    fail_closed: bool = True
    model: Optional[str] = None


class GuardrailUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    workspace: Optional[str] = None
    stage: Optional[str] = None
    kind: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    action: Optional[str] = None
    applies_to: Optional[str] = None
    enabled: Optional[bool] = None
    fail_closed: Optional[bool] = None
    model: Optional[str] = None


class GuardrailTest(BaseModel):
    text: str = ""
    stage: str = "input"
    # A sequence guardrail is tested on calls, not text: ``[{tool, input, ok}]``
    # in the order a run would make them.
    calls: Optional[List[Dict[str, Any]]] = None


class AgentGuardrailsUpdate(BaseModel):
    guardrails: List[str] = []


def _raise(exc: service.GuardrailServiceError):
    raise HTTPException(status_code=exc.status, detail=str(exc))


def _load(guardrail_id: str) -> Guardrail:
    try:
        g = service.require_guardrail(guardrail_id)
    except service.GuardrailServiceError as exc:
        _raise(exc)
    return g


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.get("/api/guardrails")
async def list_guardrails(workspace: Optional[str] = None,
                          include_archived: bool = False):
    """Global guardrails plus ``workspace``'s own (every scope the caller can
    see when no workspace is named)."""
    rows = service.list_guardrails(workspace, include_archived=include_archived,
                                   all_scopes=not workspace)
    return [service.to_dict(g) for g in rows]


@router.post("/api/guardrails")
async def create_guardrail(payload: GuardrailCreate):
    data = payload.model_dump(exclude_unset=True)
    try:
        g = service.create_guardrail(data)
    except service.GuardrailServiceError as exc:
        _raise(exc)
    return service.to_dict(g)


@router.get("/api/guardrails/events")
async def list_guardrail_events(workspace: Optional[str] = None,
                                run_id: Optional[str] = None, guardrail_id: Optional[str] = None,
                                limit: int = 200):
    """Recent findings (blocks and warns; passes are not recorded), newest
    first."""
    return store.list_events(workspace=workspace, run_id=run_id, guardrail_id=guardrail_id,
                             limit=limit)


@router.get("/api/guardrails/{guardrail_id}")
async def get_guardrail(guardrail_id: str):
    return service.to_dict(_load(guardrail_id))


@router.patch("/api/guardrails/{guardrail_id}")
async def update_guardrail(guardrail_id: str, payload: GuardrailUpdate):
    _load(guardrail_id)
    patch = payload.model_dump(exclude_unset=True)
    try:
        g = service.update_guardrail(guardrail_id, patch)
    except service.GuardrailServiceError as exc:
        _raise(exc)
    return service.to_dict(g)


@router.post("/api/guardrails/{guardrail_id}/archive")
async def archive_guardrail(guardrail_id: str):
    _load(guardrail_id)
    try:
        g = service.archive_guardrail(guardrail_id)
    except service.GuardrailServiceError as exc:
        _raise(exc)
    return service.to_dict(g)


@router.delete("/api/guardrails/{guardrail_id}")
async def delete_guardrail(guardrail_id: str):
    _load(guardrail_id)
    try:
        service.delete_guardrail(guardrail_id)
    except service.GuardrailServiceError as exc:
        _raise(exc)
    return {"deleted": True}


@router.post("/api/guardrails/{guardrail_id}/test")
async def test_guardrail(guardrail_id: str, payload: GuardrailTest):
    """Dry run: the check result for ``text`` (or, for a sequence guardrail,
    for ``calls`` made in order: the first one it would stop), with no event
    written."""
    g = _load(guardrail_id)
    if g.kind == "sequence":
        from guardrails import sequence
        if not payload.calls:
            raise HTTPException(status_code=400, detail="a sequence guardrail is tested with 'calls'")
        return {"applies": True, **sequence.simulate(g, payload.calls)}
    stage = payload.stage if payload.stage in ("input", "output") else "input"
    if g.stage not in (stage, "both"):
        return {"applies": False, "passed": True, "reason": "",
                "message": f"This guardrail does not check the {stage} stage."}
    if checks.is_rule_kind(g.kind):
        reason, hits = checks.check_rule(g.kind, g.config, payload.text)
        return {"applies": True, "passed": reason is None,
                "reason": reason or "", "excerpt": checks.mask(payload.text, hits)}
    reason, error = checks.check_judge(g.config, payload.text, guardrail_model=g.model,
                                       workspace=g.workspace)
    if error is not None:
        return {"applies": True, "passed": not g.fail_closed, "reason": "", "error": error}
    return {"applies": True, "passed": reason is None, "reason": reason or ""}


# ── per-agent picker ─────────────────────────────────────────────────────────

@router.put("/api/agents/{agent_id}/guardrails")
async def update_agent_guardrails(agent_id: str, payload: AgentGuardrailsUpdate):
    """Set the guardrail ids this agent lists on top of the ones already
    applying to it (``applies_to: "all"`` ones, and its workspace's / the
    global ones), the same per-field pattern routes/agents.py uses for its
    own agent fields."""
    from agents import registry

    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    ids = []
    for gid in payload.guardrails or []:
        gid = str(gid).strip()
        if gid and gid not in ids:
            ids.append(gid)
    new_spec = dataclasses.replace(spec, guardrails=ids)
    registry.add_agent(new_spec)
    return {"guardrails": new_spec.guardrails}
