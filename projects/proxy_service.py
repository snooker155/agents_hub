"""The project backend proxy: base-URL validation and the forwarded request.

Split out of ``routes/projects.py`` so the allowlist (scheme + cloud metadata
address) and the container host rewrite are testable without going through
the HTTP layer. Deliberately minimal, not full SSRF prevention — see
``validated_api_base_url``.
"""
from __future__ import annotations

import ipaddress
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from common.hostnet import host_service_url
from projects.errors import ServiceError

_BLOCKED_API_HOSTNAMES = {"metadata.google.internal"}
_METADATA_NETWORK = ipaddress.ip_network("169.254.0.0/16")


def validated_api_base_url(base: str) -> str:
    """Validate a proxied backend base URL and rewrite its host for containers.

    Rejects non-http(s) schemes and the cloud metadata address/hostname, then
    runs the result through ``host_service_url()`` so a backend running inside
    a container reaches the Docker host's localhost the same way every other
    outbound call in this codebase does. Raises ``ValueError`` on a rejected
    URL — this is the one function here that raises ``ValueError`` rather than
    ``ServiceError``, since it is also meant to be callable as a plain
    validator outside the proxy route.
    """
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("base_url must use the http or https scheme")
    host = (parsed.hostname or "").lower()
    if host in _BLOCKED_API_HOSTNAMES:
        raise ValueError("Requests to cloud metadata addresses are not allowed")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addr = None
    if addr is not None and addr in _METADATA_NETWORK:
        raise ValueError("Requests to cloud metadata addresses are not allowed")
    return host_service_url(base)


async def proxy_api_request(
    base: str, method: str, path: str,
    headers: Optional[Dict[str, str]] = None, body: Any = None,
) -> Dict[str, Any]:
    """Validate ``base``, then forward one HTTP request to it.

    Raises ``ServiceError`` for every failure mode the route used to turn
    into an ``HTTPException`` itself: a rejected base URL (400), a backend
    that refused the connection (502), a timeout (504), or anything else
    (500).
    """
    try:
        base = validated_api_base_url(base)
    except ValueError as e:
        raise ServiceError(400, str(e))

    url = f"{base}{path}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method=method.upper(),
                url=url,
                headers=headers or {},
                json=body if method.upper() not in ("GET", "DELETE") else None,
            )
            try:
                resp_body = response.json()
            except Exception:
                resp_body = response.text
            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": resp_body,
            }
    except httpx.ConnectError:
        raise ServiceError(502, f"Cannot connect to backend at {base}")
    except httpx.TimeoutException:
        raise ServiceError(504, "Request to backend timed out")
    except ServiceError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ServiceError(500, str(e))


__all__ = ["validated_api_base_url", "proxy_api_request"]
