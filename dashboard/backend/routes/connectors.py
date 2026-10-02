"""
Credential connectors API, one set of routes for every registered one.

Endpoints (``<name>`` is jira, linear, google, microsoft, notion, confluence
or databases):
- GET  /api/connectors                 — the connectors and their config fields
- GET  /api/connectors/<name>/config   — public config (secrets as has_* flags),
                                         configured flag, extra state
- PUT  /api/connectors/<name>/config   — set fields / clear secrets
- POST /api/connectors/<name>/test     — verify the saved credentials

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


router = APIRouter(prefix="/api/connectors", tags=["connectors"])


class ConnectorConfigUpdate(BaseModel):
    config: Optional[dict[str, Any]] = None
    clear: Optional[list[str]] = None


def _spec(name: str):
    spec = credentials.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown connector: {name!r}")
    return spec


def _payload(spec) -> dict[str, Any]:
    out = {
        "name": spec.name,
        "config": spec.store.public_config(),
        "configured": spec.is_configured(),
    }
    if spec.extra is not None:
        try:
            out["extra"] = spec.extra() or {}
        except Exception as exc:  # noqa: BLE001 - extra state is informational
            out["extra"] = {"error": str(exc)[:200]}
    return out


@router.get("")
async def list_connectors():
    return [s.to_dict() for s in credentials.all_specs()]


@router.get("/{name}/config")
async def get_config(name: str):
    return _payload(_spec(name))


@router.put("/{name}/config")
async def update_config(name: str, data: ConnectorConfigUpdate):
    spec = _spec(name)
    known = {f.key for f in spec.fields}
    unknown = [k for k in (data.config or {}) if k not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown config fields: {', '.join(unknown)}")
    spec.store.set_config(data.config or {}, clear=data.clear or [])
    notify_change(f"connector_{spec.name}")
    return _payload(spec)


@router.post("/{name}/test")
async def test_connector(name: str):
    spec = _spec(name)
    if not spec.is_configured():
        return {"ok": False, "error": "not configured"}
    if spec.test is None:
        return {"ok": True}
    try:
        return await asyncio.to_thread(spec.test)
    except Exception as exc:  # noqa: BLE001 - reported to the UI
        return {"ok": False, "error": str(exc)[:300]}
