"""
Microsoft Teams (Bot Framework) inbound webhook.

Everything generic (config, allowlist, bindings, test, the debug send) is
``dashboard/backend/routes/channels.py``'s ``/api/channels/teams/...``
routes; this module only carries what the Bot Framework must POST to
directly: ``POST /api/channels/teams/messages``, one activity per call.

Every activity carries a bearer JWT signed by the Bot Framework itself, not
by the bot's own app password (``connectors/teams/auth.py::verify_activity_token``).
A missing or bad token is a 401; a bot with no app_password saved yet is a
503, so Azure's retry does not spend its budget on a channel the operator
has not finished setting up. A verified activity is handed to
``TeamsService.handle_activity`` on a background task and the route answers
200 immediately: Bot Framework expects the ack well under its timeout, long
before an agent run could finish.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request

from connectors.teams import auth

log = logging.getLogger("routes.teams_channel")

router = APIRouter(prefix="/api/channels/teams", tags=["teams"])


def _spec():
    from connectors.teams import SPEC
    return SPEC


async def _handle_activity_safely(activity: dict) -> None:
    try:
        await _spec().service.handle_activity(activity)
    except Exception:  # noqa: BLE001 - logged, the ack already went out
        log.warning("teams webhook activity handling failed", exc_info=True)


@router.post("/messages")
async def teams_messages(request: Request):
    spec = _spec()
    app_id = spec.store.get("app_id")
    app_password = spec.store.get("app_password")
    if not app_password:
        raise HTTPException(status_code=503, detail="Teams channel is not configured")

    authz = request.headers.get("authorization") or ""
    token = authz[7:] if authz.lower().startswith("bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid JSON") from None

    try:
        auth.verify_activity_token(token, app_id, service_url=data.get("serviceUrl"))
    except auth.TeamsAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    asyncio.create_task(_handle_activity_safely(data))
    return {}


__all__ = ["router"]
