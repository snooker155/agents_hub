"""
Health & liveness endpoint.

``GET /api/health`` reports whether the database is reachable, the row counts of
the core stores, the liveness of every background service (plan scheduler, run
watchdog, external-state publisher), and on-disk state sizes.
Gives operators one place to see that the moving parts are alive and how large
local state has grown, instead of inferring it from logs.

The snapshot itself lives in ``common.health``.

``GET /api/health/doctor`` judges that snapshot (common/doctor.py): the same
checks ``ah doctor`` reports.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from common.health import snapshot

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health(request: Request):
    """Liveness + state snapshot. Never raises: a failed probe is reported as such.
    Run in a worker thread: the probes query the database and stat files, and
    the page calls this on every load, so the event loop must keep serving."""
    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(snapshot, request.app.state)


@router.get("/health/doctor")
async def doctor(request: Request):
    """The doctor's checks over the snapshot (common/doctor.py): each ok, warn,
    fail or skip with a summary and a docs anchor, plus the overall status.
    Run in a worker thread: some checks probe the network with a timeout, and
    the event loop must keep serving meanwhile. Never raises."""
    from starlette.concurrency import run_in_threadpool
    from common.doctor import run_doctor
    return await run_in_threadpool(run_doctor, request.app.state)
