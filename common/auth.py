"""
Optional API-token authorization.

A single pure predicate, shared by the FastAPI middleware and its tests, decides
whether a request is authorized. Authorization is off by default (no token
configured) so the local-only workflow is unchanged; when a token is set every
``/api`` request must present it. The token may arrive as an
``Authorization: Bearer`` header, an ``X-Api-Token`` header, or a ``?token=``
query parameter — the query form lets the browser's EventSource (which cannot
set headers) authenticate the SSE stream.
"""
from __future__ import annotations

from typing import Optional


def extract_bearer(auth_header: Optional[str]) -> Optional[str]:
    """Return the token from an ``Authorization: Bearer <token>`` header, else None."""
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return None


def is_authorized(
    *,
    configured_token: str,
    method: str,
    path: str,
    auth_header: Optional[str] = None,
    x_api_token: Optional[str] = None,
    query_token: Optional[str] = None,
) -> bool:
    """True when a request may proceed.

    Open (returns True) when no token is configured, for CORS preflight
    (``OPTIONS``), and for non-``/api`` paths. Otherwise the presented token
    (header or query) must match exactly.
    """
    if not configured_token:
        return True
    if method == "OPTIONS":
        return True
    if not path.startswith("/api"):
        return True
    presented = extract_bearer(auth_header) or x_api_token or query_token
    return presented == configured_token
