"""
Google OAuth flow: the three endpoints a generic credentials form cannot
cover on its own (``dashboard/backend/routes/connectors.py`` already serves
``GET/PUT /api/connectors/google/config`` and ``POST .../test`` for the
plain fields).

- GET  /api/google/oauth/start      — redirect to Google's consent screen;
                                      ``?gmail=1`` also asks for the Gmail
                                      scope (IMAP and SMTP over XOAUTH2)
- GET  /api/google/oauth/callback   — exchange the code, store the refresh
                                      token and the connected account's
                                      address, redirect back to the UI
- POST /api/google/oauth/disconnect — clear the stored refresh token
- GET  /api/google/gmail/status     — connected, Gmail granted, as whom: what
                                      the mail forms (watchers, the mail
                                      channel) show next to "Sign in with
                                      Google"

Every route works on one workspace's Google connector (``?workspace=``, the
default workspace when omitted; connectors/channels/store.py). Connecting an
account defines the connector in that workspace, so a workspace other than
the default first saves its own OAuth client (``PUT
/api/connectors/google/config?workspace=``); one that inherits the default's
connector cannot connect or disconnect the default's account from inside
itself. The state nonce remembers the workspace, so the callback, which
Google calls with no workspace of its own, writes the refresh token to the
document the flow was started for. ``/gmail/status`` answers for the
connector in effect there, inherited or not.

The redirect URI is derived from the request (or ``AGENTS_HUB_PUBLIC_URL``
when set, for a backend behind a reverse proxy) rather than configured
separately, so the operator only ever has to paste that one URL into
Google's own OAuth client setup.
"""
from __future__ import annotations

import logging
import os
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from common import identity
from common.auth import WS_EDITOR
from common.session_broker import notify_change
from connectors.channels.store import DEFAULT_WORKSPACE, in_workspace
from connectors.google import STORE
from connectors.google.auth import (
    GoogleError,
    build_auth_url,
    exchange_code,
    fetch_userinfo,
    gmail_status,
    new_state,
    pop_state,
    reset_cache,
)

log = logging.getLogger("routes.google")

router = APIRouter(prefix="/api/google", tags=["google"])


def _redirect_uri(request: Request) -> str:
    public = os.environ.get("AGENTS_HUB_PUBLIC_URL", "").strip()
    base = public.rstrip("/") if public else str(request.base_url).rstrip("/")
    return f"{base}/api/google/oauth/callback"


def _ws(workspace: Optional[str]) -> str:
    """The workspace named by ``?workspace=`` (the default when empty); 404
    for one that does not exist."""
    from common.workspace_context import normalize_workspace_name
    ws = normalize_workspace_name(workspace or "") or DEFAULT_WORKSPACE
    if ws != DEFAULT_WORKSPACE:
        from workspace import get_workspace_folder
        if get_workspace_folder(ws) is None:
            raise HTTPException(status_code=404, detail=f"Workspace '{ws}' does not exist")
    return ws


def _own_store(ws: str):
    """``ws``'s own Google connector document. A workspace that inherits the
    default's has none to connect or disconnect: changing the default's
    account belongs to the default workspace."""
    if ws != DEFAULT_WORKSPACE and not STORE.defines(ws):
        raise HTTPException(
            status_code=400,
            detail=f"Workspace '{ws}' uses the default workspace's Google connector. Save an OAuth "
                   "client id and secret for this workspace first, or connect from the default "
                   "workspace.",
        )
    return STORE.for_workspace(ws)


def _connectors_page(ws: str) -> str:
    url = "/connectors?tab=google"
    return url if ws == DEFAULT_WORKSPACE else f"{url}&workspace={quote(ws)}"


@router.get("/oauth/start")
async def oauth_start(request: Request, gmail: bool = False, workspace: Optional[str] = None):
    ws = _ws(workspace)
    # A GET, so the middleware asks only for membership; connecting an
    # account changes the workspace's connector, an editor's business.
    identity.require_role(identity.request_principal(request), workspace=ws, role=WS_EDITOR)
    store = _own_store(ws)
    if not store.get("client_id") or not store.get("client_secret"):
        raise HTTPException(
            status_code=400,
            detail="Set the Google OAuth client id and client secret on the Connectors page first",
        )
    state = new_state(ws)
    try:
        url = build_auth_url(_redirect_uri(request), state, gmail=gmail, workspace=ws)
    except GoogleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url)


@router.get("/oauth/callback")
async def oauth_callback(request: Request, code: str = "", state: str = ""):
    ws = pop_state(state)
    if ws is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    if not code:
        raise HTTPException(status_code=400, detail="Google did not return an authorization code")
    if ws != DEFAULT_WORKSPACE and not STORE.defines(ws):
        # The workspace dropped its own connector while the person was on
        # Google's consent screen: nothing of its own to store the grant in.
        raise HTTPException(status_code=400, detail=f"Workspace '{ws}' no longer defines a Google "
                                                    "connector of its own")
    try:
        # In the workspace, so the exchange uses its OAuth client (the one
        # the consent screen was opened with).
        tokens = in_workspace(ws, exchange_code, code, _redirect_uri(request))
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            raise GoogleError(
                "Google did not return a refresh token. Disconnect and reconnect, "
                "approving offline access when Google asks."
            )
        userinfo = fetch_userinfo(str(tokens.get("access_token") or ""))
    except GoogleError as exc:
        log.warning("google oauth callback failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    STORE.for_workspace(ws).set_config({
        "refresh_token": refresh_token,
        "account_email": userinfo.get("email") or "",
        "granted_scopes": str(tokens.get("scope") or ""),
    })
    reset_cache()
    notify_change("connector_google", workspace=ws)
    return RedirectResponse(url=_connectors_page(ws))


@router.post("/oauth/disconnect")
async def oauth_disconnect(workspace: Optional[str] = None):
    ws = _ws(workspace)
    _own_store(ws).set_config({}, clear=("refresh_token", "account_email", "granted_scopes"))
    reset_cache()
    notify_change("connector_google", workspace=ws)
    return {"ok": True}


@router.get("/gmail/status")
async def gmail_status_route(workspace: Optional[str] = None):
    """The Google connector in effect in ``?workspace=`` (its own or the
    default's): connected, Gmail granted, as whom."""
    return gmail_status(_ws(workspace))


__all__ = ["router"]
