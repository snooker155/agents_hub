"""Importing an agent from an A2A Agent Card instead of from a repository.

The repository import exists because a foreign agent's *code* has to be brought
somewhere the hub can reach it. An A2A agent is already running and already
describes itself: its card says where its endpoint is, what it is called and
what it can do. There is nothing to clone, so this module turns a card URL
straight into an :class:`~agents.importer.manifest.AgentManifest` and the rest
of the pipeline (readiness checks, registration, the ``remote`` descriptor)
runs unchanged.

The card is fetched from a URL a person pasted, so the fetch is bounded on every
axis that a hostile or merely broken server could stretch: scheme, redirects,
time and size.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

from a2a import card as a2a_card
from agents.importer.manifest import AGENT_ID_RE, AgentManifest

#: Suffixes that make a URL a card URL rather than a repository. The first two
#: are the well-known paths from the specification; the bare filenames cover a
#: card published somewhere else, which the spec explicitly allows.
CARD_SUFFIXES = (
    "/.well-known/agent-card.json",
    "/.well-known/agent.json",
    "/agent-card.json",
    "/agent.json",
)

#: Seconds to wait for a card. A card is a static file; a server that cannot
#: produce one in this long is not one to import an agent from.
CARD_TIMEOUT = 15.0


class CardError(Exception):
    """The card could not be fetched or is not a card."""


def is_card_url(url: str) -> bool:
    """Whether this URL names an Agent Card rather than a git repository."""
    text = (url or "").strip().lower()
    if not text.startswith(("http://", "https://")):
        return False
    path = text.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return any(path.endswith(suffix) for suffix in CARD_SUFFIXES)


def fetch_card(url: str, *, timeout: float = CARD_TIMEOUT) -> Dict[str, Any]:
    """GET an Agent Card, with every limit a pasted URL deserves.

    Raises :class:`CardError` with something an operator can act on. Redirects
    are followed because a card is routinely served behind one, but only within
    the http/https schemes the check below enforces on the final URL too.
    """
    import httpx

    target = (url or "").strip()
    if not target.startswith(("http://", "https://")):
        raise CardError("an agent card URL must start with http:// or https://")

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(target, headers={"Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001 - every transport error reads the same here
        raise CardError(f"could not fetch {target}: {type(exc).__name__}: {exc}")

    if str(response.url).lower().split(":", 1)[0] not in ("http", "https"):
        raise CardError(f"{target} redirected somewhere that is not http(s)")
    if response.status_code >= 400:
        raise CardError(f"{target} answered HTTP {response.status_code}")

    raw = response.content or b""
    if len(raw) > a2a_card.MAX_CARD_BYTES:
        raise CardError(
            f"{target} returned {len(raw)} bytes; an agent card must be at most "
            f"{a2a_card.MAX_CARD_BYTES}"
        )
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace") or "{}")
    except ValueError as exc:
        raise CardError(f"{target} did not return JSON: {exc}")

    card, problems = a2a_card.validate_card(payload)
    if problems:
        raise CardError(f"{target} is not a usable agent card: " + "; ".join(problems))
    return card


def slugify(value: str) -> str:
    """An agent id from a card's name, since a card carries no id of its own.

    A2A identifies an agent by its URL; this hub identifies it by a short id
    that has to be typeable and unique here. Deriving one from the name is what
    the import dialog pre-fills, and the operator can overwrite it.
    """
    slug = re.sub(r"[^a-z0-9_-]+", "-", (value or "").strip().lower()).strip("-_")
    slug = re.sub(r"-{2,}", "-", slug)
    if len(slug) < 2:
        slug = f"a2a-{slug}" if slug else "a2a-agent"
    return slug[:64] if AGENT_ID_RE.match(slug[:64]) else "a2a-agent"


def manifest_from_card(
    card: Dict[str, Any],
    *,
    card_url: str = "",
    agent_id: Optional[str] = None,
) -> AgentManifest:
    """Build the manifest an A2A card implies.

    ``found=True`` although no ``agent-hub.json`` exists anywhere: the card *is*
    the declaration, and the readiness report would otherwise open with "no
    manifest" for an agent that described itself perfectly well.
    """
    name = str(card.get("name") or "").strip()
    manifest = AgentManifest(
        found=True,
        path=card_url or a2a_card.CARD_PATH,
        raw=dict(card),
        id=(agent_id or "").strip() or slugify(name),
        name=name or (agent_id or ""),
        description=str(card.get("description") or "").strip(),
        domain="external",
        runtime_kind="a2a",
        url=a2a_card.card_endpoint(card),
        # An A2A agent has one endpoint and no paths of its own: every method is
        # a JSON-RPC call to the same URL.
        run_path="",
        health_path="",
        card_url=card_url,
        card=dict(card),
        tools=a2a_card.card_skill_ids(card),
    )
    if not manifest.url:
        manifest.problems.append("the agent card declares no 'url' to send messages to")
    return manifest


def load_manifest(card_url: str, *, agent_id: Optional[str] = None) -> Tuple[AgentManifest, Dict[str, Any]]:
    """Fetch a card and return ``(manifest, card)``. Raises :class:`CardError`."""
    card = fetch_card(card_url)
    return manifest_from_card(card, card_url=card_url, agent_id=agent_id), card


def probe(card_url: str) -> Dict[str, Any]:
    """Fetch a card for a readiness check, reporting failure instead of raising."""
    try:
        card = fetch_card(card_url)
    except CardError as exc:
        return {"ok": False, "detail": str(exc), "card": {}}
    return {"ok": True, "detail": f"{card.get('name') or 'agent'} card read from {card_url}", "card": card}


__all__ = [
    "CARD_SUFFIXES",
    "CARD_TIMEOUT",
    "CardError",
    "is_card_url",
    "fetch_card",
    "slugify",
    "manifest_from_card",
    "load_manifest",
    "probe",
]
