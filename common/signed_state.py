"""
A signed, short-lived browser round trip: the public URL of the hub, whether
its cookies are Secure, a safe post-login path, and an HMAC-signed cookie
payload.

Single sign-on (``ee/oidc.py``) and the GitHub user connection
(``dashboard/backend/routes/github_app.py``) both send the browser to another
site and need what they started to survive the trip back. Each puts it in one
cookie signed here, rather than in a server-side table: nothing to clean up,
and any replica that shares the signing key can finish a flow another one
started. A payload carries its own ``exp`` and, where two flows could be
confused, its own ``purpose``.

The key is ``AGENTS_HUB_SECRET_KEY`` when set, else the OIDC client secret,
else the process's service credential (``identity.service_token()``), in that
order, so a multi-replica deployment has something shared to sign with and a
single process always has something. The derivation is unchanged from when
this lived in the OIDC module, so cookies already issued stay valid.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Any, Dict, Optional


class StateError(Exception):
    """A round-trip cookie that is missing, forged or expired. ``code`` is
    what the page the browser lands on is told."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def _settings():
    from common.config import settings
    return settings


# ── the public URL and cookies ───────────────────────────────────────────────

def public_url(request) -> str:
    """Where the browser reaches this hub: ``AUTH_PUBLIC_URL``, else derived
    from the request, honouring ``X-Forwarded-Proto`` and ``X-Forwarded-Host``
    from a reverse proxy."""
    configured = (getattr(_settings(), "auth_public_url", "") or "").strip().rstrip("/")
    if configured:
        return configured
    headers = request.headers
    proto = (headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    host = (headers.get("x-forwarded-host") or "").split(",")[0].strip()
    proto = proto or request.url.scheme
    host = host or headers.get("host") or request.url.netloc
    return f"{proto}://{host}".rstrip("/")


def cookie_secure(request) -> bool:
    """Whether a round-trip cookie is marked Secure (``AUTH_COOKIE_SECURE``)."""
    raw = str(getattr(_settings(), "auth_cookie_secure", "auto") or "auto").strip().lower()
    if raw in ("true", "1", "yes", "on"):
        return True
    if raw in ("false", "0", "no", "off"):
        return False
    return public_url(request).startswith("https://")


def safe_next(value: Optional[str]) -> str:
    """A post-login destination: a path on this hub, never another site.

    Raises ValueError for anything with a scheme, a host (``//evil``, and the
    ``/\\evil`` spelling browsers read the same way) or no leading slash.
    """
    value = (value or "").strip()
    if not value:
        return "/"
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme or parsed.netloc or not value.startswith("/")
            or value.startswith("//") or value.startswith("/\\") or "\\" in value[:2]
            or any(ord(c) < 32 for c in value)):
        raise ValueError("next must be a relative path on this hub")
    return value


# ── the signed payload ───────────────────────────────────────────────────────

def _signing_key() -> bytes:
    from common import identity
    settings = _settings()
    material = ((getattr(settings, "secret_key", "") or "").strip()
                or (getattr(settings, "auth_oidc_client_secret", "") or "").strip()
                or identity.service_token())
    return hashlib.sha256(b"agents-hub-oidc-state:" + material.encode("utf-8")).digest()


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_state(payload: Dict[str, Any]) -> str:
    body = b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    mac = hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{b64(mac)}"


def read_state(value: Optional[str]) -> Dict[str, Any]:
    """The cookie's payload, or StateError("bad_state") when it is missing,
    forged or expired."""
    if not value or "." not in value:
        raise StateError("bad_state", "no sign-in in progress")
    body, mac = value.rsplit(".", 1)
    expected = hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).digest()
    try:
        presented = unb64(mac)
    except ValueError:
        raise StateError("bad_state", "malformed state cookie")
    if not hmac.compare_digest(expected, presented):
        raise StateError("bad_state", "state cookie signature mismatch")
    try:
        payload = json.loads(unb64(body).decode("utf-8"))
    except ValueError:
        raise StateError("bad_state", "malformed state cookie")
    if float(payload.get("exp", 0)) < time.time():
        raise StateError("bad_state", "the sign-in took too long")
    return payload


__all__ = ["StateError", "public_url", "cookie_secure", "safe_next", "sign_state", "read_state"]
