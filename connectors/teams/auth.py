"""
Bot Framework authentication: outbound app tokens and inbound activity verification.

Two directions, two different credentials:

- Outbound: the hub is the client. It gets a token from Azure AD with its own
  app id and password (client credentials, scope
  ``https://api.botframework.com/.default``) and attaches it as a Bearer
  header to every reply it posts back to Teams. Cached until shortly before
  expiry, so a reply does not pay for a token fetch every time
  (``connectors/teams/service.py`` calls this on every send).
- Inbound: Teams is the client. Every activity it posts to
  ``dashboard/backend/routes/teams_channel.py`` carries a Bearer JWT signed by the
  Bot Framework itself, not by the bot's own app password. Verifying it means
  fetching the Bot Framework's own signing keys (the OpenID metadata, then
  its JWKS), checking the signature, the audience (the bot's own app id) and
  the issuer, and, when the token carries a ``serviceurl`` claim, that it
  matches the activity's own ``serviceUrl``.

Both the outbound token fetch and the inbound JWKS fetch are plain module
functions rather than methods, so a test can inject a fake cheaply instead of
reaching the network (see tests/test_channel_teams.py).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Optional

import httpx
import jwt

log = logging.getLogger("channels.teams.auth")

_OUTBOUND_TOKEN_URL = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
_OUTBOUND_SCOPE = "https://api.botframework.com/.default"
_OPENID_CONFIG_URL = "https://login.botframework.com/v1/.well-known/openidconfiguration"
_ISSUER = "https://api.botframework.com"
_JWKS_TTL_SECONDS = 3600.0
#: Refresh an outbound token this many seconds before its stated expiry.
_OUTBOUND_REFRESH_SKEW = 60.0


class TeamsAuthError(Exception):
    """Raised when an inbound activity's bearer token does not check out."""


# ── outbound: the bot's own app token ───────────────────────────────────────

_outbound_cache: dict[str, tuple[str, float]] = {}


def clear_caches() -> None:
    """Drop the cached outbound token and JWKS. Used between tests."""
    _outbound_cache.clear()
    _jwks_cache["keys"] = None
    _jwks_cache["fetched_at"] = 0.0


def _token_request_body(app_id: str, app_password: str) -> dict[str, str]:
    return {
        "grant_type": "client_credentials",
        "client_id": app_id,
        "client_secret": app_password,
        "scope": _OUTBOUND_SCOPE,
    }


def _store_outbound_token(app_id: str, body: dict[str, Any], now: float) -> str:
    token = body.get("access_token")
    if not token:
        raise TeamsAuthError("token response carried no access_token")
    expires_in = float(body.get("expires_in") or 3600)
    _outbound_cache[app_id] = (token, now + expires_in - _OUTBOUND_REFRESH_SKEW)
    return token


async def get_outbound_token(app_id: str, app_password: str) -> str:
    """The bot's own bearer token for posting replies, cached until near expiry."""
    cached = _outbound_cache.get(app_id)
    now = time.time()
    if cached and cached[1] > now:
        return cached[0]
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(_OUTBOUND_TOKEN_URL, data=_token_request_body(app_id, app_password))
    if resp.status_code != 200:
        raise TeamsAuthError(f"token request failed: {resp.status_code} {resp.text[:200]}")
    return _store_outbound_token(app_id, resp.json(), now)


def get_outbound_token_sync(app_id: str, app_password: str) -> str:
    """Sync counterpart, used by ``send_text_sync`` for outbound notifications."""
    cached = _outbound_cache.get(app_id)
    now = time.time()
    if cached and cached[1] > now:
        return cached[0]
    resp = httpx.post(_OUTBOUND_TOKEN_URL, data=_token_request_body(app_id, app_password), timeout=15.0)
    if resp.status_code != 200:
        raise TeamsAuthError(f"token request failed: {resp.status_code} {resp.text[:200]}")
    return _store_outbound_token(app_id, resp.json(), now)


# ── inbound: verifying an activity's bearer token ───────────────────────────

_jwks_cache: dict[str, Any] = {"keys": None, "fetched_at": 0.0}


def fetch_jwks(*, force: bool = False) -> dict[str, Any]:
    """The Bot Framework's current signing keys, cached for an hour.

    Two hops, both documented by the Bot Framework: the OpenID metadata names
    the ``jwks_uri``, which carries the actual keys.
    """
    now = time.time()
    if not force and _jwks_cache["keys"] is not None and now - _jwks_cache["fetched_at"] < _JWKS_TTL_SECONDS:
        return _jwks_cache["keys"]
    with httpx.Client(timeout=15.0) as client:
        meta = client.get(_OPENID_CONFIG_URL)
        meta.raise_for_status()
        jwks_uri = meta.json().get("jwks_uri")
        if not jwks_uri:
            raise TeamsAuthError("OpenID metadata carried no jwks_uri")
        keys_resp = client.get(jwks_uri)
        keys_resp.raise_for_status()
        jwks = keys_resp.json()
    _jwks_cache["keys"] = jwks
    _jwks_cache["fetched_at"] = now
    return jwks


def _find_key(jwks: dict[str, Any], kid: Optional[str]) -> Optional[dict[str, Any]]:
    for key in jwks.get("keys") or []:
        if key.get("kid") == kid:
            return key
    return None


def _signing_key_from_jwk(jwk: dict[str, Any]) -> Any:
    """An RSA public key object built from one JWK entry.

    ``jwt.PyJWK`` is PyJWT's own wrapper and the simplest path when present;
    ``RSAAlgorithm.from_jwk`` is the fallback for older PyJWT versions that
    do not carry it.
    """
    if hasattr(jwt, "PyJWK"):
        return jwt.PyJWK(jwk, algorithm="RS256").key
    return jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))


def verify_activity_token(
    token: str, app_id: str, *,
    service_url: Optional[str] = None,
    jwks_fetcher: Optional[Callable[[], dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Verify an inbound activity's ``Authorization: Bearer <token>``.

    Raises :class:`TeamsAuthError` on anything that does not check out: a
    malformed token, an unknown signing key, a bad signature, the wrong
    audience or issuer, or (when the token carries one) a ``serviceurl``
    claim that does not match the activity's own ``serviceUrl``.
    ``jwks_fetcher`` defaults to :func:`fetch_jwks`; a test passes a fake one
    instead of reaching the network.
    """
    if not app_id:
        raise TeamsAuthError("no app_id configured for this bot")
    fetcher = jwks_fetcher or fetch_jwks
    try:
        header = jwt.get_unverified_header(token)
    except Exception as exc:  # noqa: BLE001 - reported as a verification failure
        raise TeamsAuthError(f"malformed token: {exc}") from exc
    kid = header.get("kid")

    jwks = fetcher()
    key = _find_key(jwks, kid)
    if key is None and fetcher is fetch_jwks:
        # The Bot Framework rotates keys; one forced refresh covers a kid
        # minted after our cache was last filled.
        jwks = fetch_jwks(force=True)
        key = _find_key(jwks, kid)
    if key is None:
        raise TeamsAuthError(f"no signing key found for kid={kid!r}")

    try:
        public_key = _signing_key_from_jwk(key)
    except Exception as exc:  # noqa: BLE001
        raise TeamsAuthError(f"could not build a signing key: {exc}") from exc

    try:
        claims = jwt.decode(
            token, public_key, algorithms=["RS256"],
            audience=app_id, issuer=_ISSUER,
            options={"require": ["exp", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise TeamsAuthError(f"token verification failed: {exc}") from exc

    claimed_service_url = claims.get("serviceurl")
    if claimed_service_url and service_url and claimed_service_url.rstrip("/") != service_url.rstrip("/"):
        raise TeamsAuthError("serviceUrl in the token does not match the activity")
    return claims


__all__ = [
    "TeamsAuthError", "get_outbound_token", "get_outbound_token_sync",
    "fetch_jwks", "verify_activity_token", "clear_caches",
]
