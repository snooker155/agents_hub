"""Guardrails over REST (guardrails/, docs/guardrails.md).

A guardrail is a workspace object like an environment (routes/environments.py,
which this module mirrors): a small, named profile that a run is checked
against by ``guardrails.runtime`` rather than launched inside. These routes
are the Guardrails page's backend, the Test box on it, the events table, and
the per-agent picker on the agent's Config tab.

Who may do what, same rule as environments: reading a list follows workspace
visibility (global plus what the caller can see); writing a workspace's own
guardrail needs the editor role in it, writing a global one needs an
administrator. Outside ``multi`` mode every check is a no-op. Every write
lands in the audit log as ``guardrail.<verb>`` (a trip/warn from a run itself
is recorded by guardrails/runtime.py as ``guardrail.trip``/``guardrail.warn``,
not here).
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common import access, audit, identity
from common.auth import WS_EDITOR
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
    text: str
    stage: str = "input"


class AgentGuardrailsUpdate(BaseModel):
    guardrails: List[str] = []


def _principal(request: Request):
    return identity.request_principal(request)


def _raise(exc: service.GuardrailServiceError):
    raise HTTPException(status_code=exc.status, detail=str(exc))


def _require_write(principal, workspace: Optional[str]) -> None:
    if workspace:
        identity.require_role(principal, workspace=workspace, role=WS_EDITOR)
    else:
        identity.require_role(principal, admin=True)


def _load_visible(request: Request, guardrail_id: str) -> Guardrail:
    try:
        g = service.require_guardrail(guardrail_id)
    except service.GuardrailServiceError as exc:
        _raise(exc)
    access.require_visible(_principal(request), g.workspace)
    return g


def _record(request: Request, action: str, g: Guardrail, details: Optional[dict] = None) -> None:
    audit.record(action, principal=_principal(request), object_type="guardrail",
                 object_id=g.id, workspace=g.workspace, ip=identity.client_ip(request),
                 details={"name": g.name, **(details or {})})


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.get("/api/guardrails")
async def list_guardrails(request: Request, workspace: Optional[str] = None,
                          include_archived: bool = False):
    """Global guardrails plus ``workspace``'s own (every scope the caller can
    see when no workspace is named)."""
    rows = service.list_guardrails(workspace, include_archived=include_archived,
                                   all_scopes=not workspace)
    dicts = [service.to_dict(g) for g in rows]
    return access.filter_by_workspace(_principal(request), dicts)


@router.post("/api/guardrails")
async def create_guardrail(request: Request, payload: GuardrailCreate):
    principal = _principal(request)
    data = payload.model_dump(exclude_unset=True)
    workspace = (payload.workspace or "").strip() or None
    _require_write(principal, workspace)
    try:
        g = service.create_guardrail(data)
    except service.GuardrailServiceError as exc:
        _raise(exc)
    _record(request, "guardrail.create", g, {"kind": g.kind, "stage": g.stage})
    return service.to_dict(g)


@router.get("/api/guardrails/events")
async def list_guardrail_events(request: Request, workspace: Optional[str] = None,
                                run_id: Optional[str] = None, guardrail_id: Optional[str] = None,
                                limit: int = 200):
    """Recent findings (blocks and warns; passes are not recorded), newest
    first, narrowed to the workspaces the caller can see."""
    if workspace:
        access.require_visible(_principal(request), workspace)
    rows = store.list_events(workspace=workspace, run_id=run_id, guardrail_id=guardrail_id,
                             limit=limit)
    return access.filter_by_workspace(_principal(request), rows)


@router.get("/api/guardrails/{guardrail_id}")
async def get_guardrail(request: Request, guardrail_id: str):
    return service.to_dict(_load_visible(request, guardrail_id))


@router.patch("/api/guardrails/{guardrail_id}")
async def update_guardrail(request: Request, guardrail_id: str, payload: GuardrailUpdate):
    principal = _principal(request)
    current = _load_visible(request, guardrail_id)
    _require_write(principal, current.workspace)
    patch = payload.model_dump(exclude_unset=True)
    if "workspace" in patch:
        target = (patch["workspace"] or "").strip() or None
        if target != current.workspace:
            _require_write(principal, target)
    try:
        g = service.update_guardrail(guardrail_id, patch)
    except service.GuardrailServiceError as exc:
        _raise(exc)
    _record(request, "guardrail.update", g, {"fields": sorted(patch)})
    return service.to_dict(g)


@router.post("/api/guardrails/{guardrail_id}/archive")
async def archive_guardrail(request: Request, guardrail_id: str):
    current = _load_visible(request, guardrail_id)
    _require_write(_principal(request), current.workspace)
    try:
        g = service.archive_guardrail(guardrail_id)
    except service.GuardrailServiceError as exc:
        _raise(exc)
    _record(request, "guardrail.archive", g)
    return service.to_dict(g)


@router.delete("/api/guardrails/{guardrail_id}")
async def delete_guardrail(request: Request, guardrail_id: str):
    current = _load_visible(request, guardrail_id)
    _require_write(_principal(request), current.workspace)
    try:
        service.delete_guardrail(guardrail_id)
    except service.GuardrailServiceError as exc:
        _raise(exc)
    _record(request, "guardrail.delete", current)
    return {"deleted": True}


@router.post("/api/guardrails/{guardrail_id}/test")
async def test_guardrail(request: Request, guardrail_id: str, payload: GuardrailTest):
    """Dry run: the check result for ``text``, with no event and no audit
    record written (visibility only, no editor role needed)."""
    g = _load_visible(request, guardrail_id)
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
async def update_agent_guardrails(request: Request, agent_id: str, payload: AgentGuardrailsUpdate):
    """Set the guardrail ids this agent lists on top of the ones already
    applying to it (``applies_to: "all"`` ones, and its workspace's / the
    global ones), the same per-field pattern routes/agents.py uses for its
    own agent fields."""
    from agents import registry

    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    principal = _principal(request)
    workspace = getattr(spec, "owner_workspace", None)
    if workspace:
        identity.require_role(principal, workspace=workspace, role=WS_EDITOR)
    ids = []
    for gid in payload.guardrails or []:
        gid = str(gid).strip()
        if gid and gid not in ids:
            ids.append(gid)
    new_spec = dataclasses.replace(spec, guardrails=ids)
    registry.add_agent(new_spec)
    audit.record("guardrail.agent_update", principal=principal, object_type="agent",
                 object_id=agent_id, workspace=workspace, ip=identity.client_ip(request),
                 details={"guardrails": ids})
    return {"guardrails": new_spec.guardrails}
