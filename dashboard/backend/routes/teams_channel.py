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

A workspace's own bot (docs/connectors.md "Connectors per workspace") has
its own messaging endpoint, ``/api/channels/teams/messages?workspace=<name>``
(the URL the Connectors page shows for it): the activity is verified with
that workspace's app id and handled by that workspace's bot, which serves
that workspace only. Without ``?workspace=`` it is the default's bot.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

from connectors.teams import auth

log = logging.getLogger("routes.teams_channel")

router = APIRouter(prefix="/api/channels/teams", tags=["teams"])


def _spec():
    from connectors.teams import SPEC
    return SPEC


def _service(workspace: Optional[str]):
    """The bot an inbound activity is for: the default's, or the bot a
    workspace defines for itself. 404 for a workspace with no bot of its own."""
    from connectors.channels import registry
    from connectors.channels.store import DEFAULT_WORKSPACE
    from common.workspace_context import normalize_workspace_name

    ws = normalize_workspace_name(workspace or "") or DEFAULT_WORKSPACE
    if ws == DEFAULT_WORKSPACE:
        return _spec().service
    svc = registry.service_for("teams", ws) if _spec().store.defines(ws) else None
    if svc is None:
        raise HTTPException(status_code=404, detail="No Teams bot for this workspace")
    return svc


async def _handle_activity_safely(activity: dict, service=None) -> None:
    svc: Any = service or _spec().service
    try:
        with svc.scope():
            await svc.handle_activity(activity)
    except Exception:  # noqa: BLE001 - logged, the ack already went out
        log.warning("teams webhook activity handling failed", exc_info=True)


@router.post("/messages")
async def teams_messages(request: Request, workspace: Optional[str] = None):
    svc = _service(workspace)
    app_id = svc.store.get("app_id")
    app_password = svc.store.get("app_password")
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

    asyncio.create_task(_handle_activity_safely(data, svc))
    return {}


__all__ = ["router"]
