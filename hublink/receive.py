"""
The receiving end of the mirror: what a linked hub sends lands here.

Chats arrive whole and are stored under ``mirror:<node>:<id>``, read-only,
with ``origin`` ``mirror`` and the sender named in ``mirror``; they belong
to the key's owner and sit in the target workspace (the owner's personal
workspace on a multi-user hub unless the sender chose another). Personal
notes are merged by note id into the owner's personal pool in that
workspace. Nothing here is ever sent back.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

CHAT_PREFIX = "mirror:"
MAX_CHATS_PER_CALL = 50
MAX_POOLS_PER_CALL = 20


class ReceiveError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def target_workspace(principal: Any, wanted: Optional[str]) -> str:
    """Where mirrored records go: the named workspace when the caller can
    see it, else their personal workspace (multi) or ``default``."""
    from common import access, identity
    wanted = (wanted or "").strip()
    if wanted:
        if principal is not None and not principal.reaches(wanted):
            raise ReceiveError(f"this API key does not reach workspace '{wanted}'", status=403)
        if not access.can_see_workspace(principal, wanted):
            raise ReceiveError(f"workspace '{wanted}' is not visible to this account", status=403)
        return wanted
    if identity.current_mode() == "multi" and principal is not None and getattr(principal, "kind", "") == "user":
        from common import personal_workspace
        return str(personal_workspace.ensure_for_principal(principal) or "default")
    return "default"


def chat_id_for(node: str, source_id: str) -> str:
    from hublink.store import slugify
    return f"{CHAT_PREFIX}{slugify(node)}:{source_id}"


def store_chats(principal: Any, *, node: str, workspace: Optional[str],
                chats: List[Dict[str, Any]]) -> Dict[str, Any]:
    from common import chat_store
    if not isinstance(chats, list):
        raise ReceiveError("chats must be a list")
    if len(chats) > MAX_CHATS_PER_CALL:
        raise ReceiveError(f"at most {MAX_CHATS_PER_CALL} chats per call")
    target = target_workspace(principal, workspace)
    node_name = (node or "node").strip()[:60] or "node"
    stored = 0
    for chat in chats:
        if not isinstance(chat, dict) or not str(chat.get("id") or "").strip():
            continue
        source_id = str(chat["id"]).strip()
        doc = {k: v for k, v in chat.items() if k != "owner"}
        doc.update({
            "id": chat_id_for(node_name, source_id),
            "workspace": target,
            "origin": "mirror",
            "read_only": True,
            "mirror": {"node": node_name, "source_id": source_id,
                       "source_workspace": str(chat.get("workspace") or ""),
                       "source_origin": str(chat.get("origin") or ""),
                       "received_at": _now()},
            # Project ids, flows and teams are the sender's; a page here would
            # look them up and find nothing or, worse, something else.
            "project_id": None, "flow_id": None, "team_id": None,
        })
        chat_store.save_chat(doc)
        stored += 1
    return {"stored": stored, "workspace": target}


def store_memory(principal: Any, *, node: str, workspace: Optional[str],
                 pools: List[Dict[str, Any]]) -> Dict[str, Any]:
    from common import identity
    from memory import personal
    from memory.store import MemoryStore
    if not isinstance(pools, list):
        raise ReceiveError("pools must be a list")
    if len(pools) > MAX_POOLS_PER_CALL:
        raise ReceiveError(f"at most {MAX_POOLS_PER_CALL} pools per call")
    target = target_workspace(principal, workspace)
    if not personal.workspace_enabled(target):
        return {"stored": 0, "workspace": target, "skipped": "personal memory is off in that workspace"}
    node_name = (node or "node").strip()[:60] or "node"
    user_id = identity.current_user_id()
    mem = personal.ensure_pool(user_id, target)
    existing = {str(n.get("id")): i for i, n in enumerate(mem.notes) if isinstance(n, dict) and n.get("id")}
    stored = 0
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        source_pool = str(pool.get("source_id") or "")
        for note in pool.get("notes") or []:
            if not isinstance(note, dict) or not note.get("id"):
                continue
            if not str(note.get("title") or "").strip() and not str(note.get("content") or "").strip():
                continue
            copy = {
                "id": str(note["id"]),
                "title": str(note.get("title") or "")[:300],
                "content": str(note.get("content") or "")[:20000],
                "created_at": str(note.get("created_at") or _now()),
                "mirror": {"node": node_name, "pool": source_pool},
            }
            idx = existing.get(copy["id"])
            if idx is None:
                mem.notes.append(copy)
                existing[copy["id"]] = len(mem.notes) - 1
            else:
                mem.notes[idx] = copy
            stored += 1
    if stored:
        mem.touch()
        MemoryStore().add(mem)
    return {"stored": stored, "workspace": target, "memory_id": str(mem.id)}


__all__ = ["CHAT_PREFIX", "MAX_CHATS_PER_CALL", "MAX_POOLS_PER_CALL", "ReceiveError",
           "chat_id_for", "store_chats", "store_memory", "target_workspace"]
