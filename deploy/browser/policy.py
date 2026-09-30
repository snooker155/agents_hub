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

One exemption, and only one: the hub's own application pages. A project
deployment (docs/project-deployments.md) is served by the hub itself under
``/apps/<slug>/`` and ``/preview/<ticket>/``, on an origin that is by
construction loopback or private from where this service sits. The hub hands
those origins over as ``internal_origins`` (common/hub_urls.py), and a URL on
one of them whose path starts with one of ``internal_paths`` is allowed
without the DNS check; every other path on the same origin (the hub's API,
its dashboard) stays blocked. The deny list still applies to it.

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


def _origin(url: str) -> str:
    parts = urlparse((url or "").strip())
    if not parts.scheme or not parts.hostname:
        return ""
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme.lower()}://{parts.hostname.lower()}{port}"


@dataclass(frozen=True)
class Policy:
    """The domain policy one browser session enforces."""
    deny_domains: Tuple[str, ...] = field(default_factory=tuple)
    allow_domains: Tuple[str, ...] = field(default_factory=tuple)
    allowlist_enabled: bool = False
    #: The hub's own origins and the app paths on them (see the module docstring).
    internal_origins: Tuple[str, ...] = field(default_factory=tuple)
    internal_paths: Tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "Policy":
        data = data or {}
        origins = tuple(o for o in (_origin(str(x)) for x in (data.get("internal_origins") or ())) if o)
        paths = tuple(str(p) for p in (data.get("internal_paths") or ()) if str(p).startswith("/"))
        return cls(
            deny_domains=_clean(data.get("deny_domains")),
            allow_domains=_clean(data.get("allow_domains")),
            allowlist_enabled=bool(data.get("allowlist_enabled")),
            internal_origins=origins,
            internal_paths=paths,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "deny_domains": list(self.deny_domains),
            "allow_domains": list(self.allow_domains),
            "allowlist_enabled": self.allowlist_enabled,
            "internal_origins": list(self.internal_origins),
            "internal_paths": list(self.internal_paths),
        }

    def is_internal(self, url: str) -> bool:
        """One of the hub's own app pages: allowed without the DNS check."""
        if not self.internal_origins or not self.internal_paths:
            return False
        origin = _origin(url)
        if not origin or origin not in self.internal_origins:
            return False
        path = urlparse(url.strip()).path or "/"
        return any(path.startswith(p) for p in self.internal_paths)


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
    host = (parsed.hostname or "").lower()
    for pattern in policy.deny_domains:
        if host_matches(host, pattern):
            return False, f"host {host!r} is on the deny list"
    if policy.is_internal(url):
        return True, ""
    ok, reason = check_domain(url, policy)
    if not ok:
        return False, reason
    if not resolve:
        return True, ""
    return resolve_and_check(parsed.hostname or "")


__all__ = ["Policy", "host_matches", "check_domain", "check_url", "NETWORK_SCHEMES"]
