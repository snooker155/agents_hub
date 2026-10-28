"""
A person's conversations with the Assistant and the Help panel.

Both live in the entity chat store (common/entity_chat_store.py) under one
key per person, ``<kind>:user-<id>``, holding the live thread plus every
archived one. That shape does not travel as it is: the Assistant's threads
each belong to one workspace while the entry spans all of them, and the epoch
numbers that name a thread are this hub's own. So the record is the person's
threads *of one workspace*, with the epochs left out; applying replaces that
workspace's threads here and leaves the other workspaces' alone.

The record id is the store key. The person's id differs between hubs, so the
engine's id mapping is what ties ``assistant:user-local`` here to
``assistant:user-<id>`` there; :meth:`apply` returns the key it wrote.

Help threads carry no workspace stamp, so they sync with the ``default``
workspace only (where the store files an unstamped thread). The admin's
``service-`` thread is not a personal conversation and stays where it is.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from common.entity_chat_store import MAX_ARCHIVED_SESSIONS, EntityChatStore, entity_chat_store
from hubsync.adapters.base import Adapter, AdapterError, register

KINDS = ("assistant", "help")

#: Same cleaning the two routes apply to a user id when they build the key
#: (routes/assistant.py ``_clean``, routes/help_chat.py ``help_chat_id``).
#: Repeated here because the routes need the web stack to import.
_ID_OK = re.compile(r"[^A-Za-z0-9_:.@+-]")
_MAX_KEY_CHARS = 120

#: Where the store files a thread that was never stamped with a workspace.
_UNSTAMPED = "default"


def _key_of(kind: str, owner: str) -> str:
    cleaned = _ID_OK.sub("-", str(owner or "").strip())[:_MAX_KEY_CHARS]
    return f"user-{cleaned or 'local'}"


def _split(record_id: str) -> tuple:
    kind, _, entity_id = str(record_id).partition(":")
    if kind not in KINDS or not entity_id:
        raise AdapterError(f"{record_id!r} is not an assistant or help thread key")
    return kind, entity_id


def _first_at(messages: List[dict]) -> str:
    return str((messages[0].get("at") if messages else "") or "")


def _thread(session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    messages = [m for m in (session.get("messages") or []) if isinstance(m, dict)]
    trace = [t for t in (session.get("trace") or []) if isinstance(t, dict)]
    if not messages and not trace:
        return None
    return {"started_at": _first_at(messages), "messages": messages, "trace": trace}


def _threads_of(entry: Dict[str, Any], workspace: str) -> List[Dict[str, Any]]:
    """The portable threads of one workspace, in a fixed order."""
    sessions = [entry] + list(entry.get("sessions") or [])
    out = []
    for session in sessions:
        if EntityChatStore._thread_workspace(session, _UNSTAMPED) != workspace:
            continue
        thread = _thread(session)
        if thread is not None:
            out.append(thread)
    # Epochs are this hub's, so order by what the thread says about itself.
    out.sort(key=lambda t: (t["started_at"], json.dumps(t["messages"][:1], sort_keys=True, default=str)))
    return out


class AssistantAdapter(Adapter):
    kind = "assistant"
    order = 41
    label = "Assistant and Help threads"
    volatile = ()

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        store = entity_chat_store()
        ids = []
        for kind in KINDS:
            entity_id = _key_of(kind, owner)
            entry = store.docs.get(store._key(kind, entity_id))
            if entry and _threads_of(entry, workspace):
                ids.append(f"{kind}:{entity_id}")
        return ids

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        kind, entity_id = _split(record_id)
        store = entity_chat_store()
        entry = store.docs.get(store._key(kind, entity_id))
        threads = _threads_of(entry, workspace) if entry else []
        return {"thread_kind": kind, "threads": threads} if threads else None

    def existing_local(self, workspace: str, record_id: str, doc: Dict[str, Any], *,
                       owner: str) -> Optional[str]:
        # The person's own threads here live under their own key.
        kind, _ = _split(record_id)
        mine = f"{kind}:{_key_of(kind, owner)}"
        return mine if mine != record_id and self.read(workspace, mine) is not None else None

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        kind, _ = _split(record_id)
        entity_id = _key_of(kind, owner)
        threads = [t for t in (doc.get("threads") or []) if isinstance(t, dict)]
        self._replace(kind, entity_id, workspace, threads)
        return f"{kind}:{entity_id}"

    def delete(self, workspace: str, record_id: str) -> None:
        kind, entity_id = _split(record_id)
        self._replace(kind, entity_id, workspace, [])

    @staticmethod
    def _replace(kind: str, entity_id: str, workspace: str, threads: List[Dict[str, Any]]) -> None:
        """Swap this workspace's threads for ``threads`` inside one store
        transaction, so a turn writing to the same entry meanwhile is not lost."""
        store = entity_chat_store()
        key = store._key(kind, entity_id)
        with store.docs.transaction():
            entry = store.docs.get(key)
            if entry is None:
                if not threads:
                    return
                entry = store._blank(kind)
            live_is_ours = EntityChatStore._thread_workspace(entry, _UNSTAMPED) == workspace
            if live_is_ours:
                entry["messages"], entry["trace"] = [], []
            entry["sessions"] = [s for s in (entry.get("sessions") or [])
                                 if EntityChatStore._thread_workspace(s, _UNSTAMPED) != workspace]
            live_free = not entry.get("messages") and not entry.get("trace")
            archived = list(entry["sessions"])
            for i, thread in enumerate(threads):
                messages = [m for m in (thread.get("messages") or []) if isinstance(m, dict)]
                trace = [t for t in (thread.get("trace") or []) if isinstance(t, dict)]
                epoch = store._next_epoch(entry)
                newest = i == len(threads) - 1
                if newest and live_free:
                    # The latest conversation continues where the person left it.
                    entry.update({"messages": messages, "trace": trace, "session_epoch": epoch,
                                  "workspace": workspace})
                    continue
                archived.append({"epoch": epoch, "messages": messages, "trace": trace,
                                 "started_at": _first_at(messages) or None, "workspace": workspace,
                                 "updated_at": str((messages[-1].get("at") if messages else "") or "") or None})
            entry["sessions"] = archived[-MAX_ARCHIVED_SESSIONS:]
            if live_is_ours and not threads:
                entry["workspace"] = workspace
            store.docs.put(key, entry)


register(AssistantAdapter())
