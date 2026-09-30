"""
Blender connector API.

Blender is an external resource like a git host: the operator points the hub at
one, and its availability is a fact about the machine rather than about the
code. This router is where that is configured and where the running engines are
visible.

Endpoints:
- GET    /api/blender/config          — config plus what this machine can do
- PUT    /api/blender/config          — binary path, mode, ceilings, on/off
- POST   /api/blender/test            — probe a binary (defaults to the configured one)
- GET    /api/blender/daemons         — every running engine, whichever process started it
- DELETE /api/blender/daemons/{key}   — stop one
- DELETE /api/blender/daemons         — stop all

The daemon list is read from the cross-process registry and each engine is asked
what it is doing, so an operator sees the engines agents started in their own
processes, not just the ones this one did.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_project_root = Path(__file__).resolve().parents[3]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from common.session_broker import notify_change
from connectors.blender import pool, store


router = APIRouter(prefix="/api/blender", tags=["blender"])


class BlenderConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    mode: Optional[str] = None
    binary_path: Optional[str] = None
    docker_image: Optional[str] = None
    max_daemons: Optional[int] = None
    idle_timeout_s: Optional[int] = None
    startup_timeout_s: Optional[int] = None
    command_timeout_s: Optional[int] = None


class BlenderTestRequest(BaseModel):
    binary_path: Optional[str] = None


@router.get("/config")
async def get_config():
    """Stored config, the discovered binary, and whether an engine can run."""
    config = store.public_config()
    return {**config, "availability": pool.availability()}


@router.put("/config")
async def update_config(payload: BlenderConfigUpdate):
    patch = payload.model_dump(exclude_none=True)
    if not patch:
        return await get_config()
    try:
        store.save(patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    notify_change("blender_daemons")
    return await get_config()


@router.post("/test")
async def test_binary(payload: BlenderTestRequest):
    """Run ``--version`` on a candidate binary. Never raises: a bad path is an
    answer, not a server error."""
    return await asyncio.to_thread(store.probe, payload.binary_path or "")


@router.get("/daemons")
async def list_daemons():
    # Probing every engine is a handful of socket round-trips, and a wedged one
    # costs the probe timeout, so keep it off the event loop.
    return await asyncio.to_thread(pool.info)


@router.delete("/daemons/{key}")
async def stop_daemon(key: str):
    # pool.stop already publishes the event (a daemon can also be stopped from
    # an agent's own process, not just this route), so this is belt-and-braces.
    stopped = await asyncio.to_thread(pool.stop, key)
    if not stopped:
        raise HTTPException(status_code=404, detail=f"no engine running for {key!r}")
    notify_change("blender_daemons", key=key)
    return {"ok": True, "stopped": key}


@router.delete("/daemons")
async def stop_all_daemons():
    stopped = await asyncio.to_thread(pool.stop_all)
    notify_change("blender_daemons")
    return {"ok": True, "stopped": stopped}
