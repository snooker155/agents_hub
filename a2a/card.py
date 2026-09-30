"""The Agent Card: the document an A2A client reads before it talks to an agent.

A2A (Agent2Agent, the Linux Foundation specification) discovers an agent through
a small JSON document served at a well-known path. The card says who the agent
is, where its JSON-RPC endpoint lives, what it can do and how to authenticate.
Everything else in the protocol is negotiated from it, so the card is the one
place where this hub's own vocabulary (an ``AgentSpec``, a domain, a manifest's
tool list) has to be translated into the spec's.

The translation lives here rather than in the router for two reasons: it is pure
(a spec plus a base URL in, a dict out), so it can be tested without HTTP; and
the *reading* half is the same shape, so the importer validates a foreign card
with the same module that builds ours.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

#: The spec revision this hub implements. Clients use it to decide what they may
#: send; an older client that does not know the field simply ignores it.
PROTOCOL_VERSION = "0.3.0"

#: Where a card is served. The second path is what pre-0.3 clients ask for, and
#: serving both costs one route.
CARD_PATH = "/.well-known/agent-card.json"
LEGACY_CARD_PATH = "/.well-known/agent.json"

#: Text is all this hub accepts and produces over A2A today. Declaring it keeps
#: a client from sending a file part that would be silently dropped.
DEFAULT_INPUT_MODES = ["text/plain"]
DEFAULT_OUTPUT_MODES = ["text/plain"]

#: Name of the security scheme entry describing the hub's optional bearer token.
BEARER_SCHEME = "hubToken"

#: Size ceiling for a card fetched from somewhere else. A card is a few
#: kilobytes; anything larger is either a mistake or an attempt to make the
#: importer read a stream it will never finish.
MAX_CARD_BYTES = 256_000


def agent_endpoint(base_url: str, agent_id: str) -> str:
    """The JSON-RPC endpoint an A2A client posts to for *agent_id*."""
    return f"{(base_url or '').rstrip('/')}/api/a2a/agents/{agent_id}"


def agent_card_url(base_url: str, agent_id: str) -> str:
    """Where this agent's own card is served."""
    return f"{agent_endpoint(base_url, agent_id)}{CARD_PATH}"


def _clean(values: Sequence[Any], limit: int = 24) -> List[str]:
    """Deduplicate, stringify and bound a tag list, preserving order."""
    out: List[str] = []
    for value in values or ():
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def build_agent_card(
    *,
    agent_id: str,
    name: str,
    description: str = "",
    base_url: str,
    version: str = "1",
    tags: Sequence[str] = (),
    examples: Sequence[str] = (),
    token_required: bool = False,
    streaming: bool = True,
) -> Dict[str, Any]:
    """Build one agent's card.

    ``token_required`` reflects the hub's own API token: when one is configured
    every ``/api`` request must carry it, so a card that did not say so would
    send clients straight into a 401 they cannot explain. With no token the
    ``security`` list is empty, which the spec reads as "no authentication
    required" rather than "unknown".
    """
    card: Dict[str, Any] = {
        "protocolVersion": PROTOCOL_VERSION,
        "name": name or agent_id,
        "description": description or f"Agent '{agent_id}' running in Agents Hub.",
        "url": agent_endpoint(base_url, agent_id),
        "preferredTransport": "JSONRPC",
        "version": version or "1",
        "capabilities": {
            "streaming": bool(streaming),
            # Not implemented: the hub never calls a client back, so promising
            # push notifications would strand any client that relied on them.
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "defaultInputModes": list(DEFAULT_INPUT_MODES),
        "defaultOutputModes": list(DEFAULT_OUTPUT_MODES),
        "skills": [
            {
                "id": agent_id,
                "name": name or agent_id,
                "description": description or f"Run agent '{agent_id}'.",
                "tags": _clean(tags),
                "examples": _clean(examples, limit=5),
            }
        ],
        "provider": {"organization": "Agents Hub", "url": (base_url or "").rstrip("/")},
        "securitySchemes": {},
        "security": [],
    }
    if token_required:
        card["securitySchemes"] = {
            BEARER_SCHEME: {
                "type": "http",
                "scheme": "bearer",
                "description": "The hub's API token, sent as Authorization: Bearer <token>.",
            }
        }
        card["security"] = [{BEARER_SCHEME: []}]
    return card


def card_from_spec(
    spec: Any,
    base_url: str,
    *,
    version: str = "1",
    token_required: bool = False,
) -> Dict[str, Any]:
    """Build the card for a registered agent.

    One agent is one skill. The hub's agents are not menus of callable
    operations: a prompt goes in and an answer comes out, so inventing several
    skills per agent would describe a structure that does not exist. What a
    caller can usefully filter on becomes the skill's tags: the domain, and for
    an imported agent the tools its manifest declares.
    """
    tags: List[str] = []
    domain = (getattr(spec, "domain", "") or "").strip()
    if domain:
        tags.append(domain)
    tags.extend(getattr(spec, "tools", None) or [])
    remote = getattr(spec, "remote", None) or {}
    manifest = remote.get("manifest") if isinstance(remote, dict) else None
    if isinstance(manifest, dict):
        tags.extend(manifest.get("tools") or [])
    return build_agent_card(
        agent_id=spec.id,
        name=getattr(spec, "name", "") or spec.id,
        description=getattr(spec, "description", "") or "",
        base_url=base_url,
        version=version,
        tags=tags,
        token_required=token_required,
    )


# ── reading someone else's card ─────────────────────────────────────────────

def validate_card(payload: Any) -> Tuple[Dict[str, Any], List[str]]:
    """Check a fetched card and report every problem at once.

    Returns ``(card, problems)``. Like the manifest parser, this never raises
    for a card that is merely incomplete: the import flow wants to show the
    operator the whole list, and a card missing only its ``version`` is still
    perfectly usable.
    """
    problems: List[str] = []
    if not isinstance(payload, dict):
        return {}, ["the agent card is not a JSON object"]

    url = str(payload.get("url") or "").strip()
    if not url:
        problems.append("'url' is missing, so there is no endpoint to send messages to")
    elif not url.startswith(("http://", "https://")):
        problems.append(f"'url' must be http(s); got '{url}'")

    if not str(payload.get("name") or "").strip():
        problems.append("'name' is missing")

    skills = payload.get("skills")
    if skills is not None and not isinstance(skills, list):
        problems.append("'skills' must be a list")

    capabilities = payload.get("capabilities")
    if capabilities is not None and not isinstance(capabilities, dict):
        problems.append("'capabilities' must be an object")

    return payload, problems


def card_endpoint(card: Dict[str, Any]) -> str:
    """The JSON-RPC endpoint a card advertises."""
    return str((card or {}).get("url") or "").strip().rstrip("/")


def card_streaming(card: Dict[str, Any]) -> bool:
    """Whether the card declares ``capabilities.streaming``."""
    capabilities = (card or {}).get("capabilities")
    return bool(isinstance(capabilities, dict) and capabilities.get("streaming"))


def card_skill_ids(card: Dict[str, Any]) -> List[str]:
    """Skill ids from a card, used as the imported agent's declared tools."""
    skills = (card or {}).get("skills")
    if not isinstance(skills, list):
        return []
    out: List[str] = []
    for skill in skills:
        if isinstance(skill, dict):
            skill_id = str(skill.get("id") or skill.get("name") or "").strip()
            if skill_id:
                out.append(skill_id)
    return out


def card_security_schemes(card: Dict[str, Any]) -> List[str]:
    """Names of the security schemes a card declares, for the readiness report."""
    schemes = (card or {}).get("securitySchemes")
    return sorted(str(k) for k in schemes) if isinstance(schemes, dict) else []


def describe_card(card: Dict[str, Any], *, card_url: str = "") -> Dict[str, Any]:
    """The few fields the import surfaces show, flattened out of a card."""
    return {
        "name": str(card.get("name") or "").strip(),
        "description": str(card.get("description") or "").strip(),
        "url": card_endpoint(card),
        "card_url": card_url,
        "version": str(card.get("version") or "").strip(),
        "protocol_version": str(card.get("protocolVersion") or "").strip(),
        "streaming": card_streaming(card),
        "skills": card_skill_ids(card),
        "security_schemes": card_security_schemes(card),
    }


__all__ = [
    "PROTOCOL_VERSION",
    "CARD_PATH",
    "LEGACY_CARD_PATH",
    "DEFAULT_INPUT_MODES",
    "DEFAULT_OUTPUT_MODES",
    "BEARER_SCHEME",
    "MAX_CARD_BYTES",
    "agent_endpoint",
    "agent_card_url",
    "build_agent_card",
    "card_from_spec",
    "validate_card",
    "card_endpoint",
    "card_streaming",
    "card_skill_ids",
    "card_security_schemes",
    "describe_card",
]
