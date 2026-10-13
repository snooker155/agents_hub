"""
The link record: where this hub is attached, with what, and what it does there.

One document, ``link``, in the ``hub_link`` DocStore. The API key and the
connection token are stored as they are, the way custom backends keep their
keys (providers/registry.py): the database is at the same trust level as
``.env``. The status route masks them (:func:`public`).
"""
from __future__ import annotations

import re
import socket
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from common.docstore import DocStore

_store = DocStore("hub_link")
KEY = "link"

#: Id of the custom backend and of the MCP server the uplink registers.
BACKEND_ID = "hub-remote"
MCP_SERVER_ID = "hub-remote"

#: Providers a node shares by default: what runs on this machine. Cloud
#: providers configured here are never offered through the node, since that
#: would hand the remote hub this hub's keys to spend.
DEFAULT_SHARED_PROVIDERS = ("hub-local", "ollama", "lmstudio")

MIRROR_KINDS = ("runs", "chats", "memory")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_node_name() -> str:
    return (socket.gethostname() or "node").split(".")[0][:40] or "node"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug[:40] or "node"


def normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        raise ValueError("the remote hub's URL is required")
    if not re.match(r"^https?://[^\s/]+", url):
        raise ValueError("the remote hub's URL must start with http:// or https://")
    for suffix in ("/api", "/v1"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


def blank() -> Dict[str, Any]:
    return {
        "url": "",
        "api_key": "",
        "name": default_node_name(),
        "use_models": True,
        "use_agents": True,
        "mirror": {"runs": True, "chats": True, "memory": False},
        "target_workspace": "",
        "node": {"enabled": False, "token": "", "share_models": True, "sandbox": True,
                 "shared_providers": list(DEFAULT_SHARED_PROVIDERS), "capacity": 4},
        "remote": {},           # whoami of the remote, as last seen
        "connection": {},       # {id, token} of the mirror connection on the remote
        "linked_at": None,
        "last_sync_at": None,
        "last_error": "",
    }


def get() -> Optional[Dict[str, Any]]:
    doc = _store.get(KEY)
    return dict(doc) if isinstance(doc, dict) and doc.get("url") else None


def is_linked() -> bool:
    return get() is not None


def put(doc: Dict[str, Any]) -> Dict[str, Any]:
    _store.put(KEY, doc)
    return doc


def update(**changes: Any) -> Optional[Dict[str, Any]]:
    with _store.transaction():
        doc = _store.get(KEY)
        if not isinstance(doc, dict):
            return None
        doc = {**doc, **changes}
        _store.put(KEY, doc)
    return doc


def clear() -> bool:
    return _store.delete(KEY)


def node_enabled(doc: Optional[Dict[str, Any]] = None) -> bool:
    doc = doc if doc is not None else get()
    if not doc:
        return False
    node = doc.get("node") or {}
    return bool(node.get("enabled")) and bool(node.get("token"))


def mirror_enabled(kind: str, doc: Optional[Dict[str, Any]] = None) -> bool:
    doc = doc if doc is not None else get()
    if not doc:
        return False
    return bool((doc.get("mirror") or {}).get(kind))


def shared_providers(doc: Optional[Dict[str, Any]] = None) -> List[str]:
    doc = doc if doc is not None else get()
    node = (doc or {}).get("node") or {}
    raw = node.get("shared_providers")
    if not isinstance(raw, list) or not raw:
        return list(DEFAULT_SHARED_PROVIDERS)
    return [str(p).strip().lower() for p in raw if str(p).strip()]


def node_capacity(doc: Optional[Dict[str, Any]] = None) -> int:
    """How many of the remote hub's requests this machine runs at once."""
    doc = doc if doc is not None else get()
    try:
        value = int(((doc or {}).get("node") or {}).get("capacity") or 4)
    except (TypeError, ValueError):
        value = 4
    return max(1, min(64, value))


def _mask(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    return "•" * 8 + value[-4:]


def public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The record for the UI and the CLI: credentials masked, never echoed."""
    if not doc:
        return None
    out = dict(doc)
    out["api_key_set"] = bool(doc.get("api_key"))
    out["api_key"] = _mask(doc.get("api_key") or "")
    node = dict(doc.get("node") or {})
    node["token_set"] = bool(node.get("token"))
    node["token"] = _mask(node.get("token") or "")
    out["node"] = node
    conn = dict(doc.get("connection") or {})
    if conn.get("token"):
        conn["token"] = _mask(conn["token"])
    out["connection"] = conn
    return out


__all__ = [
    "BACKEND_ID", "DEFAULT_SHARED_PROVIDERS", "KEY", "MCP_SERVER_ID", "MIRROR_KINDS",
    "blank", "clear", "default_node_name", "get", "is_linked", "mirror_enabled",
    "node_capacity", "node_enabled", "normalize_url", "public", "put", "shared_providers", "slugify", "update",
]
