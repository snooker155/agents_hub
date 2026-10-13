"""
Data classes and node trust: which work may leave this hub for a node
(docs/hub-link.md, "Trust and data classes").

A node is someone's machine. Whoever owns it can read whatever it computes:
a prompt sent to its model, a snippet run in its sandbox, the answer that
comes back. Nothing in software changes that; only hardware that keeps
memory encrypted from its own owner does (confidential computing, checked by
remote attestation). So the hub does not try to hide work from a node. It
decides which work a node may see at all.

Every workspace has a **data class**, and an agent may make its own stricter:

* ``public``: nothing in it would hurt if read by a stranger. Any node.
* ``internal`` (the default): the organisation's own data. Only nodes the
  hub's administrator vouched for.
* ``confidential``: must stay on hardware nobody outside can read. Only an
  attested node; until attestation exists, no node at all.

Every node has a **trust level**, set by the hub's administrator and never
by the node's owner (who would always vouch for themselves):

* ``untrusted``: a volunteer's or a partner's machine. Public work only.
* ``trusted``: a machine the administrator answers for. Internal work too.
* ``attested``: reserved for a node whose hardware proved, by remote
  attestation, that it runs the hub's image in an enclave its owner cannot
  read. Not settable by hand.

The check runs where work is handed to a node (routes/hublink.py): the
node's own model proxy, the node pool and both exec routes. The workspace
and agent travel with a model call as headers the model builder sets
(providers/adapters.py), so the class is always the one in force now, not
the one when the model was built.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

PUBLIC = "public"
INTERNAL = "internal"
CONFIDENTIAL = "confidential"
CLASSES = (PUBLIC, INTERNAL, CONFIDENTIAL)
DEFAULT_CLASS = INTERNAL

UNTRUSTED = "untrusted"
TRUSTED = "trusted"
ATTESTED = "attested"
TRUST_LEVELS = (UNTRUSTED, TRUSTED, ATTESTED)
#: The levels an administrator may set by hand; ``attested`` comes only from
#: an attestation check.
SETTABLE_TRUST = (UNTRUSTED, TRUSTED)

#: The lowest trust a node needs to see work of each class.
REQUIRED_TRUST = {PUBLIC: UNTRUSTED, INTERNAL: TRUSTED, CONFIDENTIAL: ATTESTED}

#: Headers a model call to a node carries (providers/adapters.py).
WORKSPACE_HEADER = "X-Hub-Workspace"
AGENT_HEADER = "X-Hub-Agent"


def normalize_class(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    return text if text in CLASSES else None


def normalize_trust(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    return text if text in TRUST_LEVELS else None


def stricter(a: str, b: Optional[str]) -> str:
    """The stricter of two classes (``b`` may be unset)."""
    if not b:
        return a
    return a if CLASSES.index(a) >= CLASSES.index(b) else b


def _settings(workspace: Optional[str]) -> Dict[str, Any]:
    try:
        from workspace import get_workspace_metadata
        return dict((get_workspace_metadata(str(workspace or "default")) or {}).get("settings") or {})
    except Exception:  # noqa: BLE001 - an unreadable workspace keeps the default class
        log.debug("data class: settings of %s unreadable", workspace, exc_info=True)
        return {}


def workspace_class(workspace: Optional[str]) -> str:
    return normalize_class(_settings(workspace).get("data_class")) or DEFAULT_CLASS


def agent_class(agent_id: Optional[str]) -> Optional[str]:
    """The agent's own class, when it sets one."""
    if not agent_id:
        return None
    try:
        from agents.registry import get_agent
        spec = get_agent(str(agent_id))
    except Exception:  # noqa: BLE001 - an unknown agent sets nothing
        log.debug("data class: agent %s unreadable", agent_id, exc_info=True)
        return None
    return normalize_class(getattr(spec, "data_class", None)) if spec is not None else None


def effective_class(workspace: Optional[str], agent_id: Optional[str] = None) -> str:
    """The class work of ``agent_id`` in ``workspace`` carries: the
    workspace's, or the agent's when that is stricter. An agent can never
    make a workspace's data less sensitive than the workspace says it is."""
    return stricter(workspace_class(workspace), agent_class(agent_id))


def trust_of(record: Dict[str, Any]) -> str:
    """A node's trust level. A record from before trust levels existed is
    ``trusted`` in single mode (one operator made it) and ``untrusted`` in
    multi mode (anyone signed in may have)."""
    level = normalize_trust(record.get("trust"))
    if level:
        return level
    try:
        from common import identity
        return TRUSTED if identity.current_mode() != "multi" else UNTRUSTED
    except Exception:  # noqa: BLE001 - no identity layer: the cautious answer
        return UNTRUSTED


def trust_allows(trust: str, data_class: str) -> bool:
    need = REQUIRED_TRUST.get(data_class, ATTESTED)
    have = trust if trust in TRUST_LEVELS else UNTRUSTED
    return TRUST_LEVELS.index(have) >= TRUST_LEVELS.index(need)


def refusal(node_name: str, trust: str, data_class: str) -> str:
    need = REQUIRED_TRUST.get(data_class, ATTESTED)
    if data_class == CONFIDENTIAL:
        return (f"node '{node_name}' may not see confidential work: that needs an attested node "
                "(hardware its owner cannot read), and this one is "
                f"{trust}")
    return (f"node '{node_name}' is {trust}; {data_class} work needs a node that is at least {need}")


def set_workspace_class(workspace: str, data_class: str, *, actor: Optional[str] = None) -> str:
    cls = normalize_class(data_class)
    if cls is None:
        raise ValueError(f"unknown data class {data_class!r} (expected one of {', '.join(CLASSES)})")
    from workspace import get_workspace_metadata, update_workspace_metadata
    settings = dict((get_workspace_metadata(workspace) or {}).get("settings") or {})
    if settings.get("data_class") != cls:
        settings["data_class"] = cls
        settings["data_class_changed_at"] = datetime.now(timezone.utc).isoformat()
        settings["data_class_changed_by"] = actor or None
        update_workspace_metadata(workspace, {"settings": settings})
    return cls


def caller_headers() -> Dict[str, str]:
    """The headers naming who makes a model call, read where the model is
    built (an agent's run, the hub's /v1 route)."""
    out: Dict[str, str] = {}
    try:
        from common.workspace_scope import current_agent, current_workspace
        ws = current_workspace()
        agent = current_agent()
    except Exception:  # noqa: BLE001 - outside any run: the default workspace's class applies
        ws = agent = None
    if ws:
        out[WORKSPACE_HEADER] = str(ws)
    if agent:
        out[AGENT_HEADER] = str(agent)
    return out


__all__ = [
    "AGENT_HEADER", "ATTESTED", "CLASSES", "CONFIDENTIAL", "DEFAULT_CLASS", "INTERNAL", "PUBLIC",
    "REQUIRED_TRUST", "SETTABLE_TRUST", "TRUSTED", "TRUST_LEVELS", "UNTRUSTED", "WORKSPACE_HEADER",
    "agent_class", "caller_headers", "effective_class", "normalize_class", "normalize_trust",
    "refusal", "set_workspace_class", "stricter", "trust_allows", "trust_of", "workspace_class",
]
