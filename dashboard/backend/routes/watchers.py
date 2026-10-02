"""
Routes: watchers (watchers/, docs/watchers.md).

``/api/watchers`` lists, creates, edits and deletes the observers of a
workspace; ``.../pause``, ``.../resume`` and ``.../probe`` act on one;
``/api/watchers/summary`` is what the header indicator reads (how many are
active, each one's last check and the agents that react to it);
``/api/watchers/kinds`` describes the kinds and their config fields for the
form. Writes need the workspace editor role, like environments.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common import access, audit, identity
from common.auth import WS_EDITOR
from watchers import kinds, service
from watchers.models import (
    DEFAULT_AUTO_PAUSE_AFTER,
    DEFAULT_INTERVAL_SECONDS,
    KINDS,
    MAX_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watchers", tags=["watchers"])


class WatcherCreate(BaseModel):
    workspace: str
    name: str
    kind: str
    config: Dict[str, Any] = {}
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    enabled: bool = True
    auto_pause_after: int = DEFAULT_AUTO_PAUSE_AFTER


class WatcherUpdate(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    interval_seconds: Optional[int] = None
    enabled: Optional[bool] = None
    auto_pause_after: Optional[int] = None


def _principal(request: Request):
    return identity.request_principal(request)


def _require_write(request: Request, workspace: str) -> None:
    identity.require_role(_principal(request), workspace=workspace, role=WS_EDITOR)


def _raise(exc: service.WatcherError):
    raise HTTPException(status_code=getattr(exc, "status", 400), detail=str(exc))


def _load(request: Request, watcher_id: str):
    try:
        watcher = service.require(watcher_id)
    except service.WatcherError as exc:
        _raise(exc)
    access.require_visible(_principal(request), watcher.workspace)
    return watcher


def _audit(request: Request, action: str, watcher: Any, details: Optional[Dict[str, Any]] = None) -> None:
    try:
        audit.record(action, principal=_principal(request), object_type="watcher", object_id=watcher.id,
                     workspace=watcher.workspace, ip=identity.client_ip(request),
                     details={"name": watcher.name, "kind": watcher.kind, **(details or {})})
    except Exception:  # noqa: BLE001 - an audit failure must not undo the change
        log.warning("could not record the %s audit entry for %s", action, watcher.id, exc_info=True)


@router.get("/kinds")
async def list_kinds():
    """The kinds and their config fields, for the form."""
    return {
        "kinds": [{"kind": k, "fields": kinds.CONFIG_FIELDS[k]} for k in KINDS],
        "interval": {"min": MIN_INTERVAL_SECONDS, "max": MAX_INTERVAL_SECONDS, "default": DEFAULT_INTERVAL_SECONDS},
    }


@router.get("/summary")
async def watchers_summary(request: Request, workspace: Optional[str] = None):
    if workspace:
        access.require_visible(_principal(request), workspace)
    body = service.summary(workspace or None)
    body["watchers"] = access.filter_by_workspace(_principal(request), body["watchers"])
    return body


@router.get("")
async def list_watchers(request: Request, workspace: Optional[str] = None):
    if workspace:
        access.require_visible(_principal(request), workspace)
    rows = [service.to_dict(w, with_listeners=True) for w in service.list_watchers(workspace or None)]
    return access.filter_by_workspace(_principal(request), rows)


@router.post("", status_code=201)
async def create_watcher(request: Request, payload: WatcherCreate):
    _require_write(request, payload.workspace)
    principal = _principal(request)
    try:
        watcher = service.create(payload.workspace, payload.model_dump(exclude={"workspace"}),
                                 created_by=getattr(principal, "username", None))
    except service.WatcherError as exc:
        _raise(exc)
    _audit(request, "watcher.create", watcher)
    return service.to_dict(watcher, with_listeners=True)


@router.get("/{watcher_id}")
async def get_watcher(request: Request, watcher_id: str):
    return service.to_dict(_load(request, watcher_id), with_listeners=True)


@router.patch("/{watcher_id}")
async def update_watcher(request: Request, watcher_id: str, payload: WatcherUpdate):
    current = _load(request, watcher_id)
    _require_write(request, current.workspace)
    patch = payload.model_dump(exclude_unset=True)
    try:
        watcher = service.update(watcher_id, patch)
    except service.WatcherError as exc:
        _raise(exc)
    _audit(request, "watcher.update", watcher, {"changed": sorted(patch)})
    return service.to_dict(watcher, with_listeners=True)


@router.delete("/{watcher_id}")
async def delete_watcher(request: Request, watcher_id: str):
    current = _load(request, watcher_id)
    _require_write(request, current.workspace)
    service.delete(watcher_id)
    _audit(request, "watcher.delete", current)
    return {"deleted": True}


@router.post("/{watcher_id}/pause")
async def pause_watcher(request: Request, watcher_id: str):
    current = _load(request, watcher_id)
    _require_write(request, current.workspace)
    watcher = service.pause(watcher_id)
    _audit(request, "watcher.pause", watcher)
    return service.to_dict(watcher, with_listeners=True)


@router.post("/{watcher_id}/resume")
async def resume_watcher(request: Request, watcher_id: str):
    current = _load(request, watcher_id)
    _require_write(request, current.workspace)
    watcher = service.resume(watcher_id)
    _audit(request, "watcher.resume", watcher)
    return service.to_dict(watcher, with_listeners=True)


@router.post("/{watcher_id}/probe")
async def probe_watcher(request: Request, watcher_id: str, dry_run: bool = True):
    """Look at the source now. ``dry_run`` (the default, the form's test
    button) reports without storing state or waking anybody; ``dry_run=false``
    is a real poll ahead of schedule."""
    current = _load(request, watcher_id)
    _require_write(request, current.workspace)
    result = service.probe_once(watcher_id, dry_run=dry_run)
    result["watcher"] = service.to_dict(service.require(watcher_id), with_listeners=True)
    return result
