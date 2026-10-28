"""Catalog only: browse and install from the linked hub (hublink/catalog.py).

The person's own hub is the one that runs everything; the linked hub is a
library. Browsing needs the link and the ``catalog`` switch; installing needs
editor rights in the target workspace; the switch itself is the
administrator's (it decides whether a hub-wide link is used this way).

The link's key is hub-wide, so everything read through it is the linking
administrator's view of the remote. Agents, skills and published flows are
meant for anyone and are browsed by any signed-in account; teams, loops and
unpublished flows have no publishing flag there and are this hub's
administrators' only (``catalog.ADMIN_KINDS``). What is already installed here
(updates, diffs) is shown only for copies in workspaces the caller can see.

Route order matters: the fixed ``updates`` and ``settings`` paths are declared
before the ``/{kind}/...`` ones.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from common import access, identity
from common.auth import WS_EDITOR
from hublink import catalog

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


class SettingsBody(BaseModel):
    enabled: bool


class PlanBody(BaseModel):
    workspace: str = "default"
    new_id: Optional[str] = None


def _fail(exc: catalog.CatalogError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def _workspace(value: Optional[str]) -> str:
    """The workspace an install goes to, named before any check: an empty
    value means default, and a role check on "" would check nothing."""
    return (value or "").strip() or "default"


def _editor(request: Request, workspace: str) -> None:
    identity.require_role(identity.request_principal(request), workspace=_workspace(workspace), role=WS_EDITOR)


def _signed_in(request: Request) -> None:
    identity.require_role(identity.request_principal(request))


def _is_admin(request: Request) -> bool:
    """One operator outside ``multi`` is every role, as in require_role."""
    if identity.current_mode() != identity.MULTI:
        return True
    principal = identity.request_principal(request)
    return bool(principal and principal.is_admin)


def _can_see(request: Request):
    principal = identity.request_principal(request)
    return lambda ws: access.can_see_workspace(principal, ws)


@router.get("/settings")
async def get_settings(request: Request):
    from hublink import store
    doc = store.get()
    return {"linked": doc is not None, "enabled": bool(doc and doc.get("catalog")),
            "url": (doc or {}).get("url", "")}


@router.put("/settings")
async def put_settings(body: SettingsBody, request: Request):
    identity.require_role(identity.request_principal(request), admin=True)
    try:
        return catalog.set_enabled(body.enabled)
    except catalog.CatalogError as exc:
        raise _fail(exc)


@router.get("/updates")
async def get_updates(request: Request, workspace: Optional[str] = Query(None)):
    import asyncio
    _signed_in(request)
    try:
        return {"items": await asyncio.to_thread(catalog.updates, workspace, _can_see(request))}
    except catalog.CatalogError as exc:
        raise _fail(exc)


@router.get("/{kind}")
async def browse(kind: str, request: Request, q: str = ""):
    import asyncio
    _signed_in(request)
    try:
        return {"items": await asyncio.to_thread(lambda: catalog.browse(kind, q, admin=_is_admin(request)))}
    except catalog.CatalogError as exc:
        raise _fail(exc)


@router.get("/{kind}/{item_id}")
async def get_item(kind: str, item_id: str, request: Request):
    import asyncio
    _signed_in(request)
    try:
        return await asyncio.to_thread(lambda: catalog.item(kind, item_id, admin=_is_admin(request)))
    except catalog.CatalogError as exc:
        raise _fail(exc)


@router.post("/{kind}/{item_id}/plan")
async def plan(kind: str, item_id: str, body: PlanBody, request: Request):
    import asyncio
    ws = _workspace(body.workspace)
    # A plan names the local agents an install would reuse or update, so it
    # is for the people who could run that install.
    _editor(request, ws)
    try:
        return await asyncio.to_thread(lambda: catalog.plan_install(kind, item_id, ws, body.new_id,
                                                                    admin=_is_admin(request)))
    except catalog.CatalogError as exc:
        raise _fail(exc)


@router.post("/{kind}/{item_id}/install")
async def install(kind: str, item_id: str, body: PlanBody, request: Request):
    ws = _workspace(body.workspace)
    _editor(request, ws)
    try:
        return await catalog.install(kind, item_id, ws, body.new_id, request, admin=_is_admin(request))
    except catalog.CatalogError as exc:
        raise _fail(exc)


@router.get("/{kind}/{local_id}/diff")
async def get_diff(kind: str, local_id: str, request: Request):
    import asyncio
    _signed_in(request)
    rec = catalog.provenance(kind, local_id) if kind in catalog.KINDS else None
    # The diff carries the local copy in full (instructions, flow YAML), so it
    # is shown only where the copy itself could be read; an unseen one reads
    # as not installed rather than as forbidden, so ids do not leak either.
    if rec is not None and not _can_see(request)(catalog.item_workspace(rec)):
        raise HTTPException(status_code=404, detail="This item was not installed from the linked hub.")
    try:
        return await asyncio.to_thread(lambda: catalog.diff(kind, local_id, admin=_is_admin(request)))
    except catalog.CatalogError as exc:
        raise _fail(exc)


@router.post("/{kind}/{local_id}/update")
async def update(kind: str, local_id: str, request: Request):
    rec = catalog.provenance(kind, local_id) if kind in catalog.KINDS else None
    # Rights on the workspace that owns the copy now, which is also where the
    # update writes; the record's workspace is only the fallback for a copy
    # that is gone.
    _editor(request, catalog.owner_workspace(kind, local_id, (rec or {}).get("workspace")) if rec else "default")
    try:
        return await catalog.update(kind, local_id, request, admin=_is_admin(request))
    except catalog.CatalogError as exc:
        raise _fail(exc)
