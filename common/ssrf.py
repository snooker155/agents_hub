"""
Shared SSRF guard: resolve a hostname and require every address it maps to be
public.

``tools/web.py`` (the ``fetch_url`` tool) and ``projects/proxy_service.py``
(the project backend proxy) both need the same answer to "is this host safe
to connect to": not the cloud metadata endpoint, not loopback, not a private
or link-local range, on *every* address a name resolves to, not just the
first. This check was written once, in ``tools/web.py``, and lives here now so
the proxy uses the identical logic instead of the two-hostname allowlist it
had before (see ``projects.proxy_service.validated_api_base_url``'s old
docstring). ``tools/web.py`` imports its public names back from here, so
nothing that already depends on them (tests, callers) has to change.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import List, Tuple


def is_public_address(ip_str: str) -> bool:
    """True only for globally-routable addresses.

    Everything else is refused: loopback (127.0.0.0/8, ::1), private ranges
    (RFC1918, and RFC4193's fc00::/7), link-local (RFC3927 and fe80::/10,
    which is also where the cloud metadata endpoint 169.254.169.254 lives),
    an IPv4-mapped IPv6 address of any of the above (``::ffff:10.0.0.1`` etc,
    since Python's ``ipaddress`` derives an IPv4-mapped address's privacy from
    the address it maps to), multicast, reserved and unspecified
    (``0.0.0.0``, ``::``).
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    )


def resolved_addresses(host: str) -> List[str]:
    """Every address ``host`` resolves to, deduplicated and sorted.

    Empty when the name does not resolve at all; the caller decides what that
    means (:func:`resolve_and_check` treats it as a refusal). Used on its own
    by a caller that needs the concrete address to connect to, e.g. to pin a
    connection against DNS rebinding between the check and the request.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    return sorted({info[4][0] for info in infos})


def resolve_and_check(host: str) -> Tuple[bool, str]:
    """Resolve ``host`` and require *every* address it maps to be public.

    Checking every answer, not just the first, closes the DNS-rebinding gap
    where a name resolves to one public and one private address.
    """
    if not host:
        return False, "URL has no host"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        return False, f"could not resolve host {host!r}: {e}"
    addrs = {info[4][0] for info in infos}
    if not addrs:
        return False, f"could not resolve host {host!r}"
    for addr in addrs:
        if not is_public_address(addr):
            return False, (
                f"host {host!r} resolves to non-public address {addr}: "
                "internal, loopback and link-local targets are blocked"
            )
    return True, ""


__all__ = ["is_public_address", "resolved_addresses", "resolve_and_check"]
