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

The redirect URI is derived from the request (or ``AGENTS_HUB_PUBLIC_URL``
when set, for a backend behind a reverse proxy) rather than configured
separately, so the operator only ever has to paste that one URL into
Google's own OAuth client setup.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from common.session_broker import notify_change
from connectors.google import STORE
from connectors.google.auth import (
    GoogleError,
    build_auth_url,
    consume_state,
    exchange_code,
    fetch_userinfo,
    gmail_status,
    new_state,
    reset_cache,
)

log = logging.getLogger("routes.google")

router = APIRouter(prefix="/api/google", tags=["google"])


def _redirect_uri(request: Request) -> str:
    public = os.environ.get("AGENTS_HUB_PUBLIC_URL", "").strip()
    base = public.rstrip("/") if public else str(request.base_url).rstrip("/")
    return f"{base}/api/google/oauth/callback"


@router.get("/oauth/start")
async def oauth_start(request: Request, gmail: bool = False):
    client_id = STORE.get("client_id")
    client_secret = STORE.get("client_secret")
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail="Set the Google OAuth client id and client secret on the Connectors page first",
        )
    state = new_state()
    try:
        url = build_auth_url(_redirect_uri(request), state, gmail=gmail)
    except GoogleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url)


@router.get("/oauth/callback")
async def oauth_callback(request: Request, code: str = "", state: str = ""):
    if not consume_state(state):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    if not code:
        raise HTTPException(status_code=400, detail="Google did not return an authorization code")
    try:
        tokens = exchange_code(code, _redirect_uri(request))
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

    STORE.set_config({
        "refresh_token": refresh_token,
        "account_email": userinfo.get("email") or "",
        "granted_scopes": str(tokens.get("scope") or ""),
    })
    reset_cache()
    notify_change("connector_google")
    return RedirectResponse(url="/connectors?tab=google")


@router.post("/oauth/disconnect")
async def oauth_disconnect():
    STORE.set_config({}, clear=("refresh_token", "account_email", "granted_scopes"))
    reset_cache()
    notify_change("connector_google")
    return {"ok": True}


@router.get("/gmail/status")
async def gmail_status_route():
    return gmail_status()


__all__ = ["router"]
