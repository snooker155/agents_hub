"""Short-lived, signed tickets: the preview proxy's and the streams'.

Two kinds of ticket share one signing key and one format here. A *preview*
ticket (kind ``container`` or ``project``) is the credential of an iframe
that loads a proxied page. An *auth* ticket (kind ``auth``, see
:func:`mint_auth`) is the one-time credential an ``EventSource`` or a
``WebSocket`` carries in its query string, since neither can set a header,
so the long-lived session token never has to appear in a URL (and in the
access logs a URL ends up in).

Preview tickets
---------------

An iframe navigation cannot carry the ``Authorization: Bearer`` header the
rest of the API uses, and the proxied page must not run same-origin with the
dashboard, or it could read the hub's own tokens out of ``localStorage``. So
the dashboard mints a ticket with an authenticated call
(``POST /api/preview/tickets``), the iframe then loads
``/preview/<ticket>/<path>`` (outside ``/api``: an open path, see
``common/auth.py``'s ``is_open_path``), and the ticket in the path is the
credential.

A ticket is an HMAC-signed, URL-safe token carrying ``{kind, id, user, exp}``:
which target it is for, who minted it and when it stops working. It carries
no secret of its own and no resolved URL: ``dashboard/backend/routes/preview.py``
re-resolves the target (a container's ``http_url``, a project's frontend URL)
fresh on every proxied request, so a container restart or a project's port
change between mint and use is simply picked up rather than baked in.

Binding the minting user matters in ``multi`` mode: :func:`verify` checks the
user still exists, so a revoked account's outstanding tickets die with it
instead of outliving the account by up to their TTL.

A preview ticket lives ten minutes. It is kept alive by :func:`renew`: the
proxy hands a fresh one back in ``X-Preview-Ticket`` once the presented one
is past half its life, and the dashboard calls ``POST
/api/preview/tickets/renew`` while the preview is open (an iframe's response
headers are out of its reach).

Auth tickets
------------
Minted by ``POST /api/auth/ticket`` for whoever the request authenticated
as, valid for a minute and good for one use: the nonce of a verified ticket
goes into a per-process consumed set (pruned as entries expire), so a URL
copied out of a log or a proxy is dead by the time anyone reads it. Across
replicas the set is not shared, so a ticket could be presented once per
replica within its TTL; at 60 seconds that is an accepted trade for keeping
the check free of a database round trip.

Terminal tickets
----------------
Minted by ``POST /api/terminal/{kind}/{id}/ticket`` (see
``dashboard/backend/routes/terminal.py`` and docs/terminal.md): an auth
ticket that also names the one container it may open a shell in (a run or a
service replica) and, on a reconnect, the session it resumes. A shell is the
widest thing the dashboard hands out, so an ordinary auth ticket does not
open one, and a terminal ticket opens nothing else: :func:`verify_terminal`
checks the target in the socket's path against the one signed in.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from typing import Any, Dict, Optional

from common.paths import AGENTS_HUB_ROOT

log = logging.getLogger(__name__)

#: A preview link is meant for one sitting at the hub, not a bookmark. Kept
#: short and renewed while the preview is open (see :func:`renew`).
DEFAULT_TTL_SECONDS = 600

#: An auth ticket only has to survive the round trip between minting it and
#: opening the stream it is for.
AUTH_TTL_SECONDS = 60

_PREVIEW_KINDS = ("container", "project", "deployment")
_AUTH_KIND = "auth"
_TERMINAL_KIND = "terminal"

#: A terminal ticket, like an auth ticket, only bridges the call that mints
#: it and the socket that spends it.
TERMINAL_TTL_SECONDS = 60

_SECRET_FILE = AGENTS_HUB_ROOT / "preview_secret"
_SECRET_BYTES = 32

#: Cached once per process: read from disk (or generated) on first use, so a
#: signature check never touches the filesystem again after that.
_cached_secret: Optional[bytes] = None


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _persisted_secret() -> bytes:
    """A dedicated 32-byte secret, generated once and kept under the state
    directory (mode 0600), for an installation with no ``AGENTS_HUB_SECRET_KEY``
    configured. Cached in memory after the first read so a signature check is
    one HMAC, not a file open."""
    global _cached_secret
    if _cached_secret is not None:
        return _cached_secret
    try:
        existing = _SECRET_FILE.read_bytes()
        if len(existing) >= _SECRET_BYTES:
            _cached_secret = existing
            return _cached_secret
    except OSError:
        pass
    generated = secrets.token_bytes(_SECRET_BYTES)
    try:
        AGENTS_HUB_ROOT.mkdir(parents=True, exist_ok=True)
        _SECRET_FILE.write_bytes(generated)
        try:
            _SECRET_FILE.chmod(0o600)
        except OSError:
            log.debug("could not chmod preview_secret to 0600", exc_info=True)
    except OSError:
        # Read-only state directory or similar: keep the secret in memory for
        # this process. A ticket minted here simply stops verifying once the
        # process restarts, which reads to the caller exactly like an expired
        # ticket does.
        log.debug("could not persist preview_secret, keeping it in memory only",
                  exc_info=True)
    _cached_secret = generated
    return _cached_secret


def _signing_key() -> bytes:
    """The per-installation secret that signs preview tickets.

    Reuses ``AGENTS_HUB_SECRET_KEY`` (``settings.secret_key``) when one is
    configured: the same persistent, per-installation secret
    ``common/signed_state.py``'s sign-in state cookie and ``common/secrets.py``'s
    encryption at rest already fall back to, so a deployment that has set it
    once gets ticket signing "for free" and does not accumulate one more
    secret to manage. Otherwise a dedicated secret is generated once and kept
    under the state directory.
    """
    from common.config import settings
    material = (getattr(settings, "secret_key", "") or "").strip()
    if material:
        return hashlib.sha256(b"agents-hub-preview-ticket:" + material.encode("utf-8")).digest()
    return _persisted_secret()


def _sign(payload: Dict[str, Any]) -> str:
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    mac = hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64encode(mac)}"


def _decode(ticket: str) -> Optional[Dict[str, Any]]:
    """The payload of a well-formed, correctly signed, unexpired ticket of
    any kind, or ``None``."""
    if not ticket or "." not in ticket:
        return None
    body, _, mac_part = ticket.rpartition(".")
    try:
        presented_mac = _b64decode(mac_part)
        body_bytes = body.encode("ascii")
    except (ValueError, UnicodeEncodeError):
        return None
    expected_mac = hmac.new(_signing_key(), body_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_mac, presented_mac):
        return None
    try:
        payload = json.loads(_b64decode(body).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        expires_at = float(payload.get("exp", 0))
    except (TypeError, ValueError):
        return None
    if expires_at < time.time():
        return None
    payload["exp"] = expires_at
    return payload


def _user_gone(user: Optional[str]) -> bool:
    """True when ``user`` names an account that no longer exists. Only
    ``multi`` has accounts; the other modes' constant ids always stand."""
    if not user:
        return False
    from common import identity
    return identity.current_mode() == identity.MULTI and identity.get_user(user) is None


# ── preview tickets ──────────────────────────────────────────────────────────

def mint(target: Dict[str, Any], *, principal_id: str,
         ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Mint a signed, short-lived ticket for one preview target.

    ``target`` is ``{"kind": "container" | "project" | "deployment", "id":
    <name, project id, or deployment id[/service]>}``. The proxy resolves that id to an actual URL again on every
    request; nothing about the current URL is baked into the ticket.
    """
    ttl = max(1, int(ttl_seconds))
    return _sign({
        "kind": target["kind"],
        "id": target["id"],
        "user": principal_id,
        "exp": time.time() + ttl,
        "ttl": ttl,
    })


def _verified_preview(ticket: str) -> Optional[Dict[str, Any]]:
    payload = _decode(ticket)
    if payload is None:
        return None
    if payload.get("kind") not in _PREVIEW_KINDS or not payload.get("id"):
        return None
    if _user_gone(payload.get("user")):
        return None
    return payload


def verify(ticket: str) -> Optional[Dict[str, Any]]:
    """The ticket's target (``{kind, id, user}``), or ``None`` when the ticket
    is missing, malformed, tampered with, expired, of another kind, or minted
    for a user who no longer exists (checked only in ``multi`` mode, where a
    "user" is a real account rather than the single-operator or shared-token
    constant)."""
    payload = _verified_preview(ticket)
    if payload is None:
        return None
    return {"kind": payload["kind"], "id": payload["id"], "user": payload.get("user")}


def expires_at(ticket: str) -> Optional[float]:
    """When a valid preview ticket stops working (epoch seconds), else None."""
    payload = _verified_preview(ticket)
    return payload["exp"] if payload else None


def renew(ticket: str, *, force: bool = False) -> Optional[str]:
    """A fresh ticket for the same target and user, or ``None``.

    ``None`` when the presented ticket is not a valid preview ticket, and,
    unless ``force``, while it still has more than half its life left: the
    proxy calls this on every request and should only hand out a
    replacement once one is worth having. The old ticket stays valid until
    its own expiry, so nothing already loaded under it breaks.
    """
    payload = _verified_preview(ticket)
    if payload is None:
        return None
    try:
        ttl = int(payload.get("ttl") or DEFAULT_TTL_SECONDS)
    except (TypeError, ValueError):
        ttl = DEFAULT_TTL_SECONDS
    if not force and payload["exp"] - time.time() > ttl / 2:
        return None
    return mint({"kind": payload["kind"], "id": payload["id"]},
                principal_id=payload.get("user") or "", ttl_seconds=ttl)


# ── auth tickets ─────────────────────────────────────────────────────────────

#: nonce -> expiry of every auth ticket this process has accepted.
_consumed: Dict[str, float] = {}
_consumed_lock = threading.Lock()


def mint_auth(principal: Any, ttl_seconds: int = AUTH_TTL_SECONDS) -> str:
    """A one-time ticket standing for ``principal`` (a ``common.auth.Principal``).

    Carries the principal's id, its kind (``user``, ``token``, ``service``,
    ``local``), how it originally authenticated and, for a scoped API key,
    the workspaces it may reach, so a ticket never acts wider than the
    credential that minted it.
    """
    scope = getattr(principal, "scope", None)
    return _sign({
        "kind": _AUTH_KIND,
        "user": principal.id,
        "pkind": getattr(principal, "kind", "user") or "user",
        "via": getattr(principal, "via", "") or "",
        "scope": list(scope) if scope is not None else None,
        "nonce": secrets.token_urlsafe(12),
        "exp": time.time() + max(1, int(ttl_seconds)),
    })


def _consume(nonce: str, expiry: float) -> bool:
    """Mark ``nonce`` used; False when it already was."""
    now = time.time()
    with _consumed_lock:
        for key in [k for k, exp in _consumed.items() if exp < now]:
            del _consumed[key]
        if nonce in _consumed:
            return False
        _consumed[nonce] = expiry
        return True


def verify_auth(ticket: str) -> Optional[Dict[str, Any]]:
    """``{user, kind, via, scope, exp}`` for a valid, unused auth ticket, or
    ``None``. Verifying consumes it: a second presentation (to this process)
    fails."""
    payload = _decode(ticket)
    if payload is None or payload.get("kind") != _AUTH_KIND:
        return None
    user, nonce = payload.get("user"), payload.get("nonce")
    if not user or not nonce:
        return None
    if not _consume(str(nonce), payload["exp"]):
        return None
    scope = payload.get("scope")
    return {"user": user, "kind": payload.get("pkind") or "user",
            "via": payload.get("via") or "", "scope": scope, "exp": payload["exp"]}


# ── terminal tickets ─────────────────────────────────────────────────────────

def mint_terminal(principal: Any, *, target_kind: str, target_id: str,
                  session_id: Optional[str] = None,
                  ttl_seconds: int = TERMINAL_TTL_SECONDS) -> str:
    """A one-time ticket that opens (or, with ``session_id``, resumes) a
    terminal on one target for ``principal``. Carries the same principal
    fields as :func:`mint_auth`, so the socket resolves to who minted it."""
    scope = getattr(principal, "scope", None)
    return _sign({
        "kind": _TERMINAL_KIND,
        "target": f"{target_kind}:{target_id}",
        "session": session_id or None,
        "user": principal.id,
        "pkind": getattr(principal, "kind", "user") or "user",
        "via": getattr(principal, "via", "") or "",
        "scope": list(scope) if scope is not None else None,
        "nonce": secrets.token_urlsafe(12),
        "exp": time.time() + max(1, int(ttl_seconds)),
    })


def verify_terminal(ticket: str, *, target_kind: str, target_id: str) -> Optional[Dict[str, Any]]:
    """``{user, kind, via, scope, session, exp}`` for a valid, unused terminal
    ticket minted for exactly this target, or ``None``. A ticket for another
    target is refused without being spent; a matching one is spent here."""
    payload = _decode(ticket)
    if payload is None or payload.get("kind") != _TERMINAL_KIND:
        return None
    if payload.get("target") != f"{target_kind}:{target_id}":
        return None
    user, nonce = payload.get("user"), payload.get("nonce")
    if not user or not nonce:
        return None
    if payload.get("pkind") == "user" and _user_gone(user):
        return None
    if not _consume(str(nonce), payload["exp"]):
        return None
    return {"user": user, "kind": payload.get("pkind") or "user",
            "via": payload.get("via") or "", "scope": payload.get("scope"),
            "session": payload.get("session") or None, "exp": payload["exp"]}


__all__ = ["AUTH_TTL_SECONDS", "DEFAULT_TTL_SECONDS", "TERMINAL_TTL_SECONDS", "expires_at",
           "mint", "mint_auth", "mint_terminal", "renew", "verify", "verify_auth",
           "verify_terminal"]
