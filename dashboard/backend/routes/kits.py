"""Industry agent kits over REST (``kits/``, docs/kits.md).

A kit is a ready set of agents for one line of work, declared as an
``ah apply`` bundle plus a manifest (``kits/<id>/kit.yaml``). Installing one
is applying its bundle against this workspace:

- ``GET /api/kits``: every kit's manifest, with each named connector or
  channel's configured status.
- ``GET /api/kits/{id}``: the manifest, the bundle's resources, and the plan
  (create / update / unchanged) for a given workspace, read only.
- ``POST /api/kits/{id}/install``: plans, and unless ``dry_run`` applies, the
  bundle into the workspace. Requires the editor role there.

The plan and apply run against the hub's own ASGI app in process, through
the same REST routes a remote caller would use (``declarative/``), *as the
calling principal*: the install forwards the request's own Authorization
header and cookies, so it can do nothing the caller could not already do by
calling those routes directly, and nothing here needs its own permission
model beyond "may this caller write this workspace". The lock that makes a
second install update instead of duplicate is kept per (kit, workspace)
under the state directory (``kits.lock_path``).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import kits
from common import access, identity
from common.auth import WS_EDITOR
from declarative import Lock, LockError, apply as declarative_apply, plan as declarative_plan
from declarative.errors import ValidationError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kits", tags=["kits"])


class InstallBody(BaseModel):
    workspace: Optional[str] = None
    dry_run: bool = False


def _connector_payload(kit: "kits.Kit", workspace: Optional[str] = None) -> Dict[str, Any]:
    def status(names: List[str]) -> List[Dict[str, Any]]:
        return [{"name": n, "configured": kits.connector_status(n, workspace)} for n in names]
    return {"required": status(kit.connectors_required), "optional": status(kit.connectors_optional)}


def _kit_or_404(kit_id: str) -> "kits.Kit":
    kit = kits.get_kit(kit_id)
    if kit is None:
        raise HTTPException(status_code=404, detail=f"Unknown kit '{kit_id}'")
    return kit


def _caller_request(http_request: Request):
    """A ``request(method, path, params=None, json=None)`` callable that
    drives this same app in process, carrying the calling principal's own
    Authorization header and cookies — the apply engine then does exactly
    what that caller could do over the real API, no more. Mirrors
    ``cli.backend.DirectBackend.request``; see its docstring for why a plain
    ``TestClient`` call (no lifespan) is the right tool here."""
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app

    client = TestClient(app)
    client.cookies.update(dict(http_request.cookies))
    headers: Dict[str, str] = {}
    auth = http_request.headers.get("authorization")
    if auth:
        headers["authorization"] = auth

    def request(method: str, path: str, *, params: Optional[dict] = None, json: Optional[dict] = None) -> Any:
        r = client.request(method, path, params=params, json=json, headers=headers)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:  # noqa: BLE001 - fall back to raw text when the body is not JSON
                detail = r.text
            raise RuntimeError(f"{r.status_code}: {detail}")
        return r.json() if r.content else None

    return request


def _bundle_summary(bundle) -> List[Dict[str, Any]]:
    return [{"kind": r.kind, "key": r.key} for r in bundle.resources]


@router.get("")
async def list_kits_route(workspace: Optional[str] = None):
    out = []
    for kit in kits.list_kits():
        payload = kit.to_dict()
        payload["connectors"] = _connector_payload(kit, workspace or "default")
        out.append(payload)
    return out


@router.get("/{kit_id}")
async def get_kit_route(kit_id: str, http_request: Request, workspace: Optional[str] = None):
    kit = _kit_or_404(kit_id)
    principal = identity.request_principal(http_request)
    if workspace:
        access.require_visible(principal, workspace)
    payload = kit.to_dict()
    payload["connectors"] = _connector_payload(kit, workspace or "default")

    def _run():
        bundle = kits.namespaced_bundle(kits.load_kit_bundle(kit), workspace)
        lock = Lock.load(kits.lock_path(kit.id, workspace))
        request_fn = _caller_request(http_request)
        return bundle, declarative_plan(bundle, request_fn, lock, workspace)

    try:
        bundle, p = await asyncio.to_thread(_run)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LockError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"could not read the hub: {exc}") from exc
    payload["resources"] = _bundle_summary(bundle)
    payload["plan"] = p.to_dict()
    return payload


@router.post("/{kit_id}/install")
async def install_kit_route(kit_id: str, data: InstallBody, http_request: Request):
    kit = _kit_or_404(kit_id)
    workspace = (data.workspace or "").strip() or None
    principal = identity.request_principal(http_request)
    if workspace:
        access.require_visible(principal, workspace)
    identity.require_role(principal, workspace=workspace or "default", role=WS_EDITOR)

    def _run():
        bundle = kits.namespaced_bundle(kits.load_kit_bundle(kit), workspace)
        lock = Lock.load(kits.lock_path(kit.id, workspace))
        request_fn = _caller_request(http_request)
        p = declarative_plan(bundle, request_fn, lock, workspace)
        result = None
        if p.ok and not data.dry_run:
            result = declarative_apply(p, request_fn, lock)
            lock.save()
        return p, result

    try:
        p, result = await asyncio.to_thread(_run)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LockError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"could not reach the hub: {exc}") from exc

    payload: Dict[str, Any] = {"plan": p.to_dict()}
    if result is not None:
        payload["result"] = result.to_dict()
        payload["agents"] = [c.hub_id for c in p.changes if c.kind == "agent" and c.hub_id]
    if not p.ok:
        raise HTTPException(status_code=400, detail={"plan": p.to_dict(), "message": p.explain()})
    if result is not None and not result.ok:
        raise HTTPException(status_code=502, detail=payload)
    return payload
