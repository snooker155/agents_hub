"""
Credential connectors API, one set of routes for every registered one.

Endpoints (``<name>`` is jira, linear, google, microsoft, notion, confluence
or databases), each for one workspace (``?workspace=``, the default workspace
when omitted):
- GET    /api/connectors                 — the connectors and their config fields
- GET    /api/connectors/<name>/config   — the config in effect there (secrets as
                                           has_* flags), whether the workspace
                                           defines it or inherits the default's,
                                           configured flag, extra state
- PUT    /api/connectors/<name>/config   — set fields / clear secrets; on a
                                           workspace other than the default this
                                           defines the connector there
- DELETE /api/connectors/<name>/config   — drop a workspace's own definition, so
                                           it inherits the default's again
- POST   /api/connectors/<name>/test     — verify the credentials in effect there

A connector lives in the workspace that defines it, and the default
workspace's live everywhere (connectors/channels/store.py). The middleware
reads ``?workspace=`` like any other request: reading needs membership,
changing needs an editor of that workspace.

Connector-specific routes (a Google OAuth flow, database connections, a
project's tracker) live in their own route modules.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from common.session_broker import notify_change
from connectors import credentials
from connectors.channels.store import DEFAULT_WORKSPACE, in_workspace


router = APIRouter(prefix="/api/connectors", tags=["connectors"])


class ConnectorConfigUpdate(BaseModel):
    config: Optional[dict[str, Any]] = None
    clear: Optional[list[str]] = None


def _spec(name: str):
    spec = credentials.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown connector: {name!r}")
    return spec


def _ws(workspace: Optional[str]) -> str:
    from common.workspace_context import normalize_workspace_name
    ws = normalize_workspace_name(workspace or "") or DEFAULT_WORKSPACE
    if ws != DEFAULT_WORKSPACE:
        from workspace import get_workspace_folder
        if get_workspace_folder(ws) is None:
            raise HTTPException(status_code=404, detail=f"Workspace '{ws}' does not exist")
    return ws


def _payload(spec, ws: str) -> dict[str, Any]:
    defined = spec.store.defines(ws)
    out = in_workspace(ws, lambda: {
        "name": spec.name,
        "workspace": ws,
        # "here": this workspace defines it; "default": inherited.
        "source": "here" if defined else DEFAULT_WORKSPACE,
        "defined_in": [DEFAULT_WORKSPACE, *spec.store.defined_workspaces()]
        if ws == DEFAULT_WORKSPACE else None,
        "config": spec.store.public_config(),
        "configured": spec.is_configured(),
    })
    if out["defined_in"] is None:
        out.pop("defined_in")
    if spec.extra is not None:
        try:
            out["extra"] = in_workspace(ws, spec.extra) or {}
        except Exception as exc:  # noqa: BLE001 - extra state is informational
            out["extra"] = {"error": str(exc)[:200]}
    return out


@router.get("")
async def list_connectors():
    return [s.to_dict() for s in credentials.all_specs()]


@router.get("/{name}/config")
async def get_config(name: str, workspace: Optional[str] = None):
    return _payload(_spec(name), _ws(workspace))


@router.put("/{name}/config")
async def update_config(name: str, data: ConnectorConfigUpdate, workspace: Optional[str] = None):
    spec = _spec(name)
    ws = _ws(workspace)
    known = {f.key for f in spec.fields}
    unknown = [k for k in (data.config or {}) if k not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown config fields: {', '.join(unknown)}")
    spec.store.for_workspace(ws).set_config(data.config or {}, clear=data.clear or [])
    notify_change(f"connector_{spec.name}", workspace=ws)
    return _payload(spec, ws)


@router.delete("/{name}/config")
async def remove_config(name: str, workspace: Optional[str] = None):
    """The workspace stops defining the connector and inherits the default's."""
    spec = _spec(name)
    ws = _ws(workspace)
    if ws == DEFAULT_WORKSPACE:
        raise HTTPException(status_code=400, detail="The default workspace's connector is cleared "
                                                    "field by field, not removed")
    spec.store.remove_workspace(ws)
    notify_change(f"connector_{spec.name}", workspace=ws)
    return _payload(spec, ws)


@router.post("/{name}/test")
async def test_connector(name: str, workspace: Optional[str] = None):
    spec = _spec(name)
    ws = _ws(workspace)
    if not in_workspace(ws, spec.is_configured):
        return {"ok": False, "error": "not configured"}
    if spec.test is None:
        return {"ok": True}
    try:
        return await asyncio.to_thread(in_workspace, ws, spec.test)
    except Exception as exc:  # noqa: BLE001 - reported to the UI
        return {"ok": False, "error": str(exc)[:300]}
