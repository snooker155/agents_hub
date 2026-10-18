"""Liveness, readiness and metrics: the three endpoints a reverse proxy or a
scrape target calls, as opposed to an operator (that is ``/api/health``, see
``routes/health.py``).

Deliberately not under ``/api``: a request whose path does not start with
``/api`` is already open (``common.auth.is_open_path``), so these three
answer without a token whether or not one is configured, exactly as a
container health check or a Prometheus scraper needs.

``/livez`` never touches the database: it answers as long as the process can
run Python at all, which is the one thing a liveness probe is supposed to
prove ("should this container be restarted?"). ``/readyz`` is the "should
traffic be routed here?" question, and does touch the database: a process
that is alive but cannot reach its own state should not receive requests.
``/metrics`` is ``common.metrics.render()`` verbatim, so the collection logic
has exactly one home.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
import logging

log = logging.getLogger(__name__)

router = APIRouter(tags=["ops"])


@router.get("/livez")
async def livez() -> Dict[str, str]:
    """Whether the process answers at all. No database access: a liveness
    probe that can fail because the database is slow would restart a process
    that was never the problem."""
    return {"status": "alive"}


def _database_ok() -> bool:
    try:
        from common import db
        db.get_conn().execute("SELECT 1").fetchone()
        return True
    except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
        log.debug("_database_ok: falling back after a failure", exc_info=True)
        return False


@router.get("/readyz")
async def readyz() -> Response:
    """Whether this process can serve traffic right now: 200 when the
    database answers a trivial query, 503 otherwise, with a body naming the
    check so an operator does not have to guess from the status code."""
    db_ok = _database_ok()
    body: Dict[str, Any] = {
        "status": "ready" if db_ok else "not ready",
        "database": db_ok,
    }
    return JSONResponse(status_code=200 if db_ok else 503, content=body)


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus text exposition format. See common/metrics.py for the
    metrics themselves."""
    from common.metrics import render
    return Response(content=render(), media_type="text/plain; version=0.0.4")


__all__ = ["router"]
