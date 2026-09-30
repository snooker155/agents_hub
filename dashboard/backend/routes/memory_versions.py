"""
Routes: memory_versions (fourth-cycle stage 2, feature G).

The version history of a memory pool's blocks, notes and structured slots:
listing, reading one version, restoring it and redacting it. The pool
visibility check is the one ``routes/memory.py`` already uses
(``_require_pool_visible``), imported rather than copied so the two routers
agree on who may see a pool without one of them drifting out of date.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from routes.memory import _require_pool_visible
from memory.store import MemoryStore
from memory import versions as memory_versions
from common import audit, identity

router = APIRouter(prefix="/api/memory", tags=["memory_versions"])


def _get_pool_or_404(memory_id: str):
    mem = MemoryStore().get(memory_id)
    if mem is None:
        raise HTTPException(status_code=404, detail="Memory pool not found")
    return mem


@router.get("/{memory_id}/versions")
async def list_versions(memory_id: str, request: Request,
                        kind: Optional[str] = None, item_key: Optional[str] = None,
                        limit: int = 100):
    mem = _get_pool_or_404(memory_id)
    _require_pool_visible(request, mem)
    return {"versions": memory_versions.list_versions(memory_id, kind=kind,
                                                       item_key=item_key, limit=limit)}


@router.get("/{memory_id}/versions/{version_id}")
async def get_version(memory_id: str, version_id: int, request: Request):
    mem = _get_pool_or_404(memory_id)
    _require_pool_visible(request, mem)
    version = memory_versions.get_version(version_id)
    if version is None or str(version["memory_id"]) != str(memory_id):
        raise HTTPException(status_code=404, detail="Version not found")
    return version


class RedactBody(BaseModel):
    also_current: bool = False


def _audit_actor(request: Request) -> dict:
    principal = identity.request_principal(request)
    fields = audit.actor_fields(principal)
    return {"actor_kind": fields.get("actor_kind"), "actor_id": fields.get("actor_id")}


@router.post("/{memory_id}/versions/{version_id}/restore")
async def restore_version(memory_id: str, version_id: int, request: Request):
    mem = _get_pool_or_404(memory_id)
    _require_pool_visible(request, mem)
    principal = identity.request_principal(request)
    try:
        result = memory_versions.restore(memory_id, version_id, _audit_actor(request))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit.record("memory.restore", principal=principal, object_type="memory_pool",
                 object_id=str(memory_id), workspace=mem.workspace,
                 details={"kind": result["kind"], "item_key": result["item_key"],
                          "restored_from_version": version_id, "new_version": result["version"]})
    return result


@router.post("/{memory_id}/versions/{version_id}/redact")
async def redact_version(memory_id: str, version_id: int, body: RedactBody, request: Request):
    mem = _get_pool_or_404(memory_id)
    _require_pool_visible(request, mem)
    principal = identity.request_principal(request)
    try:
        result = memory_versions.redact(memory_id, version_id, _audit_actor(request),
                                        also_current=body.also_current)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit.record("memory.redact", principal=principal, object_type="memory_pool",
                 object_id=str(memory_id), workspace=mem.workspace,
                 details={"kind": result["kind"], "item_key": result["item_key"],
                          "redacted_version": version_id,
                          "also_current": bool(body.also_current),
                          "current_scrubbed": result["current_scrubbed"]})
    return result
