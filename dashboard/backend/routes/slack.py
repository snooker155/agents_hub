"""
Slack Events API inbound webhook.

Everything generic (config, allowlist, bindings, test, the debug send) is
``dashboard/backend/routes/channels.py``'s ``/api/channels/slack/...``
routes; this module only carries what Slack must POST to directly:

- ``POST /api/channels/slack/events``: the Events API callback. Answers
  ``url_verification`` with the challenge, otherwise hands the event to
  ``SlackService.handle_event`` on a background task so the 3-second ack
  deadline is never at the mercy of how long the agent run takes.
- ``POST /api/channels/slack/interactions``: Block Kit button presses,
  posted as a form field named ``payload`` rather than JSON.

Both verify ``X-Slack-Signature`` (HMAC SHA256 over ``v0:<timestamp>:<body>``
with the configured signing secret) before touching the body, and reject a
timestamp older than 5 minutes against replay.

A workspace's own bot (docs/connectors.md "Connectors per workspace") is
reached at the same paths with ``?workspace=<name>``: its signing secret
verifies the request and its service handles the event, serving that
workspace only. Without ``?workspace=`` it is the default's bot.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Optional
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request

log = logging.getLogger("routes.slack")

router = APIRouter(prefix="/api/channels/slack", tags=["slack"])

_MAX_SKEW_SECONDS = 300


def _spec():
    from connectors.slack import SPEC
    return SPEC


def _service(workspace: Optional[str]):
    """The bot an inbound request is for: the default's, or the bot a
    workspace defines for itself. 404 for a workspace with no bot of its own."""
    from connectors.channels import registry
    from connectors.channels.store import DEFAULT_WORKSPACE
    from common.workspace_context import normalize_workspace_name

    ws = normalize_workspace_name(workspace or "") or DEFAULT_WORKSPACE
    if ws == DEFAULT_WORKSPACE:
        return _spec().service
    svc = registry.service_for("slack", ws) if _spec().store.defines(ws) else None
    if svc is None:
        raise HTTPException(status_code=404, detail="No Slack bot for this workspace")
    return svc


def _verify(raw: bytes, headers: Any, service: Any = None) -> None:
    store = (service or _spec().service).store
    secret = store.get("signing_secret")
    if not secret:
        raise HTTPException(status_code=403, detail="Slack signing secret not configured")

    timestamp = headers.get("x-slack-request-timestamp")
    signature = headers.get("x-slack-signature")
    if not timestamp or not signature:
        raise HTTPException(status_code=403, detail="Missing Slack signature headers")
    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=403, detail="Bad Slack timestamp") from None
    if abs(time.time() - ts) > _MAX_SKEW_SECONDS:
        raise HTTPException(status_code=403, detail="Stale Slack timestamp")

    basestring = f"v0:{timestamp}:{raw.decode('utf-8', errors='replace')}".encode("utf-8")
    computed = "v0=" + hmac.new(secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, signature):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")


async def _handle_event_safely(event: dict[str, Any], event_id: str | None, service: Any = None) -> None:
    svc = service or _spec().service
    try:
        with svc.scope():
            await svc.handle_event(event, event_id=event_id)
    except Exception:  # noqa: BLE001 - logged, the ack already went out
        log.warning("slack webhook event handling failed", exc_info=True)


async def _handle_block_action_safely(payload: dict[str, Any], service: Any = None) -> None:
    svc = service or _spec().service
    try:
        with svc.scope():
            await svc.handle_block_action(payload)
    except Exception:  # noqa: BLE001 - logged, the ack already went out
        log.warning("slack webhook block action handling failed", exc_info=True)


@router.post("/events")
async def slack_events(request: Request, workspace: Optional[str] = None):
    svc = _service(workspace)
    raw = await request.body()
    _verify(raw, request.headers, svc)
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid JSON") from None

    if data.get("type") == "url_verification":
        return {"challenge": data.get("challenge")}

    if data.get("type") == "event_callback":
        event = data.get("event") or {}
        # Ack within Slack's 3-second budget; the run itself can take longer.
        asyncio.create_task(_handle_event_safely(event, data.get("event_id"), svc))

    return {"ok": True}


@router.post("/interactions")
async def slack_interactions(request: Request, workspace: Optional[str] = None):
    svc = _service(workspace)
    raw = await request.body()
    _verify(raw, request.headers, svc)
    form = parse_qs(raw.decode("utf-8"))
    payload_str = (form.get("payload") or [""])[0]
    if not payload_str:
        return {"ok": True}
    try:
        payload = json.loads(payload_str)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid payload") from None

    if payload.get("type") == "block_actions":
        asyncio.create_task(_handle_block_action_safely(payload, svc))

    return {"ok": True}


__all__ = ["router"]
