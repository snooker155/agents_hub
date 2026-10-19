"""Short-lived, signed, one-time terminal tickets.

Minted by ``POST /api/terminal/{kind}/{id}/ticket`` (see
``dashboard/backend/routes/terminal.py`` and docs/terminal.md): the one-time
credential the terminal WebSocket carries in its query string, since a socket
cannot set a header. It names the one container it may open a shell in (a run
or a resident instance) and, on a reconnect, the session it resumes.

A ticket is an HMAC-signed, URL-safe token valid for a minute and good for one
use: the nonce of a verified ticket goes into a per-process consumed set
(pruned as entries expire), so a URL copied out of a log or a proxy is dead by
the time anyone reads it. :func:`verify_terminal` checks the target in the
socket's path against the one signed in, so a ticket opens nothing else.
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

_TERMINAL_KIND = "terminal"

#: A terminal ticket only bridges the call that mints it and the socket that
#: spends it.
TERMINAL_TTL_SECONDS = 60

_SECRET_FILE = AGENTS_HUB_ROOT / "ticket_secret"
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
            log.debug("could not chmod ticket_secret to 0600", exc_info=True)
    except OSError:
        # Read-only state directory or similar: keep the secret in memory for
        # this process. A ticket minted here simply stops verifying once the
        # process restarts, which reads to the caller exactly like an expired
        # ticket does.
        log.debug("could not persist ticket_secret, keeping it in memory only",
                  exc_info=True)
    _cached_secret = generated
    return _cached_secret


def _signing_key() -> bytes:
    """The per-installation secret that signs terminal tickets.

    Reuses ``AGENTS_HUB_SECRET_KEY`` (``settings.secret_key``) when one is
    configured: the same persistent, per-installation secret
    ``common/secrets.py``'s encryption at rest uses, so a deployment that has set it
    once gets ticket signing "for free" and does not accumulate one more
    secret to manage. Otherwise a dedicated secret is generated once and kept
    under the state directory.
    """
    from common.config import settings
    material = (getattr(settings, "secret_key", "") or "").strip()
    if material:
        return hashlib.sha256(b"agents-hub-terminal-ticket:" + material.encode("utf-8")).digest()
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


# ── one-time tickets ─────────────────────────────────────────────────────────

#: nonce -> expiry of every one-time ticket this process has accepted.
_consumed: Dict[str, float] = {}
_consumed_lock = threading.Lock()


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


# ── terminal tickets ─────────────────────────────────────────────────────────

def mint_terminal(principal: Any, *, target_kind: str, target_id: str,
                  session_id: Optional[str] = None,
                  ttl_seconds: int = TERMINAL_TTL_SECONDS) -> str:
    """A one-time ticket that opens (or, with ``session_id``, resumes) a
    terminal on one target for ``principal``. Carries the principal's fields,
    so the socket resolves to who minted it."""
    return _sign({
        "kind": _TERMINAL_KIND,
        "target": f"{target_kind}:{target_id}",
        "session": session_id or None,
        "user": principal.id,
        "nonce": secrets.token_urlsafe(12),
        "exp": time.time() + max(1, int(ttl_seconds)),
    })


def verify_terminal(ticket: str, *, target_kind: str, target_id: str) -> Optional[Dict[str, Any]]:
    """``{user, session, exp}`` for a valid, unused terminal
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
    if not _consume(str(nonce), payload["exp"]):
        return None
    return {"user": user, "session": payload.get("session") or None, "exp": payload["exp"]}


__all__ = ["TERMINAL_TTL_SECONDS", "mint_terminal", "verify_terminal"]
