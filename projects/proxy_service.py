"""The project backend proxy: base-URL validation and the forwarded request.

Split out of ``routes/projects.py`` so the SSRF guard and the container host
rewrite are testable without going through the HTTP layer.

Full protection, not the two-hostname allowlist this used to be: the host is
resolved and *every* address it maps to must be public (``common.ssrf``, the
same check ``tools.web.fetch_url`` uses), the connection is pinned to the
address that check approved so a DNS answer that changes afterward cannot
redirect it (DNS rebinding), and a redirect is followed manually, one hop at a
time, re-running the whole check against the new target rather than trusting
where the backend pointed. See ``validated_api_base_url`` for the one
deliberate exception, project backends that legitimately run on the
operator's own machine.
"""
from __future__ import annotations

import ipaddress
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import httpx

from common.hostnet import host_service_url
from common.ssrf import is_public_address, resolved_addresses
from projects.errors import ServiceError

# Checked before the general resolve-based guard below so the cloud metadata
# endpoint, by far the most common SSRF target, gets its own clear reason
# rather than the generic "resolves to a non-public address" one. It is also
# refused this way even when the environment cannot resolve the hostname at
# all (metadata.google.internal only answers from inside GCP).
_BLOCKED_API_HOSTNAMES = {"metadata.google.internal"}
_METADATA_NETWORK = ipaddress.ip_network("169.254.0.0/16")

# A project backend legitimately runs on the operator's own machine: a
# host-run backend, where "localhost" already means the right thing, or a
# containerized one reached through common.hostnet.host_service_url's rewrite
# onto the Docker host gateway (without that rewrite, a containerized
# dashboard's own "localhost" would mean the container, not the host). The
# public-address requirement below is waived for exactly these hosts, and
# nothing else: an operator pointing a project at their own machine is not
# the thing this guard exists to stop.
_LOCAL_MACHINE_HOSTS = {"localhost", "127.0.0.1", "host.docker.internal"}

#: Redirect hops to follow before giving up. Mirrors tools.web's own default
#: (settings.web_fetch_max_redirects); this module reads the same setting so
#: an operator has one knob for both, and falls back to this literal only if
#: that setting somehow is not there.
_DEFAULT_MAX_REDIRECTS = 5


def _is_local_machine_host(host: str) -> bool:
    return (host or "").lower() in _LOCAL_MACHINE_HOSTS


def _max_redirects() -> int:
    try:
        from common.config import settings
        return max(0, int(settings.web_fetch_max_redirects))
    except Exception:
        return _DEFAULT_MAX_REDIRECTS


def _validate_and_resolve(base: str) -> Tuple[str, str, Optional[str]]:
    """Validate ``base`` (or a full redirect target) and resolve its host once.

    Returns ``(rewritten, host, pinned_ip)``. ``rewritten`` is ``base`` with
    its host rewritten by ``host_service_url`` where that applies; anything
    else about the string (path, query) is untouched. ``pinned_ip`` is the
    single address the connection should actually be made to, resolved here
    and reused rather than looked up again right before the request, so the
    DNS answer this function used to decide "this host is safe" is also the
    address the connection reaches. It is ``None`` for the local-machine
    exemption, where the target is a name meant to be resolved normally
    (``host.docker.internal``, or an untouched ``localhost``) rather than
    something a hostile DNS answer could rebind.

    Raises ``ValueError`` on a rejected URL.
    """
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("base_url must use the http or https scheme")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("base_url has no host")

    if host in _BLOCKED_API_HOSTNAMES:
        raise ValueError("Requests to cloud metadata addresses are not allowed")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and literal in _METADATA_NETWORK:
        raise ValueError("Requests to cloud metadata addresses are not allowed")

    rewritten = host_service_url(base)
    rewritten_host = (urlparse(rewritten).hostname or "").lower()
    if _is_local_machine_host(host) or rewritten_host != host:
        return rewritten, rewritten_host, None

    addrs = resolved_addresses(host)
    if not addrs:
        raise ValueError(f"could not resolve host {host!r}")
    for addr in addrs:
        if not is_public_address(addr):
            raise ValueError(
                f"host {host!r} resolves to non-public address {addr}: "
                "internal, loopback and link-local targets are blocked"
            )
    return rewritten, host, addrs[0]


def validated_api_base_url(base: str) -> str:
    """Validate a proxied backend base URL and rewrite its host for containers.

    The plain validator: scheme, the metadata endpoint, and a full resolve
    check on the host (see :func:`_validate_and_resolve`), then the same
    container host rewrite every outbound call in this codebase goes through.
    Raises ``ValueError`` on a rejected URL: this is the one function here
    that raises ``ValueError`` rather than ``ServiceError``, since it is also
    meant to be callable as a plain validator outside the proxy route.

    ``proxy_api_request`` calls :func:`_validate_and_resolve` directly instead
    of this, so it can reuse the same resolution for connection pinning rather
    than looking the host up a second time.
    """
    rewritten, _host, _pin = _validate_and_resolve(base)
    return rewritten


class _PinnedTransport(httpx.AsyncHTTPTransport):
    """Connects to a fixed IP address regardless of what the request's host
    resolves to at connect time.

    This is the half of the SSRF defense that a validate-then-request call
    pair cannot provide on its own: :func:`_validate_and_resolve` approves one
    specific address, and this makes sure every byte of the request actually
    goes there, never to a fresh DNS answer for the same name looked up later.
    Without it, a host that answered a public address at check time and a
    private one moments later (DNS rebinding) would sail straight through
    between the check and the connection. The original hostname still goes
    out as the ``Host`` header and the TLS SNI name, so virtual hosting and
    certificate validation both still see the name the operator configured,
    only the actual socket destination is pinned.
    """

    def __init__(self, pinned_ip: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._pinned_ip = pinned_ip

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        original_host = request.url.host
        request.url = request.url.copy_with(host=self._pinned_ip)
        request.headers.setdefault("Host", original_host)
        request.extensions = {**request.extensions, "sni_hostname": original_host}
        return await super().handle_async_request(request)


async def proxy_api_request(
    base: str, method: str, path: str,
    headers: Optional[Dict[str, str]] = None, body: Any = None,
) -> Dict[str, Any]:
    """Validate ``base``, then forward one HTTP request to it.

    Redirects are followed manually, up to :func:`_max_redirects` hops: each
    one is re-validated and re-resolved from scratch exactly like a
    user-supplied base URL would be, since a redirect is exactly how a
    trusted-looking host hands a request to a completely different one.
    ``httpx``'s own redirect-following is never used for that reason:
    letting it happen automatically would validate only the first hop.

    Raises ``ServiceError`` for every failure mode the route used to turn
    into an ``HTTPException`` itself: a rejected base URL or redirect target
    (400), a backend that refused the connection (502), a timeout (504), or
    anything else (500).
    """
    try:
        base, _host, pinned_ip = _validate_and_resolve(base)
    except ValueError as e:
        raise ServiceError(400, str(e))

    current = f"{base}{path}"
    method_u = method.upper()
    hops = _max_redirects()

    try:
        for attempt in range(hops + 1):
            transport = _PinnedTransport(pinned_ip) if pinned_ip else None
            async with httpx.AsyncClient(timeout=30.0, transport=transport) as client:
                response = await client.request(
                    method=method_u,
                    url=current,
                    headers=headers or {},
                    json=body if method_u not in ("GET", "DELETE") else None,
                )

            # httpx.Response.is_redirect covers the standard 3xx redirect
            # codes; checked by hand against status_code so a stubbed
            # response in a test does not need to implement the property.
            if response.status_code not in (301, 302, 303, 307, 308):
                break
            location = response.headers.get("location")
            if not location or attempt >= hops:
                break
            current = str(httpx.URL(current).join(location))
            try:
                current, _host, pinned_ip = _validate_and_resolve(current)
            except ValueError as e:
                raise ServiceError(400, f"redirected to a refused target: {e}")

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
        raise ServiceError(502, f"Cannot connect to backend at {current}")
    except httpx.TimeoutException:
        raise ServiceError(504, "Request to backend timed out")
    except ServiceError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ServiceError(500, str(e))


__all__ = ["validated_api_base_url", "proxy_api_request"]
