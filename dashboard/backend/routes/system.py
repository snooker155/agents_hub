"""
System workspace routes: the repository copy, the maintenance loop's schedule
and the branches the loop left behind. The logic lives in
``common/system_workspace.py``; these are thin wrappers so the Health page's
system section, the CLI and the tools all see the same state.

Reading is open to whoever reaches the API (the global auth middleware
applies). The three writes (sync, schedule, prune) change the copy or the
schedule of a loop that spends money, so under ``AUTH_MODE=multi`` they need
an administrator. Every route answers 404 when ``SYSTEM_WORKSPACE`` is off.

See docs/system-workspace.md.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/api/system", tags=["system"])


def _require_enabled() -> None:
    from common import system_workspace as sw
    if not sw.enabled():
        raise HTTPException(
            status_code=404,
            detail="The system workspace is turned off (SYSTEM_WORKSPACE=false).")


def _require_admin(request: Request) -> None:
    from common import identity
    identity.require_role(identity.request_principal(request), admin=True)


@router.get("")
async def system_status():
    """The copy, the loop and its schedule, and the ``system/`` branches."""
    _require_enabled()
    from common import system_workspace as sw
    return await run_in_threadpool(sw.status)


@router.post("/sync")
async def system_sync(request: Request):
    """Clone the repository into the copy, or fetch and fast forward it."""
    _require_enabled()
    _require_admin(request)
    from common import system_workspace as sw
    return await run_in_threadpool(sw.ensure_clone)


class ScheduleIn(BaseModel):
    enabled: bool
    every_hours: int = Field(6, ge=1, le=24)


@router.post("/schedule")
async def system_schedule(body: ScheduleIn, request: Request):
    """Turn the maintenance loop's schedule on or off and set its interval.
    Returns the loop section of the status."""
    _require_enabled()
    _require_admin(request)
    from common import system_workspace as sw
    try:
        return sw.set_schedule(body.enabled, body.every_hours)
    except (sw.SystemCopyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class PruneIn(BaseModel):
    older_than_days: int = Field(14, ge=1, le=3650)


@router.post("/prune")
async def system_prune(body: PruneIn, request: Request):
    """Delete ``system/`` branches of the copy older than the cutoff."""
    _require_enabled()
    _require_admin(request)
    from common import system_workspace as sw
    try:
        deleted = await run_in_threadpool(sw.prune_branches, body.older_than_days)
    except sw.SystemCopyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"deleted": deleted, "older_than_days": body.older_than_days}


@router.get("/branches")
async def system_branches():
    """The copy's ``system/`` branches, newest first, each with its fetch command."""
    _require_enabled()
    from common import system_workspace as sw

    def _rows():
        return [{**b, "fetch_command": sw.fetch_command(b["name"])} for b in sw.list_branches()]
    return await run_in_threadpool(_rows)
