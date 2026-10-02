"""
Google Workspace authentication: turns whichever mode the operator configured
(``connectors/google/__init__.py``'s :data:`STORE`) into a bearer access
token, and carries the OAuth three-legged flow's pure logic (the consent URL,
the code exchange, the userinfo lookup, the state nonce) for
``dashboard/backend/routes/google.py`` to drive.

Mirrors ``connectors/git/providers.py`` in spirit: one place that turns
stored credentials into something an HTTP client can use, with errors mapped
to messages safe to show in the UI (:class:`GoogleError`). See
``connectors/google/client.py`` for the HTTP client that spends the token
this module hands out.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

#: The four Workspace surfaces this connector touches.
SCOPES = (
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/calendar",
)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

_REQUEST_TIMEOUT = 30.0
#: How long an OAuth state nonce is good for: the consent-screen round trip
#: is seconds long, not minutes, so this is generous, not tight.
_STATE_TTL_SECONDS = 600


class GoogleError(Exception):
    """Raised for configuration or token errors; the message is UI-safe."""


# ── OAuth state nonces ───────────────────────────────────────────────────────
# Kept in memory, not the database: the callback is a same-process round trip
# seconds after /oauth/start, so nothing here needs to survive a restart, and
# a dict keeps the dashboard backend from taking a database round trip on
# every step of a flow a person is actively watching.

_STATES: dict[str, float] = {}


def _prune_states() -> None:
    now = time.time()
    for s in [s for s, exp in _STATES.items() if exp < now]:
        _STATES.pop(s, None)


def new_state() -> str:
    """A fresh, single-use nonce for the consent screen's ``state`` param."""
    _prune_states()
    state = uuid.uuid4().hex
    _STATES[state] = time.time() + _STATE_TTL_SECONDS
    return state


def consume_state(state: str) -> bool:
    """True once, for a state issued within the last ten minutes. A missing,
    unknown or expired state (replay, a forged callback) returns False and
    leaves nothing behind to retry with."""
    _prune_states()
    return _STATES.pop(str(state or ""), None) is not None


# ── the three-legged flow ────────────────────────────────────────────────────

def build_auth_url(redirect_uri: str, state: str) -> str:
    """Google's consent screen URL for this operator's OAuth client.

    ``access_type=offline`` plus ``prompt=consent`` is what makes Google hand
    back a refresh token even for an account that has already approved this
    client once before.
    """
    from . import STORE

    client_id = STORE.get("client_id")
    if not client_id:
        raise GoogleError("No Google OAuth client id configured on the Connectors page")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str, redirect_uri: str) -> dict[str, Any]:
    """POST the authorization code for tokens; the parsed JSON body
    (``access_token``, ``refresh_token``, ``expires_in``, ...)."""
    from . import STORE

    client_id = STORE.get("client_id")
    client_secret = STORE.get("client_secret")
    if not client_id or not client_secret:
        raise GoogleError("No Google OAuth client configured on the Connectors page")
    try:
        resp = httpx.post(TOKEN_URL, data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=_REQUEST_TIMEOUT)
    except httpx.HTTPError as exc:
        raise GoogleError(f"Google token exchange failed: {exc.__class__.__name__}") from exc
    if resp.status_code >= 400:
        raise GoogleError(f"Google token exchange failed ({resp.status_code})")
    return resp.json()


def fetch_userinfo(access_token: str) -> dict[str, Any]:
    """The connected account's profile (used for its ``email``)."""
    try:
        resp = httpx.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"},
                         timeout=_REQUEST_TIMEOUT)
    except httpx.HTTPError as exc:
        raise GoogleError(f"Google userinfo request failed: {exc.__class__.__name__}") from exc
    if resp.status_code >= 400:
        raise GoogleError(f"Google userinfo request failed ({resp.status_code})")
    return resp.json()


# ── access tokens ────────────────────────────────────────────────────────────
# One process-wide cache: every caller in this backend wants the same token
# for the same stored credentials, and refreshing on every tool call would
# mean a token round trip per Drive search. Cleared on disconnect
# (dashboard/backend/routes/google.py) and by reset_cache() in tests.

_cached_token: Optional[str] = None
_cached_expiry: float = 0.0


def _refresh_oauth_token() -> tuple[str, float]:
    from . import STORE

    client_id = STORE.get("client_id")
    client_secret = STORE.get("client_secret")
    refresh_token = STORE.get("refresh_token")
    if not (client_id and client_secret and refresh_token):
        raise GoogleError("Google OAuth is not connected. Click Connect Google on the Connectors page.")
    try:
        resp = httpx.post(TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }, timeout=_REQUEST_TIMEOUT)
    except httpx.HTTPError as exc:
        raise GoogleError(f"Google token refresh failed: {exc.__class__.__name__}") from exc
    if resp.status_code in (400, 401):
        raise GoogleError("Google token expired or was revoked. Reconnect it on the Connectors page.")
    if resp.status_code >= 400:
        raise GoogleError(f"Google token refresh failed ({resp.status_code})")
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise GoogleError("Google token refresh returned no access token")
    expires_in = float(data.get("expires_in") or 3600)
    return str(token), time.time() + expires_in - 60


def _service_account_token() -> tuple[str, float]:
    import json as _json

    from . import STORE

    raw = STORE.get("service_account_json")
    if not raw:
        raise GoogleError("No Google service account JSON configured")
    try:
        info = _json.loads(raw)
    except Exception as exc:
        raise GoogleError("The Google service account JSON is not valid JSON") from exc
    try:
        from google.auth.transport.requests import Request as GoogleAuthRequest
        from google.oauth2.service_account import Credentials as ServiceAccountCredentials
    except ImportError as exc:
        raise GoogleError("google-auth is not installed") from exc
    try:
        creds = ServiceAccountCredentials.from_service_account_info(info, scopes=list(SCOPES))
        creds.refresh(GoogleAuthRequest())
    except Exception as exc:
        raise GoogleError(f"Google service account authentication failed: {exc}") from exc
    if not creds.token:
        raise GoogleError("Google service account authentication returned no token")
    expiry = creds.expiry.timestamp() if creds.expiry else time.time() + 3600
    return str(creds.token), expiry - 60


def get_access_token() -> str:
    """A bearer token for whichever mode is configured, cached in memory
    until shortly before it expires. Raises :class:`GoogleError` with a
    UI-safe message when unconfigured or the grant fails."""
    global _cached_token, _cached_expiry
    if _cached_token and time.time() < _cached_expiry:
        return _cached_token
    from . import STORE

    if str(STORE.get("service_account_json") or "").strip():
        token, expiry = _service_account_token()
    else:
        token, expiry = _refresh_oauth_token()
    _cached_token = token
    _cached_expiry = expiry
    return token


def reset_cache() -> None:
    """Drop the cached access token: after a disconnect/reconnect, and
    between tests."""
    global _cached_token, _cached_expiry
    _cached_token = None
    _cached_expiry = 0.0


__all__ = [
    "GoogleError", "SCOPES", "new_state", "consume_state", "build_auth_url",
    "exchange_code", "fetch_userinfo", "get_access_token", "reset_cache",
]
