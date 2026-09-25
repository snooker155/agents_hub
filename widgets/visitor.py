"""
Anonymous visitors: a server-minted id in an HMAC-signed token.

A widget's visitor has no account and must not need one. What they do need is
a stable identity for their own threads, one that another visitor cannot
claim. So the server mints a random id and hands it back inside a signed
token ``{w: widget_id, v: visitor_id, exp}``; the script keeps the token in
``localStorage`` and presents it as ``X-Visitor-Token``. Nothing is stored
server side for a visitor: the signature is the proof, the widget id in it
stops a token from one widget being replayed against another, and the expiry
bounds a token copied out of a browser.

A visitor who keeps coming back keeps their id: minting with a still valid
token renews it (same id, new expiry), so only a visitor gone longer than
:data:`TOKEN_TTL_SECONDS` starts over with no threads.

The live preview on the Widgets page uses a second kind of token from the
same key, :func:`mint_preview`: minted by an editor, bound to one widget and
short lived. It is what lets the preview run from the hub's own origin,
which is not in the widget's allowed origins.

The signing key is derived from ``common.preview_tickets``' per-installation
secret (``AGENTS_HUB_SECRET_KEY`` when set, else the generated one under the
state directory), under a label of its own so a visitor token can never be
presented as a preview ticket or the other way round.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Dict, Optional

#: How long a visitor token lives without being renewed.
TOKEN_TTL_SECONDS = 30 * 24 * 3600
#: How long a preview ticket lives: one sitting at the Widgets page.
PREVIEW_TTL_SECONDS = 30 * 60

_VISITOR_KIND = "visitor"
_PREVIEW_KIND = "preview"
VISITOR_PREFIX = "vis_"


def _key() -> bytes:
    from common import preview_tickets
    return hmac.new(preview_tickets._signing_key(), b"agents-hub-widget-token",
                    hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: Dict[str, Any]) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    mac = hmac.new(_key(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"


def _verify(token: Optional[str], kind: str, widget_id: str) -> Optional[Dict[str, Any]]:
    if not token or "." not in token or len(token) > 1024:
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
    if not isinstance(payload, dict) or payload.get("k") != kind:
        return None
    if payload.get("w") != widget_id:
        return None
    try:
        if float(payload.get("exp", 0)) < time.time():
            return None
    except (TypeError, ValueError):
        return None
    return payload


def mint_visitor(widget_id: str, visitor_id: Optional[str] = None,
                 *, ttl_seconds: int = TOKEN_TTL_SECONDS) -> Dict[str, Any]:
    """``{visitor_token, visitor_id, expires_at}`` for a new visitor, or a
    renewed token for ``visitor_id``."""
    vid = visitor_id or (VISITOR_PREFIX + secrets.token_hex(12))
    exp = int(time.time()) + int(ttl_seconds)
    token = _sign({"k": _VISITOR_KIND, "w": widget_id, "v": vid, "exp": exp})
    return {"visitor_token": token, "visitor_id": vid, "expires_at": exp}


def verify_visitor(token: Optional[str], widget_id: str) -> Optional[str]:
    """The visitor id a valid token for this widget carries, else None."""
    payload = _verify(token, _VISITOR_KIND, widget_id)
    vid = payload.get("v") if payload else None
    return str(vid) if isinstance(vid, str) and vid.startswith(VISITOR_PREFIX) else None


def mint_preview(widget_id: str, *, minted_by: str,
                 ttl_seconds: int = PREVIEW_TTL_SECONDS) -> Dict[str, Any]:
    exp = int(time.time()) + int(ttl_seconds)
    ticket = _sign({"k": _PREVIEW_KIND, "w": widget_id, "u": str(minted_by or ""), "exp": exp})
    return {"ticket": ticket, "expires_at": exp}


def verify_preview(ticket: Optional[str], widget_id: str) -> bool:
    return _verify(ticket, _PREVIEW_KIND, widget_id) is not None


__all__ = ["PREVIEW_TTL_SECONDS", "TOKEN_TTL_SECONDS", "VISITOR_PREFIX", "mint_preview",
           "mint_visitor", "verify_preview", "verify_visitor"]
