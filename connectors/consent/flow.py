"""
The round trip, without HTTP: hand out a link, open it, send the end user to
the provider, and take the code back.

``dashboard/backend/routes/consent.py`` drives this from the public page and
the callback; ``connectors/consent/tools.py`` calls :func:`request_access`
from the agent's tool. Everything that decides something lives here so the
tests can walk a whole grant with the provider's HTTP mocked.

The state nonce. Continue mints a random state, keeps only its SHA-256 on the
request row (status ``started``) together with a PKCE verifier, and sends the
nonce to the provider. The callback hashes what comes back and finds the row
by it, so a forged or replayed callback finds nothing, a callback on another
API replica still finds its row, and finishing the row clears both, which is
what makes the link single use.

The app registration. A request is asked with the Google or Microsoft
connector of the workspace the turn ran in (its own, else the default
workspace's; connectors/channels/store.py). The public page and the callback
run outside any turn, so they name the request's workspace explicitly.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets as pysecrets
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from common import secrets as secrets_mod

from . import access, catalog, links, store

log = logging.getLogger(__name__)

#: How long a link is good for, unless AGENTS_HUB_CONSENT_TTL_MINUTES says otherwise.
DEFAULT_TTL_MINUTES = 30
#: At most this many links per end user per hour: a looping agent must not
#: flood a visitor (or the table) with them.
MAX_LINKS_PER_HOUR = 10
CALLBACK_PATH = "/consent/callback"


class ConsentFlowError(Exception):
    """The request cannot go on; ``code`` picks the page, the message is for logs."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def ttl_seconds() -> int:
    raw = os.environ.get("AGENTS_HUB_CONSENT_TTL_MINUTES", "").strip()
    try:
        minutes = int(raw) if raw else DEFAULT_TTL_MINUTES
    except ValueError:
        minutes = DEFAULT_TTL_MINUTES
    return max(5, min(minutes, 24 * 60)) * 60


def public_base() -> str:
    """Where an end user's browser reaches the hub (``AGENTS_HUB_PUBLIC_URL``),
    a local default when it is not set."""
    from common.hub_urls import public_base as _public
    return _public() or "http://localhost:8000"


def redirect_uri(base: Optional[str] = None) -> str:
    """The callback the Google and Microsoft app registrations must list."""
    return f"{(base or public_base()).rstrip('/')}{CALLBACK_PATH}"


def provider_ready(provider: str, workspace: Optional[str] = None) -> bool:
    """Whether the app registration in effect in ``workspace`` (the running
    code's when None) exists to ask end users with."""
    if provider == catalog.GOOGLE:
        from connectors.google.auth import consent_ready
        return consent_ready(workspace)
    from connectors.microsoft.graph import consent_ready
    return consent_ready(workspace)


# ── the agent's side ─────────────────────────────────────────────────────────

def request_access(provider: str, purpose: str = "", *,
                   run_id: Optional[str] = None) -> Dict[str, Any]:
    """A consent link for the end user of the current turn. Raises
    :class:`ConsentFlowError` with a message for the agent."""
    provider = str(provider or "").strip().lower()
    if not catalog.is_provider(provider):
        raise ConsentFlowError("bad_provider", f"provider must be one of: {', '.join(catalog.PROVIDERS)}")
    scope = access.turn_scope()
    if scope is None:
        raise ConsentFlowError(
            "no_end_user", "Asking for account access works only in a widget or chat channel "
            "conversation, where the person you talk to is not a hub user.")
    workspace, agent_id, principal = scope
    store.expire_open()  # cheap, and keeps the table's open rows honest
    settings = store.get_settings(agent_id)
    if provider not in settings.get("providers", []):
        raise ConsentFlowError(
            "not_enabled", f"This agent is not set up to ask for {catalog.PROVIDER_LABELS[provider]} "
            "access. The operator turns it on in the agent's Account access card.")
    if not provider_ready(provider, workspace):
        raise ConsentFlowError(
            "not_configured", f"The hub has no {catalog.PROVIDER_LABELS[provider]} app registration "
            "to ask with. The operator sets it up on the Connectors page.")
    try:
        secrets_mod.backend()._check_writable()
    except secrets_mod.SecretsError as exc:
        raise ConsentFlowError("no_secret_store", f"The hub cannot store the grant: {exc}") from exc
    keys = list(settings.get("scopes", {}).get(provider) or catalog.DEFAULT_KEYS[provider])
    if access.has_grant(workspace, agent_id, principal, provider):
        row = store.grant_row(workspace, agent_id, principal, provider) or {}
        if set(keys) <= set(row.get("scopes") or []):
            return {"already_granted": True, "provider": provider,
                    "account_email": row.get("account_email") or ""}
    if store.recent_open_count(principal) >= MAX_LINKS_PER_HOUR:
        raise ConsentFlowError("too_many", "Too many access links for this person in the last "
                                           "hour. Use the link already sent.")
    row = store.create_request(workspace=workspace, agent_id=agent_id, principal=principal,
                               provider=provider, scopes=keys, purpose=purpose,
                               ttl_seconds=ttl_seconds(), run_id=run_id)
    expires = int(datetime.fromisoformat(row["expires_at"]).timestamp())
    token = links.mint(row["request_id"], principal, expires)
    return {"already_granted": False, "provider": provider, "url": f"{public_base()}/consent/{token}",
            "expires_at": row["expires_at"], "access": keys}


# ── the end user's side ──────────────────────────────────────────────────────

def open_request(token: str) -> Dict[str, Any]:
    """The request a link names, if the link is valid and the request open.
    Raises :class:`ConsentFlowError` with ``invalid``, ``expired``, ``used``
    or the request's final status (``granted``, ``denied``, ``revoked``)."""
    claims = links.verify(token)
    if claims is None:
        raise ConsentFlowError("invalid")
    row = store.get_request(claims["request_id"])
    if row is None or row["principal"] != claims["principal"]:
        raise ConsentFlowError("invalid")
    if row["status"] in store.OPEN_STATUSES:
        if store.is_expired(row):
            store.finish(row["request_id"], "expired")
            raise ConsentFlowError("expired")
        return row
    if row["status"] in ("granted", "replaced"):
        raise ConsentFlowError("granted")
    if row["status"] in ("denied", "expired", "revoked"):
        raise ConsentFlowError(row["status"])
    raise ConsentFlowError("used")


def _pkce() -> Tuple[str, str]:
    verifier = pysecrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge.rstrip(b"=").decode("ascii")


def state_hash(state: str) -> str:
    return hashlib.sha256(str(state or "").encode("utf-8")).hexdigest()


def begin(row: Dict[str, Any], *, base: Optional[str] = None) -> str:
    """Mark the request started and return the provider's authorize URL."""
    state = pysecrets.token_urlsafe(32)
    verifier, challenge = _pkce()
    if not store.start(row["request_id"], state_hash=state_hash(state), pkce_verifier=verifier):
        raise ConsentFlowError("used")
    provider = row["provider"]
    scopes = catalog.provider_scopes(provider, row["scopes"])
    workspace = row.get("workspace") or None
    if provider == catalog.GOOGLE:
        from connectors.google.auth import build_consent_url
        return build_consent_url(redirect_uri(base), state, scopes, code_challenge=challenge,
                                 workspace=workspace)
    from connectors.microsoft.graph import build_consent_url
    return build_consent_url(redirect_uri(base), state, scopes, code_challenge=challenge,
                             workspace=workspace)


def _exchange(row: Dict[str, Any], code: str, verifier: str,
              base: Optional[str]) -> Tuple[str, str, list]:
    """``(refresh_token, account_email, granted provider scopes)``."""
    provider = row["provider"]
    scopes = catalog.provider_scopes(provider, row["scopes"])
    # The same app registration the consent screen was opened with: the
    # request's workspace's.
    workspace = row.get("workspace") or None
    if provider == catalog.GOOGLE:
        from connectors.google.auth import GoogleError, exchange_code, fetch_userinfo
        tokens = exchange_code(code, redirect_uri(base), code_verifier=verifier, workspace=workspace)
        refresh = tokens.get("refresh_token")
        if not refresh:
            raise GoogleError("Google returned no refresh token")
        info = fetch_userinfo(str(tokens.get("access_token") or ""))
        granted = str(tokens.get("scope") or "").split() or scopes
        return str(refresh), str(info.get("email") or ""), granted
    from connectors.microsoft.graph import GraphError, exchange_delegated_code, fetch_me
    tokens = exchange_delegated_code(code, redirect_uri(base), scopes, code_verifier=verifier,
                                     workspace=workspace)
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise GraphError("Microsoft returned no refresh token")
    me = fetch_me(str(tokens.get("access_token") or ""))
    email = str(me.get("mail") or me.get("userPrincipalName") or "")
    # Refresh with exactly what was asked for: the token response lists the
    # Graph scopes with their resource prefix, which a refresh also accepts,
    # but the identity scopes it leaves out would then go missing.
    return str(refresh), email, scopes


def complete(*, state: str, code: str = "", error: str = "",
             base: Optional[str] = None) -> Dict[str, Any]:
    """Finish the round trip for the callback. Returns the closed request
    row with ``status`` ``granted``; raises :class:`ConsentFlowError`
    (``invalid``, ``expired``, ``denied``, ``failed``)."""
    row = store.by_state_hash(state_hash(state)) if state else None
    if row is None:
        raise ConsentFlowError("invalid", "unknown or used state")
    if store.is_expired(row):
        store.finish(row["request_id"], "expired")
        raise ConsentFlowError("expired")
    if error or not code:
        # access_denied when the person said no; anything else is a failure
        # the provider describes in its own words, kept short and off the page.
        status = "denied" if (error or "access_denied") == "access_denied" else "failed"
        store.finish(row["request_id"], status, error=error or "no code")
        raise ConsentFlowError(status, error or "no code")
    verifier = row.get("pkce_verifier") or ""
    try:
        refresh, email, granted = _exchange(row, code, verifier, base)
        access.store_grant(row, refresh_token=refresh, account_email=email, granted_scopes=granted)
    except Exception as exc:  # noqa: BLE001 - every provider or storage failure ends on the failure page
        log.warning("consent: request %s failed at the callback: %s", row["request_id"],
                    type(exc).__name__)
        store.finish(row["request_id"], "failed", error=str(exc)[:300])
        raise ConsentFlowError("failed", type(exc).__name__) from exc
    if not store.finish(row["request_id"], "granted", account_email=email):
        raise ConsentFlowError("used")
    store.supersede(row["workspace"], row["agent_id"], row["principal"], row["provider"],
                    row["request_id"])
    from common import audit
    audit.record("consent.grant", actor=access.end_user_actor(row["principal"], email),
                 object_type="consent", object_id=f"{row['agent_id']}:{row['provider']}",
                 workspace=row["workspace"],
                 details={"principal": row["principal"], "provider": row["provider"],
                          "access": row["scopes"], "request_id": row["request_id"]})
    return {**row, "status": "granted", "account_email": email}


def decline(row: Dict[str, Any]) -> None:
    store.finish(row["request_id"], "denied", error="declined on the consent page")


__all__ = [
    "CALLBACK_PATH", "ConsentFlowError", "DEFAULT_TTL_MINUTES", "MAX_LINKS_PER_HOUR", "begin",
    "complete", "decline", "open_request", "provider_ready", "public_base", "redirect_uri",
    "request_access", "state_hash", "ttl_seconds",
]
