"""
Routes: memory_versions (fourth-cycle stage 2, feature G).

The version history of a memory pool's blocks, notes and structured slots:
listing, reading one version, restoring it and redacting it.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from memory.store import MemoryStore
from memory import versions as memory_versions
from common.auth import LOCAL_OPERATOR_ID

router = APIRouter(prefix="/api/memory", tags=["memory_versions"])


def _get_pool_or_404(memory_id: str):
    mem = MemoryStore().get(memory_id)
    if mem is None:
        raise HTTPException(status_code=404, detail="Memory pool not found")
    return mem


@router.get("/{memory_id}/versions")
async def list_versions(memory_id: str, 
                        kind: Optional[str] = None, item_key: Optional[str] = None,
                        limit: int = 100):
    _get_pool_or_404(memory_id)
    return {"versions": memory_versions.list_versions(memory_id, kind=kind,
                                                       item_key=item_key, limit=limit)}


@router.get("/{memory_id}/versions/{version_id}")
async def get_version(memory_id: str, version_id: int):
    _get_pool_or_404(memory_id)
    version = memory_versions.get_version(version_id)
    if version is None or str(version["memory_id"]) != str(memory_id):
        raise HTTPException(status_code=404, detail="Version not found")
    return version


class RedactBody(BaseModel):
    also_current: bool = False


#: Who a restore or a redaction is stamped with on the version row: the operator.
_ACTOR = {"actor_kind": "local", "actor_id": LOCAL_OPERATOR_ID}


@router.post("/{memory_id}/versions/{version_id}/restore")
async def restore_version(memory_id: str, version_id: int):
    _get_pool_or_404(memory_id)
    try:
        result = memory_versions.restore(memory_id, version_id, _ACTOR)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@router.post("/{memory_id}/versions/{version_id}/redact")
async def redact_version(memory_id: str, version_id: int, body: RedactBody):
    _get_pool_or_404(memory_id)
    try:
        result = memory_versions.redact(memory_id, version_id, _ACTOR,
                                        also_current=body.also_current)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result
