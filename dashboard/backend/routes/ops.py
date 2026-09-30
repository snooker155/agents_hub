"""Liveness, readiness and metrics: the three endpoints a load balancer or a
scrape target calls, as opposed to an operator (that is ``/api/health``, see
``routes/health.py``).

Deliberately not under ``/api``: a request whose path does not start with
``/api`` is already open in every ``AUTH_MODE`` (``common.auth.is_open_path``),
so these three answer without a token whether or not one is configured,
exactly as a Kubernetes probe or a Prometheus scraper needs. Nothing here was
added to the identity guard's exemption list for that reason — there was
nothing to add.

``/livez`` never touches the database: it answers as long as the process can
run Python at all, which is the one thing a liveness probe is supposed to
prove ("should this container be restarted?"). ``/readyz`` is the "should
traffic be routed here?" question, and does touch the database (and, when
configured, the broker and blob store): a process that is alive but cannot
reach its own state should not receive requests. ``/metrics`` is
``common.metrics.render()`` verbatim, so the collection logic has exactly one
home.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

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
    except Exception:
        return False


def _broker_status() -> Any:
    """None when AGENTS_HUB_BROKER_URL is not set (nothing to check); True or
    False once it is, from the bridge's own connection state (common.broker_
    bridge.get_redis(), which is None whenever the bridge is off or was never
    able to connect)."""
    try:
        from common.config import settings
        if not (settings.broker_url or "").strip():
            return None
    except Exception:
        return None
    try:
        from common import broker_bridge
        return broker_bridge.get_redis() is not None
    except Exception:
        return False


def _blob_status() -> Any:
    """None when AGENTS_HUB_BLOB_URL is not set; "unknown" when common.blobs
    cannot be imported (another agent may be mid-edit on it, per the brief for
    this change — a broken import there must never fail readiness); True or
    False from common.blobs.configured() otherwise."""
    try:
        import os
        from common.config import settings
        url = (getattr(settings, "blob_url", "") or os.environ.get("AGENTS_HUB_BLOB_URL", "")).strip()
    except Exception:
        url = ""
    if not url:
        return None
    try:
        from common import blobs
    except Exception:
        return "unknown"
    try:
        return bool(blobs.configured())
    except Exception:
        return "unknown"


def _singleton_info() -> Dict[str, bool]:
    """Whether some process holds each singleton role's lease right now.
    Informational only — never fails readiness — because a brand new
    deployment has no holder for the first tick or two, and that is normal,
    not degraded."""
    roles = ["scheduler", "watchdog", "outbox"]
    try:
        from connectors.telegram import telegram_store
        if telegram_store.is_enabled() and telegram_store.has_token():
            roles.append("telegram")
    except Exception:
        pass
    current: Dict[str, bool] = {}
    try:
        from common import leases
        current = {str(rec["role"]): not rec.get("expired") for rec in leases.all_leases()}
    except Exception:
        pass
    return {role: bool(current.get(role, False)) for role in roles}


@router.get("/readyz")
async def readyz() -> Response:
    """Whether this process can serve traffic right now.

    200 when the database answers a trivial query and, whichever of the
    broker/blob checks apply, none of them reports False. 503 otherwise, with
    a body naming each check so an operator does not have to guess which one
    failed from the status code alone.
    """
    db_ok = _database_ok()
    broker = _broker_status()
    blob = _blob_status()

    ready = db_ok and broker is not False and blob is not False
    body: Dict[str, Any] = {
        "status": "ready" if ready else "not ready",
        "database": db_ok,
        "broker": broker,
        "blob": blob,
    }

    try:
        from common.config import hub_role
        role = hub_role()
    except Exception:
        role = "all"
    if role in ("all", "api"):
        # Informational, not part of `ready`: see _singleton_info.
        body["singletons"] = _singleton_info()

    return JSONResponse(status_code=200 if ready else 503, content=body)


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus text exposition format. See common/metrics.py for the
    metrics themselves."""
    from common.metrics import render
    return Response(content=render(), media_type="text/plain; version=0.0.4")


__all__ = ["router"]
