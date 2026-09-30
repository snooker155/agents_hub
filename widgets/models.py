"""
The widget record and what a site owner may set on it.

Everything a visitor's browser ends up rendering or enforcing is validated
here, once, rather than trusted from the form: an origin is an exact
``scheme://host[:port]`` (``https`` only, except loopback for local
development), the accent is one of a few names the script maps to its own CSS
variables (never a free colour a page could use to inject CSS), the language
is one the script carries strings for, and every limit sits inside bounds the
chat pipeline itself can honour (an attachment is never larger than the 5 MB
``chat.attachments`` accepts).
"""
from __future__ import annotations

import ipaddress
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

#: The accents the script knows. Each maps to a pair of CSS variables (light
#: and dark) inside the widget's shadow root; see ``ACCENTS`` in widget.js.
ACCENTS = ("navy", "blue", "teal", "green", "amber", "rose", "slate")
DEFAULT_ACCENT = "navy"

#: ``auto`` follows the visitor's browser among the three the script carries.
LANGUAGES = ("auto", "en", "ru", "de")

#: The per-file ceiling of ``chat.attachments.materialize_attachments``. A
#: widget may allow less, never more: a larger file would pass the widget's
#: own check and then fail inside the pipeline with an error the visitor
#: cannot act on.
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_ATTACHMENTS = 5

#: A visitor's message, in characters. Long enough for a pasted paragraph or
#: an error log, short enough that one message cannot carry a book.
MAX_MESSAGE_CHARS = 8000

#: Texts shown in the widget.
MAX_NAME_CHARS = 120
MAX_TITLE_CHARS = 80
MAX_GREETING_CHARS = 1000
MAX_PLACEHOLDER_CHARS = 120
MAX_ORIGINS = 50

#: Hosts that may be served over plain http: a site being developed on the
#: owner's own machine.
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")


class WidgetValidationError(ValueError):
    """A value a site owner has to change. Routes answer it with a 400."""


class WidgetLimits(BaseModel):
    """What one visitor, one message and one day may cost.

    ``tokens_per_day`` is the widget's whole budget across every visitor,
    counted from the turns it served since 00:00 UTC; 0 means no cap.
    ``attachment_max_bytes`` of 0 turns attachments off.
    """
    messages_per_minute: int = Field(default=6, ge=1, le=120)
    attachment_max_bytes: int = Field(default=2 * 1024 * 1024, ge=0, le=MAX_ATTACHMENT_BYTES)
    max_attachments: int = Field(default=3, ge=0, le=MAX_ATTACHMENTS)
    tokens_per_day: int = Field(default=200_000, ge=0, le=100_000_000)


#: Bounds for the form, so the page renders the same limits the model checks.
LIMIT_BOUNDS: Dict[str, Dict[str, int]] = {
    name: {
        "min": next((m.ge for m in field.metadata if hasattr(m, "ge")), 0),
        "max": next((m.le for m in field.metadata if hasattr(m, "le")), 0),
        "default": field.default,
    }
    for name, field in WidgetLimits.model_fields.items()
}


def _is_loopback(host: str) -> bool:
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def normalize_origin(value: Any, *, allow_wildcard: bool = False) -> str:
    """One allowed origin in the exact form a browser sends in ``Origin``.

    ``https://Example.com:443/`` becomes ``https://example.com``: scheme and
    host lower case, the default port dropped, no path, no trailing slash.
    Refused: anything with a path, query, fragment or credentials, a scheme
    other than http(s), plain http to anything but loopback, and ``*``
    unless ``allow_wildcard`` (outside ``multi`` mode, see
    :func:`validate_origins`).
    """
    raw = str(value or "").strip()
    if not raw:
        raise WidgetValidationError("an allowed origin is empty")
    if raw == "*":
        if allow_wildcard:
            return "*"
        raise WidgetValidationError(
            "'*' is not allowed in multi mode: list the exact origins that embed the widget")
    if "*" in raw:
        raise WidgetValidationError(f"'{raw}': wildcards inside an origin are not supported")
    try:
        parts = urlsplit(raw)
    except ValueError:
        raise WidgetValidationError(f"'{raw}' is not an origin")
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise WidgetValidationError(f"'{raw}': an origin starts with https:// (or http:// for localhost)")
    if parts.username or parts.password:
        raise WidgetValidationError(f"'{raw}': an origin carries no user name or password")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise WidgetValidationError(
            f"'{raw}': an origin is scheme, host and port only, with no path")
    host = (parts.hostname or "").lower()
    if not host:
        raise WidgetValidationError(f"'{raw}' names no host")
    try:
        port = parts.port
    except ValueError:
        raise WidgetValidationError(f"'{raw}' has an invalid port")
    display_host = f"[{host}]" if ":" in host else host
    if scheme == "http" and not _is_loopback(host):
        raise WidgetValidationError(
            f"'{raw}': plain http is only allowed for localhost; use https")
    default_port = 443 if scheme == "https" else 80
    if port and port != default_port:
        return f"{scheme}://{display_host}:{port}"
    return f"{scheme}://{display_host}"


def validate_origins(values: Any, *, multi_mode: bool) -> List[str]:
    """The allowed origins, normalized and deduplicated, order kept."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [v for v in values.replace(",", "\n").splitlines()]
    if not isinstance(values, (list, tuple)):
        raise WidgetValidationError("allowed_origins must be a list of origins")
    out: List[str] = []
    for value in values:
        if not str(value or "").strip():
            continue
        origin = normalize_origin(value, allow_wildcard=not multi_mode)
        if origin not in out:
            out.append(origin)
    if len(out) > MAX_ORIGINS:
        raise WidgetValidationError(f"at most {MAX_ORIGINS} allowed origins")
    return out


def validate_accent(value: Any) -> str:
    accent = str(value or DEFAULT_ACCENT).strip().lower()
    if accent not in ACCENTS:
        raise WidgetValidationError(f"accent must be one of: {', '.join(ACCENTS)}")
    return accent


def validate_language(value: Any) -> str:
    language = str(value or "auto").strip().lower()
    if language not in LANGUAGES:
        raise WidgetValidationError(f"language must be one of: {', '.join(LANGUAGES)}")
    return language


def validate_text(value: Any, *, field: str, limit: int, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise WidgetValidationError(f"{field} is required")
    if len(text) > limit:
        raise WidgetValidationError(f"{field} is longer than {limit} characters")
    return text


def validate_limits(value: Any, base: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """Limits merged over ``base`` (the current ones on an update) and checked."""
    merged: Dict[str, Any] = dict(base or {})
    if value is not None:
        if not isinstance(value, dict):
            raise WidgetValidationError("limits must be an object")
        unknown = set(value) - set(WidgetLimits.model_fields)
        if unknown:
            raise WidgetValidationError(f"unknown limits: {', '.join(sorted(unknown))}")
        merged.update({k: v for k, v in value.items() if v is not None})
    try:
        return WidgetLimits.model_validate(merged).model_dump()
    except ValueError as exc:
        errors = getattr(exc, "errors", lambda: [])()
        if errors:
            first = errors[0]
            where = ".".join(str(p) for p in first.get("loc", ()))
            raise WidgetValidationError(f"limits.{where}: {first.get('msg')}")
        raise WidgetValidationError(f"invalid limits: {exc}")


__all__ = [
    "ACCENTS", "DEFAULT_ACCENT", "LANGUAGES", "LIMIT_BOUNDS", "MAX_ATTACHMENTS",
    "MAX_ATTACHMENT_BYTES", "MAX_GREETING_CHARS", "MAX_MESSAGE_CHARS", "MAX_NAME_CHARS",
    "MAX_PLACEHOLDER_CHARS", "MAX_TITLE_CHARS", "WidgetLimits", "WidgetValidationError",
    "normalize_origin", "validate_accent", "validate_language", "validate_limits",
    "validate_origins", "validate_text",
]
