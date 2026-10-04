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

Every Google connector lives in a workspace (connectors/channels/store.py):
the workspace that defines one uses its own, any other the default
workspace's. Each function below that reads the stored config takes an
optional ``workspace``; without one it uses the running code's workspace (a
run's context var). The OAuth state carries the workspace the flow was
started for, so the callback writes the refresh token back to that
workspace's document, and access tokens are cached per document.
"""
from __future__ import annotations

import hashlib
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

#: Who the connected account is: the userinfo lookup after the consent
#: screen needs it for the address, and so does IMAP's XOAUTH2, which names
#: the mailbox by that address.
IDENTITY_SCOPES = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
)

#: Full IMAP and SMTP access to the connected Gmail mailbox, the only scope
#: Google accepts for XOAUTH2. Asked for only when the operator clicks
#: "Connect with Gmail" (``/api/google/oauth/start?gmail=1``): it is a
#: restricted scope, so a person who only wants Drive never sees it on the
#: consent screen. A service account cannot use it without domain-wide
#: delegation, so Gmail always goes through the OAuth refresh token.
GMAIL_SCOPE = "https://mail.google.com/"

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

_REQUEST_TIMEOUT = 30.0
#: How long an OAuth state nonce is good for: the consent-screen round trip
#: is seconds long, not minutes, so this is generous, not tight.
_STATE_TTL_SECONDS = 600


class GoogleError(Exception):
    """Raised for configuration or token errors; the message is UI-safe."""


class GoogleGrantRevoked(GoogleError):
    """A refresh token Google no longer accepts (revoked, expired, or the
    account removed the app): the grant is dead and asking again is the fix."""


# ── OAuth state nonces ───────────────────────────────────────────────────────
# Kept in memory, not the database: the callback is a same-process round trip
# seconds after /oauth/start, so nothing here needs to survive a restart, and
# a dict keeps the dashboard backend from taking a database round trip on
# every step of a flow a person is actively watching. Each nonce remembers the
# workspace the flow was started for, so the callback (which carries no
# workspace of its own) stores the grant in that workspace's connector.

_DEFAULT_WORKSPACE = "default"
_STATES: dict[str, tuple[float, str]] = {}


def _prune_states() -> None:
    now = time.time()
    for s in [s for s, (exp, _ws) in _STATES.items() if exp < now]:
        _STATES.pop(s, None)


def new_state(workspace: Optional[str] = None) -> str:
    """A fresh, single-use nonce for the consent screen's ``state`` param,
    bound to the workspace whose connector the flow connects."""
    _prune_states()
    state = uuid.uuid4().hex
    _STATES[state] = (time.time() + _STATE_TTL_SECONDS,
                      str(workspace or "").strip() or _DEFAULT_WORKSPACE)
    return state


def pop_state(state: str) -> Optional[str]:
    """The workspace a state was issued for, once, within ten minutes of
    issue; None for a missing, unknown or expired state (replay, a forged
    callback), which leaves nothing behind to retry with."""
    _prune_states()
    entry = _STATES.pop(str(state or ""), None)
    return entry[1] if entry else None


def consume_state(state: str) -> bool:
    """True once, for a state issued within the last ten minutes."""
    return pop_state(state) is not None


def _store(workspace: Optional[str] = None):
    """The connector in effect for ``workspace`` (the running code's when
    None), bound to its one document."""
    from . import store_for
    return store_for(workspace)


# ── the three-legged flow ────────────────────────────────────────────────────

def requested_scopes(*, gmail: bool = False) -> list[str]:
    """What the consent screen asks for: the Workspace surfaces, who the
    account is, and Gmail only when asked."""
    scopes = [*SCOPES, *IDENTITY_SCOPES]
    if gmail:
        scopes.append(GMAIL_SCOPE)
    return scopes


def build_auth_url(redirect_uri: str, state: str, *, gmail: bool = False,
                   workspace: Optional[str] = None) -> str:
    """Google's consent screen URL for this operator's OAuth client.

    ``access_type=offline`` plus ``prompt=consent`` is what makes Google hand
    back a refresh token even for an account that has already approved this
    client once before. ``include_granted_scopes`` keeps what the account
    granted earlier, so reconnecting without ``gmail`` after a Gmail grant
    does not take the mailbox away.
    """
    client_id = _store(workspace).get("client_id")
    if not client_id:
        raise GoogleError("No Google OAuth client id configured on the Connectors page")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(requested_scopes(gmail=gmail)),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str, redirect_uri: str, *,
                  code_verifier: Optional[str] = None,
                  workspace: Optional[str] = None) -> dict[str, Any]:
    """POST the authorization code for tokens; the parsed JSON body
    (``access_token``, ``refresh_token``, ``expires_in``, ...).
    ``code_verifier`` is the PKCE verifier of a flow that sent a challenge
    (the consent portal's does)."""
    store = _store(workspace)
    client_id = store.get("client_id")
    client_secret = store.get("client_secret")
    if not client_id or not client_secret:
        raise GoogleError("No Google OAuth client configured on the Connectors page")
    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    if code_verifier:
        data["code_verifier"] = code_verifier
    try:
        resp = httpx.post(TOKEN_URL, data=data, timeout=_REQUEST_TIMEOUT)
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


# ── an end user's own account (the consent portal) ──────────────────────────
# connectors/consent/ asks a widget visitor or a channel user for their own
# Google account through the operator's OAuth client. Same endpoints as the
# operator's flow above, with the scopes the agent's settings name, PKCE, and
# a refresh token that belongs to the end user and lives as their personal
# secret, never in STORE.

def build_consent_url(redirect_uri: str, state: str, scopes: list[str], *,
                      code_challenge: Optional[str] = None,
                      login_hint: Optional[str] = None,
                      workspace: Optional[str] = None) -> str:
    """Google's consent screen for an end user, offline access so a refresh
    token comes back, ``prompt=consent`` so it comes back every time."""
    client_id = _store(workspace).get("client_id")
    if not client_id:
        raise GoogleError("No Google OAuth client id configured on the Connectors page")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(params)}"


def consent_ready(workspace: Optional[str] = None) -> bool:
    """Whether the operator's OAuth client exists to ask end users with."""
    store = _store(workspace)
    return bool(str(store.get("client_id") or "").strip()
                and str(store.get("client_secret") or "").strip())


def refresh_user_token(refresh_token: str, *, workspace: Optional[str] = None) -> tuple[str, float]:
    """``(access_token, expiry)`` for an end user's refresh token. Raises
    :class:`GoogleGrantRevoked` when Google refuses the grant itself."""
    store = _store(workspace)
    client_id = store.get("client_id")
    client_secret = store.get("client_secret")
    if not (client_id and client_secret):
        raise GoogleError("No Google OAuth client configured on the Connectors page")
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
        raise GoogleGrantRevoked("Google no longer accepts this account's access. Ask for access again.")
    if resp.status_code >= 400:
        raise GoogleError(f"Google token refresh failed ({resp.status_code})")
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise GoogleError("Google token refresh returned no access token")
    return str(token), time.time() + float(data.get("expires_in") or 3600) - 60


def revoke_token(token: str) -> bool:
    """Ask Google to revoke a token (a refresh token revokes the whole
    grant). True when Google confirmed; never raises."""
    try:
        resp = httpx.post(REVOKE_URL, data={"token": token}, timeout=_REQUEST_TIMEOUT,
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
    except httpx.HTTPError:
        return False
    return resp.status_code < 400


# ── access tokens ────────────────────────────────────────────────────────────
# One process-wide cache, keyed by the workspace document the credentials came
# from and a digest of the credentials themselves: refreshing on every tool
# call would mean a token round trip per Drive search, and two workspaces with
# their own Google connectors must never share a token. A credential changed
# by another process changes the digest, so a stale token is never handed out.
# Cleared on connect and disconnect (dashboard/backend/routes/google.py) and by
# reset_cache() in tests.
#
# Two kinds per document: "access" (whichever mode wins, the service account
# when both are set) and "oauth" (the refresh token's own token, for Gmail,
# which never accepts the service account's).

_TOKENS: dict[tuple[str, str, str], tuple[str, float]] = {}


def _cache_key(store, kind: str, *fields: str) -> tuple[str, str, str]:
    cfg = store.get_config()
    raw = "\x00".join(str(cfg.get(f) or "") for f in fields)
    return (kind, store.workspace or _DEFAULT_WORKSPACE, hashlib.sha256(raw.encode("utf-8")).hexdigest())


def _cached(key: tuple[str, str, str]) -> Optional[str]:
    hit = _TOKENS.get(key)
    if hit and time.time() < hit[1]:
        return hit[0]
    return None


def _refresh_oauth_token() -> tuple[str, float]:
    """A fresh access token from the stored refresh token of the connector in
    effect for the running code (callers bind the workspace with
    :func:`connectors.channels.store.in_workspace`)."""
    store = _store()
    client_id = store.get("client_id")
    client_secret = store.get("client_secret")
    refresh_token = store.get("refresh_token")
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

    raw = _store().get("service_account_json")
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


def get_access_token(workspace: Optional[str] = None) -> str:
    """A bearer token for whichever mode is configured in the Google
    connector in effect for ``workspace`` (the running code's when None),
    cached in memory until shortly before it expires. Raises
    :class:`GoogleError` with a UI-safe message when unconfigured or the
    grant fails.

    In a widget or channel turn whose end user granted their own account
    (connectors/consent/, docs/consent.md), the end user's token instead,
    and never the hub's for an agent set to act only as the end user."""
    from connectors.consent import access as _consent

    try:
        end_user_token = _consent.token_for_turn("google")
    except _consent.ConsentError as exc:
        raise GoogleError(str(exc)) from exc
    if end_user_token:
        return end_user_token
    store = _store(workspace)
    key = _cache_key(store, "access", "service_account_json", "client_id", "client_secret",
                     "refresh_token")
    token = _cached(key)
    if token:
        return token
    from connectors.channels.store import in_workspace
    fetch = _service_account_token if str(store.get("service_account_json") or "").strip() \
        else _refresh_oauth_token
    token, expiry = in_workspace(store.workspace, fetch)
    _TOKENS[key] = (token, expiry)
    return token


def reset_cache() -> None:
    """Drop every cached access token: after a disconnect/reconnect, and
    between tests."""
    _TOKENS.clear()


# ── Gmail over IMAP and SMTP ─────────────────────────────────────────────────

def granted_scopes(workspace: Optional[str] = None) -> set[str]:
    """The scopes the connected account granted, as the callback stored them."""
    return set(str(_store(workspace).get("granted_scopes") or "").split())


def _has_gmail(cfg: dict[str, Any]) -> bool:
    return (bool(str(cfg.get("refresh_token") or "").strip())
            and GMAIL_SCOPE in set(str(cfg.get("granted_scopes") or "").split()))


def has_gmail(workspace: Optional[str] = None) -> bool:
    """Whether the OAuth connection carries the Gmail scope."""
    return _has_gmail(_store(workspace).get_config())


def gmail_status(workspace: Optional[str] = None) -> dict[str, Any]:
    """What the mail forms show: connected at all, Gmail granted, as whom,
    for the Google connector in effect in ``workspace`` (its own or the
    default workspace's)."""
    cfg = _store(workspace).get_config()
    return {
        "connected": bool(str(cfg.get("refresh_token") or "").strip()),
        "gmail": _has_gmail(cfg),
        "account_email": str(cfg.get("account_email") or ""),
    }


def gmail_login(workspace: Optional[str] = None) -> tuple[str, str]:
    """``(address, access_token)`` for XOAUTH2 against Gmail's IMAP and SMTP,
    with the Google connector in effect in ``workspace`` (the running
    code's when None).

    Always the OAuth refresh token's token, never the service account's.
    Raises :class:`GoogleError` with a UI-safe message when Google is not
    connected, the Gmail scope was not granted, or the address is unknown.
    """
    store = _store(workspace)
    cfg = store.get_config()
    if not str(cfg.get("refresh_token") or "").strip():
        raise GoogleError("Google is not connected. Click Connect with Gmail on the Connectors page.")
    if not _has_gmail(cfg):
        raise GoogleError("The Google connection has no Gmail access. Click Connect with Gmail on the Connectors page.")
    address = str(cfg.get("account_email") or "").strip()
    if not address:
        raise GoogleError("The connected Google account has no known address. Reconnect it on the Connectors page.")
    key = _cache_key(store, "oauth", "client_id", "client_secret", "refresh_token")
    token = _cached(key)
    if not token:
        from connectors.channels.store import in_workspace
        token, expiry = in_workspace(store.workspace, _refresh_oauth_token)
        _TOKENS[key] = (token, expiry)
    return address, token


def xoauth2_string(address: str, token: str) -> str:
    """The SASL XOAUTH2 initial response, before base64 (imaplib and
    smtplib encode it themselves)."""
    return f"user={address}\x01auth=Bearer {token}\x01\x01"


__all__ = [
    "GoogleError", "GoogleGrantRevoked", "SCOPES", "IDENTITY_SCOPES", "GMAIL_SCOPE", "new_state",
    "consume_state", "pop_state", "build_consent_url", "consent_ready", "refresh_user_token", "revoke_token",
    "requested_scopes", "build_auth_url", "exchange_code", "fetch_userinfo", "get_access_token",
    "reset_cache", "granted_scopes", "has_gmail", "gmail_status", "gmail_login", "xoauth2_string",
]
