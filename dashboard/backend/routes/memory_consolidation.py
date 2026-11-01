"""
Routes: memory consolidation (fifth-cycle stage 2, "dreams").

Starting a consolidation, polling its status and diff, and switching an
agent's binding to the result or discarding it. The pool visibility check is
the one ``routes/memory.py`` already uses (``_require_pool_visible``),
imported rather than copied, the same choice ``routes/memory_versions.py``
makes for the same reason: the two routers must agree on who may see a pool.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common import access, audit, identity, record_access
from memory import consolidation
from memory.store import MemoryStore
from routes.memory import _require_pool_visible

router = APIRouter(prefix="/api/memory", tags=["memory_consolidation"])


def _get_pool_or_404(memory_id: str):
    mem = MemoryStore().get(memory_id)
    if mem is None:
        raise HTTPException(status_code=404, detail="Memory pool not found")
    return mem


def _get_job_or_404(job_id: str) -> dict:
    row = consolidation.get(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Consolidation not found")
    return row


def _require_job_access(request: Request, row: dict, *, write: bool = False) -> None:
    """Check a consolidation through its source pool: visible to read, and
    ``editor`` in the pool's workspace to change (one that names none is the
    default's).

    A job whose pool is gone has nothing left to check it against, and the
    workspace on the row alone is not proof of the pool it came from, so a
    caller held to their memberships is refused it (404, as if it did not
    exist) rather than let through; an administrator or the local operator
    may still read or discard it.
    """
    mem = MemoryStore().get(row["memory_id"])
    if mem is None:
        if access.visible_workspaces(identity.request_principal(request)) is not None:
            raise HTTPException(status_code=404, detail="Consolidation not found")
        return
    _require_pool_visible(request, mem)
    if write:
        record_access.require_in_workspace(request, mem.workspace)


def _require_agent_rebind(request: Request, agent_id: str, workspace: Optional[str]) -> None:
    """403 unless the caller may change ``agent_id``'s memory binding in the
    workspace an apply writes it (the body's, else the agent's own): the rule
    the middleware gives ``PUT /api/agents/{id}`` for the agent's own record
    (``editor`` in its workspace, an administrator for a system agent) and
    ``/api/agents/{id}/memory`` for another workspace's override (``editor``
    there). Both come in the body, which the middleware never reads."""
    principal = identity.request_principal(request)
    if principal is None or not record_access.restricted(principal):
        return
    from agents.registry import get_agent
    from memory.binding import home_workspace
    spec = get_agent(agent_id)
    if spec is None:
        return  # apply_to_agent answers 400 for an unknown agent
    home = home_workspace(spec)
    ws = str(workspace or home)
    path = f"/api/agents/{agent_id}" if ws == home else f"/api/agents/{agent_id}/memory"
    why = record_access.check(method="PUT", path=path, named_workspace=ws,
                              role_in=lambda name: identity.membership_role(name, principal.id))
    if why:
        raise HTTPException(status_code=403, detail=why)


class ConsolidateBody(BaseModel):
    session_limit: Optional[int] = None


@router.post("/{memory_id}/consolidate")
async def start_consolidation(memory_id: str, body: ConsolidateBody, request: Request):
    mem = _get_pool_or_404(memory_id)
    _require_pool_visible(request, mem)
    record_access.require_in_workspace(request, mem.workspace)
    principal = identity.request_principal(request)
    fields = audit.actor_fields(principal)
    try:
        row = consolidation.start(
            memory_id,
            session_limit=body.session_limit or consolidation.DEFAULT_SESSION_LIMIT,
            workspace=mem.workspace, trigger="manual",
            actor_kind=fields.get("actor_kind"), actor_id=fields.get("actor_id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record("memory.consolidate_start", principal=principal, object_type="memory_pool",
                 object_id=str(memory_id), workspace=mem.workspace,
                 details={"consolidation_id": row["id"], "session_limit": row["session_limit"]})
    return row


@router.get("/{memory_id}/consolidations")
async def list_consolidations(memory_id: str, request: Request, limit: int = 20):
    mem = _get_pool_or_404(memory_id)
    _require_pool_visible(request, mem)
    return {"consolidations": consolidation.list_for_pool(memory_id, limit=limit)}


@router.get("/consolidations/{job_id}")
async def get_consolidation(job_id: str, request: Request):
    row = _get_job_or_404(job_id)
    _require_job_access(request, row)
    return row


class ApplyBody(BaseModel):
    agent_id: str
    workspace: Optional[str] = None


@router.post("/consolidations/{job_id}/apply")
async def apply_consolidation(job_id: str, body: ApplyBody, request: Request):
    row = _get_job_or_404(job_id)
    _require_job_access(request, row, write=True)
    _require_agent_rebind(request, body.agent_id, body.workspace)
    principal = identity.request_principal(request)
    try:
        result = consolidation.apply_to_agent(job_id, body.agent_id, workspace=body.workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record("memory.consolidate_apply", principal=principal, object_type="memory_pool",
                 object_id=str(row["memory_id"]), workspace=result.get("workspace"),
                 details={"consolidation_id": job_id, "agent_id": body.agent_id,
                          "new_memory_id": result.get("new_memory_id")})
    return result


@router.post("/consolidations/{job_id}/discard")
async def discard_consolidation(job_id: str, request: Request):
    row = _get_job_or_404(job_id)
    _require_job_access(request, row, write=True)
    principal = identity.request_principal(request)
    try:
        result = consolidation.discard(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record("memory.consolidate_discard", principal=principal, object_type="memory_pool",
                 object_id=str(row["memory_id"]), workspace=row.get("workspace"),
                 details={"consolidation_id": job_id})
    return result
