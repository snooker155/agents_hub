"""
Conversations of the Chat page, as the sync engine sees them.

A chat is the one thing a person accumulates by simply using the hub, so it
is what they miss first on the other device. The store (common/chat_store.py)
keeps one JSON document per chat with the owner stamped from the request in
flight, which is why :meth:`ChatAdapter.apply` binds the syncing person as the
current user around the save instead of writing the row itself: the store's
size bounds, owner rule and change events then run unchanged.

Left out of the portable document on purpose: ``owner``, ``workspace`` and
``origin`` (they describe this hub's copy, and a synced-in chat is stamped
``sync`` here), ``id`` (the record id carries it) and the streaming state a
bubble holds while a turn is running. Chats the one-way mirror delivered
(``origin`` ``mirror``) are copies of another hub's chats and never travel
again.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from common import chat_store, db, identity
from hubsync.adapters.base import Adapter, AdapterError, register

#: What a bubble carries only while its answer is streaming; a finished chat
#: has none of it, and an unfinished one would make every poll a "change".
_LIVE_MESSAGE_FIELDS = ("thinking_live", "running_tool")

#: Columns that describe this hub's copy of the chat, not the conversation.
_LOCAL_FIELDS = ("id", "owner", "workspace", "origin", "message_count", "preview")

#: Stamped on chats written by a sync, so the one-way mirror can tell them
#: from chats a person typed here.
SYNC_ORIGIN = "sync"


def _row(chat_id: str) -> Optional[Dict[str, Any]]:
    return chat_store.get_chat(chat_id)


class ChatAdapter(Adapter):
    kind = "chat"
    order = 40
    label = "Chats"
    volatile = ("updated_at",)

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        rows = db.get_conn().execute(
            "SELECT chat_id, owner, origin FROM chats WHERE workspace = ?", (workspace,)).fetchall()
        return sorted(r["chat_id"] for r in rows
                      if (r["owner"] or "local") == owner and (r["origin"] or "") != "mirror")

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        chat = _row(record_id)
        if chat is None or chat.get("workspace") != workspace or chat.get("origin") == "mirror":
            return None
        doc = {k: v for k, v in chat.items() if k not in _LOCAL_FIELDS}
        doc["messages"] = [{k: v for k, v in m.items() if k not in _LIVE_MESSAGE_FIELDS}
                           for m in chat_store._messages(chat)]
        return doc

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        existing = _row(record_id)
        if existing is not None:
            if existing.get("workspace") != workspace:
                raise AdapterError(f"chat id {record_id} belongs to another workspace here")
            if (existing.get("owner") or "local") != owner:
                raise AdapterError(f"chat id {record_id} belongs to someone else here")
            if existing.get("origin") == "mirror":
                raise AdapterError(f"chat id {record_id} is a read-only mirror copy here")
        incoming = {k: v for k, v in doc.items() if k not in _LOCAL_FIELDS}
        incoming.update({"id": record_id, "workspace": workspace, "origin": SYNC_ORIGIN})
        # An existing chat keeps the origin it was made with (a Telegram chat
        # stays one); only a chat that arrives new is stamped as synced.
        if existing is not None and existing.get("origin"):
            incoming["origin"] = existing["origin"]
        token = identity.set_current_user(owner)
        try:
            saved = chat_store.save_chat(incoming)
        except ValueError as exc:
            raise AdapterError(str(exc)) from exc
        finally:
            identity.reset_current_user(token)
        return str(saved["id"])

    def delete(self, workspace: str, record_id: str) -> None:
        chat = _row(record_id)
        if chat is not None and chat.get("workspace") == workspace:
            chat_store.delete_chat(record_id)


register(ChatAdapter())
