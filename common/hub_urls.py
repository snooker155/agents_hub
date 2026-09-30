"""The hub's own addresses, as seen from the outside and from the browser.

Two questions this module answers, both for project deployments
(docs/project-deployments.md):

* :func:`public_base`: where a person's browser reaches the hub, so a
  deployed app's external link can be absolute
  (``AGENTS_HUB_PUBLIC_URL``, the same knob Telegram links use).
* :func:`browser_base`: where the *agent's* browser (the browser service,
  deploy/browser/) reaches the hub. Under docker-compose that service sits in
  its own network, where ``localhost`` is itself, so the hub's address is
  ``host.docker.internal`` or the compose service name; set
  ``AGENTS_HUB_BROWSER_HUB_URL`` to whichever it is.

Plus the exemption both the hub's ``validate_url`` and the browser service's
policy make for those addresses: :func:`is_internal_url`. The private-network
block (common/ssrf.py) refuses every loopback and private address, which is
right for the internet at large and wrong for the hub itself, which is
exactly where a deployed app is served from. The exemption is narrow on
purpose: only the origins listed here, and only the paths that serve
deployed apps and previews (``/apps/`` and ``/preview/``), so an agent still
cannot browse the hub's own API through it.
"""
from __future__ import annotations

import os
from typing import List, Tuple
from urllib.parse import urlsplit

#: Paths under the hub that serve proxied application pages. Everything else
#: on the hub stays subject to the ordinary private-network block.
INTERNAL_PATHS: Tuple[str, ...] = ("/apps/", "/preview/")

_DEFAULT_HUB_PORT = "8000"


def _setting(key: str) -> str:
    try:
        from common.config import live_setting
        return live_setting(key)
    except Exception:  # noqa: BLE001 - settings not importable in a trimmed process
        return (os.environ.get(key) or "").strip()


def _origin(url: str) -> str:
    """``scheme://host[:port]`` of ``url``, lowercased, or ``""``."""
    parts = urlsplit((url or "").strip())
    if not parts.scheme or not parts.hostname:
        return ""
    host = parts.hostname.lower()
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme.lower()}://{host}{port}"


def public_base() -> str:
    """Where a person reaches the hub, without a trailing slash, or ``""``."""
    return (_setting("AGENTS_HUB_PUBLIC_URL") or _setting("AUTH_PUBLIC_URL")).rstrip("/")


def browser_base() -> str:
    """Where the browser service reaches the hub, without a trailing slash.

    ``AGENTS_HUB_BROWSER_HUB_URL`` when set. Otherwise the public URL, and
    failing that a guess from where the browser service itself lives: a
    service on ``localhost`` shares the host with a host-run hub, anything
    else (a compose service) reaches the host through ``host.docker.internal``.
    """
    explicit = _setting("AGENTS_HUB_BROWSER_HUB_URL").rstrip("/")
    if explicit:
        return explicit
    public = public_base()
    if public:
        return public
    browser_url = _setting("AGENTS_HUB_BROWSER_URL")
    host = (urlsplit(browser_url).hostname or "").lower() if browser_url else ""
    if host in ("", "localhost", "127.0.0.1", "::1"):
        return f"http://localhost:{_DEFAULT_HUB_PORT}"
    return f"http://host.docker.internal:{_DEFAULT_HUB_PORT}"


def internal_origins() -> List[str]:
    """Every origin that is the hub itself: the public one, the browser's
    one and anything in ``AGENTS_HUB_INTERNAL_ORIGINS`` (comma separated)."""
    raw = [public_base(), browser_base()]
    raw += [p for p in _setting("AGENTS_HUB_INTERNAL_ORIGINS").split(",")]
    out: List[str] = []
    for item in raw:
        origin = _origin(item)
        if origin and origin not in out:
            out.append(origin)
    return out


def is_internal_url(url: str, origins=None, paths=INTERNAL_PATHS) -> bool:
    """True when ``url`` is one of the hub's own application pages: its
    origin is in ``origins`` (default :func:`internal_origins`) and its path
    starts with one of ``paths``."""
    origin = _origin(url)
    if not origin:
        return False
    allowed = list(origins) if origins is not None else internal_origins()
    if origin not in allowed:
        return False
    path = urlsplit(url.strip()).path or "/"
    return any(path.startswith(p) for p in paths)


def browser_policy_fields() -> dict:
    """What the browser service needs to make the same exemption."""
    return {"internal_origins": internal_origins(), "internal_paths": list(INTERNAL_PATHS)}


__all__ = ["INTERNAL_PATHS", "public_base", "browser_base", "internal_origins",
           "is_internal_url", "browser_policy_fields"]
