"""
URL policy for the browser service: the same answer ``fetch_url`` gives.

The hub checks every URL with ``tools.web.validate_url`` before it sends it
here, but the page itself then makes requests the hub never sees (images,
scripts, XHR, redirects, links the agent clicks). Every one of those goes
through :func:`check_url` in the service, so the policy holds for the whole
page and not only for the address the agent typed.

Two checks, in the order ``tools.web.validate_url`` runs them:

1. The domain policy handed over at session creation. Same semantics as
   ``tools.web.check_domain_policy``: the deny list always applies; the allow
   list applies only when ``allowlist_enabled`` is set, and an enabled but
   empty allow list refuses every host. Matching is ``_host_matches``: a
   pattern matches the host itself and any subdomain of it.
2. The private-network block: the host is resolved and *every* address it maps
   to must be public (no loopback, RFC1918, link-local, metadata, multicast,
   reserved or unspecified addresses).

The resolver is the hub's own ``common/ssrf.py``. The image build copies that
file in as ``hub_ssrf.py`` (see the Dockerfile), so the service runs the
identical code rather than a re-typed copy that could drift. When this module
is imported from the repository (the tests do that), ``hub_ssrf`` is not on
the path and the import falls back to ``common.ssrf``: same file, same code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple
from urllib.parse import urlparse

try:  # inside the image: common/ssrf.py copied in by the Dockerfile
    from hub_ssrf import resolve_and_check  # type: ignore[import-not-found]
except ImportError:  # imported from the repository (tests)
    from common.ssrf import resolve_and_check

# Schemes a routed request may use. ws/wss only reach this module through the
# WebSocket route; data:, blob: and about: never touch the network and are not
# routed at all.
NETWORK_SCHEMES = ("http", "https", "ws", "wss")


def host_matches(host: str, pattern: str) -> bool:
    """``example.com`` matches ``example.com`` and any subdomain of it.

    A copy of ``tools.web._host_matches``, kept byte-for-byte equivalent; the
    test suite runs both over the same cases.
    """
    host = (host or "").lower().lstrip(".")
    pattern = (pattern or "").lower().lstrip(".")
    if not host or not pattern:
        return False
    return host == pattern or host.endswith("." + pattern)


def _clean(items: Optional[Iterable[Any]]) -> Tuple[str, ...]:
    return tuple(str(i).strip().lower() for i in (items or ()) if str(i).strip())


@dataclass(frozen=True)
class Policy:
    """The domain policy one browser session enforces."""
    deny_domains: Tuple[str, ...] = field(default_factory=tuple)
    allow_domains: Tuple[str, ...] = field(default_factory=tuple)
    allowlist_enabled: bool = False

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "Policy":
        data = data or {}
        return cls(
            deny_domains=_clean(data.get("deny_domains")),
            allow_domains=_clean(data.get("allow_domains")),
            allowlist_enabled=bool(data.get("allowlist_enabled")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "deny_domains": list(self.deny_domains),
            "allow_domains": list(self.allow_domains),
            "allowlist_enabled": self.allowlist_enabled,
        }


def check_domain(url: str, policy: Policy) -> Tuple[bool, str]:
    """Deny list first, then the opt-in allow list. No DNS."""
    host = (urlparse(url).hostname or "").lower()
    for pattern in policy.deny_domains:
        if host_matches(host, pattern):
            return False, f"host {host!r} is on the deny list"
    if not policy.allowlist_enabled:
        return True, ""
    if not policy.allow_domains:
        return False, "the domain allowlist is enabled but empty, no host may be fetched"
    for pattern in policy.allow_domains:
        if host_matches(host, pattern):
            return True, ""
    return False, f"host {host!r} is not on the domain allowlist"


def check_url(url: str, policy: Policy, *, resolve: bool = True) -> Tuple[bool, str]:
    """Full check for one request the page wants to make.

    ``resolve=False`` skips the DNS step; only the tests use it.
    """
    try:
        parsed = urlparse(str(url).strip())
    except Exception:
        return False, "malformed URL"
    if parsed.scheme not in NETWORK_SCHEMES:
        return False, f"scheme {parsed.scheme!r} is not allowed"
    ok, reason = check_domain(url, policy)
    if not ok:
        return False, reason
    if not resolve:
        return True, ""
    return resolve_and_check(parsed.hostname or "")


__all__ = ["Policy", "host_matches", "check_domain", "check_url", "NETWORK_SCHEMES"]
