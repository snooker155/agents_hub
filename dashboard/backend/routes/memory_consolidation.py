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

from common import audit, identity
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


class ConsolidateBody(BaseModel):
    session_limit: Optional[int] = None


@router.post("/{memory_id}/consolidate")
async def start_consolidation(memory_id: str, body: ConsolidateBody, request: Request):
    mem = _get_pool_or_404(memory_id)
    _require_pool_visible(request, mem)
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
    mem = MemoryStore().get(row["memory_id"])
    if mem is not None:
        _require_pool_visible(request, mem)
    return row


class ApplyBody(BaseModel):
    agent_id: str
    workspace: Optional[str] = None


@router.post("/consolidations/{job_id}/apply")
async def apply_consolidation(job_id: str, body: ApplyBody, request: Request):
    row = _get_job_or_404(job_id)
    mem = MemoryStore().get(row["memory_id"])
    if mem is not None:
        _require_pool_visible(request, mem)
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
    mem = MemoryStore().get(row["memory_id"])
    if mem is not None:
        _require_pool_visible(request, mem)
    principal = identity.request_principal(request)
    try:
        result = consolidation.discard(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record("memory.consolidate_discard", principal=principal, object_type="memory_pool",
                 object_id=str(row["memory_id"]), workspace=row.get("workspace"),
                 details={"consolidation_id": job_id})
    return result
