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

Distribution over OAuth (docs/distribution.md) adds two routes Slack's
browsers reach without a hub credential:

- ``GET /api/channels/slack/install``: the public "Direct install URL" a
  Slack Marketplace listing points at. Only when the app's ``distribution``
  is ``public``; the team it installs into waits as a pending installation.
- ``GET /api/channels/slack/oauth``: the OAuth redirect. The ``state`` is
  signed (``common.signed_state``), short lived and names the bot, so nobody
  can graft an installation onto a hub that did not start it. An install the
  operator started from the Distribution page
  (``POST /api/distribution/slack/install-link``) is approved on arrival.
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


async def _handle_event_safely(event: dict[str, Any], event_id: str | None, service: Any = None,
                               team_id: str | None = None) -> None:
    svc = service or _spec().service
    try:
        with svc.scope():
            await svc.handle_event(event, event_id=event_id, team_id=team_id)
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
        asyncio.create_task(_handle_event_safely(event, data.get("event_id"), svc,
                                                 team_id=data.get("team_id")))

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


# ── distribution over OAuth ──────────────────────────────────────────────────

INSTALL_PURPOSE = "slack-install"
INSTALL_TTL_SECONDS = 900
OAUTH_PATH = "/api/channels/slack/oauth"

#: What the bot needs: hear mentions and direct messages, read the channels
#: it is in, answer, and fetch the files people send it.
BOT_SCOPES = ("app_mentions:read", "channels:history", "chat:write", "files:read",
              "groups:history", "im:history", "im:read", "im:write", "mpim:history",
              "users:read")
BOT_EVENTS = ("app_mention", "message.channels", "message.groups", "message.im",
              "message.mpim", "app_uninstalled", "tokens_revoked")


def public_base(request: Request) -> str:
    from common import hub_urls
    from common.signed_state import public_url
    return hub_urls.public_base() or public_url(request)


def authorize_url(request: Request, *, bot_workspace: str, approve: bool,
                  target_workspace: Optional[str] = None, agent_id: str = "",
                  started_by: Optional[str] = None) -> str:
    """Slack's consent screen for installing this bot, with a signed state
    that brings the browser back to this bot and says what to do on arrival."""
    from urllib.parse import urlencode
    from common.signed_state import sign_state
    svc = _service(bot_workspace)
    client_id = svc.store.get("client_id")
    if not client_id or not svc.store.get("client_secret"):
        raise HTTPException(status_code=409, detail="Set the Slack app's client ID and secret first")
    state = sign_state({"purpose": INSTALL_PURPOSE, "bot": bot_workspace, "approve": approve,
                        "ws": target_workspace, "agent": agent_id, "by": started_by,
                        "exp": time.time() + INSTALL_TTL_SECONDS})
    return "https://slack.com/oauth/v2/authorize?" + urlencode({
        "client_id": client_id, "scope": ",".join(BOT_SCOPES),
        "redirect_uri": public_base(request) + OAUTH_PATH, "state": state})


@router.get("/install")
async def slack_public_install(request: Request, workspace: Optional[str] = None):
    from fastapi.responses import RedirectResponse
    svc = _service(workspace)
    if str(svc.store.get("distribution") or "private") != "public":
        raise HTTPException(status_code=404, detail="This Slack app is not offered for installation")
    return RedirectResponse(authorize_url(request, bot_workspace=svc.workspace, approve=False),
                            status_code=302)


def _page(title: str, body: str, status: int = 200):
    from html import escape
    from fastapi.responses import HTMLResponse
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        f"<title>{escape(title)}</title>"
        "<body style='font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem'>"
        f"<h1 style='font-size:1.4rem'>{escape(title)}</h1><p>{escape(body)}</p></body>",
        status_code=status)


async def exchange_code(client_id: str, client_secret: str, code: str,
                        redirect_uri: str) -> dict[str, Any]:
    """``oauth.v2.access``: the installation's bot token and team."""
    import httpx
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post("https://slack.com/api/oauth.v2.access", data={
            "client_id": client_id, "client_secret": client_secret, "code": code,
            "redirect_uri": redirect_uri})
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(str(body.get("error") or "oauth.v2.access failed"))
    return body


@router.get("/oauth")
async def slack_oauth(request: Request, code: Optional[str] = None, state: Optional[str] = None,
                      error: Optional[str] = None):
    from fastapi.responses import RedirectResponse
    from common.signed_state import StateError, read_state
    if error:
        return _page("Slack installation cancelled", f"Slack said: {error}.", status=400)
    try:
        payload = read_state(state)
    except StateError:
        return _page("Installation link expired",
                     "Start the installation again from where you found it.", status=400)
    if payload.get("purpose") != INSTALL_PURPOSE or not code:
        return _page("Not a Slack installation", "This link does not finish an installation.",
                     status=400)
    try:
        svc = _service(payload.get("bot"))
    except HTTPException:
        return _page("Bot not found", "The bot this installation was for no longer exists.",
                     status=404)
    try:
        body = await exchange_code(svc.store.get("client_id"), svc.store.get("client_secret"),
                                   code, public_base(request) + OAUTH_PATH)
    except Exception as exc:  # noqa: BLE001 - shown to the installer, logged for the operator
        log.warning("slack oauth exchange failed: %s", exc)
        return _page("Slack installation failed", f"Slack refused the installation: {exc}.",
                     status=502)
    team = body.get("team") or {}
    enterprise = body.get("enterprise") or {}
    org_id = team.get("id") or enterprise.get("id")
    if not org_id or not body.get("access_token"):
        return _page("Slack installation failed", "Slack returned no team or token.", status=502)
    approve = bool(payload.get("approve"))
    fields: dict[str, Any] = {
        "name": team.get("name") or enterprise.get("name") or org_id,
        "bot_token": body.get("access_token"), "bot_user_id": body.get("bot_user_id"),
        "app_id": body.get("app_id"), "scope": body.get("scope"),
        "enterprise_id": enterprise.get("id"),
        "via": "hub" if approve else "catalog",
    }
    if approve:
        from datetime import datetime, timezone
        fields.update(status="approved", workspace=svc.own_workspace or payload.get("ws"),
                      agent_id=payload.get("agent") or "", approved_by=payload.get("by"),
                      approved_at=datetime.now(timezone.utc).isoformat())
    install = svc.store.upsert_install(org_id, **fields)
    try:
        from common.session_broker import notify_change
        notify_change("channel_slack", workspace=svc.workspace)
    except Exception:  # noqa: BLE001 - the page refreshes on its own anyway
        pass
    if approve:
        return RedirectResponse(f"/distribution?installed=slack&org={org_id}", status_code=302)
    if install.get("status") == "approved":
        return _page("Agents Hub is installed", "You can talk to the bot in Slack now.")
    return _page("Agents Hub is installed",
                 "The hub's operator still has to approve your Slack workspace. "
                 "The bot will answer once they do.")


__all__ = ["router", "BOT_SCOPES", "BOT_EVENTS", "authorize_url", "public_base"]
