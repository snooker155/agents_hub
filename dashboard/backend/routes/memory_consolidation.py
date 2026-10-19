"""
Routes: memory consolidation (fifth-cycle stage 2, "dreams").

Starting a consolidation, polling its status and diff, and switching an
agent's binding to the result or discarding it.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from common.auth import LOCAL_OPERATOR_ID
from memory import consolidation
from memory.store import MemoryStore

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
async def start_consolidation(memory_id: str, body: ConsolidateBody):
    mem = _get_pool_or_404(memory_id)
    try:
        row = consolidation.start(
            memory_id,
            session_limit=body.session_limit or consolidation.DEFAULT_SESSION_LIMIT,
            workspace=mem.workspace, trigger="manual",
            actor_kind="local", actor_id=LOCAL_OPERATOR_ID,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return row


@router.get("/{memory_id}/consolidations")
async def list_consolidations(memory_id: str, limit: int = 20):
    _get_pool_or_404(memory_id)
    return {"consolidations": consolidation.list_for_pool(memory_id, limit=limit)}


@router.get("/consolidations/{job_id}")
async def get_consolidation(job_id: str):
    row = _get_job_or_404(job_id)
    return row


class ApplyBody(BaseModel):
    agent_id: str
    workspace: Optional[str] = None


@router.post("/consolidations/{job_id}/apply")
async def apply_consolidation(job_id: str, body: ApplyBody):
    _get_job_or_404(job_id)
    try:
        result = consolidation.apply_to_agent(job_id, body.agent_id, workspace=body.workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/consolidations/{job_id}/discard")
async def discard_consolidation(job_id: str):
    _get_job_or_404(job_id)
    try:
        result = consolidation.discard(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result
