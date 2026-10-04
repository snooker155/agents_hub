"""
The consent link: ``/consent/<token>``, where the token is HMAC-signed and
binds the request id, the end user it was made for and its expiry.

The signature is what lets the page answer without a login: a token names
exactly one request and cannot be edited into another one, and the principal
inside it must match the request's row, so a request id guessed or copied out
of a log is not enough on its own. Single use is the row's job (its status
leaves ``pending``/``started`` once the round trip ends), not the token's.

The key is derived from ``common.preview_tickets``' per-installation secret,
the way widgets/visitor.py derives its own, under a label of its own so a
consent token can never be presented as a visitor token, a preview ticket or
the other way round.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

_KIND = "consent"
_MAX_TOKEN_CHARS = 1024


def _key() -> bytes:
    from common import preview_tickets
    return hmac.new(preview_tickets._signing_key(), b"agents-hub-consent-link",
                    hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mint(request_id: str, principal: str, expires_at: int) -> str:
    """The token for one request, valid until ``expires_at`` (epoch seconds)."""
    payload = {"k": _KIND, "r": str(request_id), "p": str(principal), "exp": int(expires_at)}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    mac = hmac.new(_key(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"


def verify(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """``{request_id, principal, exp}`` for a valid, unexpired token, else None."""
    if not token or "." not in token or len(token) > _MAX_TOKEN_CHARS:
        return None
    body, _, mac_part = token.rpartition(".")
    try:
        presented = _unb64(mac_part)
        expected = hmac.new(_key(), body.encode("ascii"), hashlib.sha256).digest()
    except (ValueError, UnicodeEncodeError):
        return None
    if not hmac.compare_digest(expected, presented):
        return None
    try:
        payload = json.loads(_unb64(body).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("k") != _KIND:
        return None
    try:
        exp = int(payload.get("exp", 0))
    except (TypeError, ValueError):
        return None
    if exp < time.time():
        return None
    request_id, principal = payload.get("r"), payload.get("p")
    if not isinstance(request_id, str) or not isinstance(principal, str):
        return None
    return {"request_id": request_id, "principal": principal, "exp": exp}


__all__ = ["mint", "verify"]
