"""Short-lived, signed tickets for the preview proxy (feature 7a).

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
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any, Dict, Optional

from common.paths import AGENTS_HUB_ROOT

log = logging.getLogger(__name__)

#: A preview link is meant for one sitting at the hub, not a bookmark.
DEFAULT_TTL_SECONDS = 3600

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
    ``common/oidc.py``'s sign-in state cookie and ``common/secrets.py``'s
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


def mint(target: Dict[str, Any], *, principal_id: str,
         ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Mint a signed, short-lived ticket for one preview target.

    ``target`` is ``{"kind": "container" | "project", "id": <name or project
    id>}``. The proxy resolves that id to an actual URL again on every
    request; nothing about the current URL is baked into the ticket.
    """
    payload = {
        "kind": target["kind"],
        "id": target["id"],
        "user": principal_id,
        "exp": time.time() + max(1, int(ttl_seconds)),
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    mac = hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64encode(mac)}"


def verify(ticket: str) -> Optional[Dict[str, Any]]:
    """The ticket's target (``{kind, id, user}``), or ``None`` when the ticket
    is missing, malformed, tampered with, expired, or minted for a user who no
    longer exists (checked only in ``multi`` mode, where a "user" is a real
    account rather than the single-operator or shared-token constant)."""
    if not ticket or "." not in ticket:
        return None
    body, _, mac_part = ticket.rpartition(".")
    try:
        presented_mac = _b64decode(mac_part)
    except ValueError:
        return None
    expected_mac = hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).digest()
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
    kind = payload.get("kind")
    target_id = payload.get("id")
    if kind not in ("container", "project") or not target_id:
        return None
    user = payload.get("user")
    if user:
        from common import identity
        if identity.current_mode() == identity.MULTI and identity.get_user(user) is None:
            return None
    return {"kind": kind, "id": target_id, "user": user}


__all__ = ["DEFAULT_TTL_SECONDS", "mint", "verify"]
