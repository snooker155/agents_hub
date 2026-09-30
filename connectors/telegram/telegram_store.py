"""
Telegram integration — config + per-chat agent bindings, in the database.

State is one document, held by :class:`common.docstore.DocStore` under the
key ``"state"`` (store name ``"telegram"``), shaped like the old
``.agents_hub/telegram.json``:

    {
        "bot_token": "<secret, never returned to the UI>",
        "enabled": false,
        "update_offset": 0,
        "allowed_chat_ids": [],
        "bindings": [
            {
                "chat_id": 123456,
                "agent_id": "swe_agent",
                "flow_id": null,
                "workspace": "demo",
                "conversation_id": "uuid-...",
                "title": "@someuser",
                "created_at": "2026-..."
            }
        ]
    }

A read-modify-write (every setter below) happens inside ``store.transaction()``,
which is atomic across every process and host, in place of the file lock this
used to take. An existing ``telegram.json`` is imported once on first use and
renamed ``.migrated``.

A binding targets either an agent (`agent_id`) or a flow (`flow_id`) — never
both. The token is write-only from the API perspective — callers see only
`has_token: bool`.

`allowed_chat_ids` is the gate on who the bot will talk to at all: an empty
list rejects every chat once a token is configured (the safe default — the
operator opts specific chats in, rather than the bot answering anyone who
finds it). The polling adapter drops any update from a chat_id not on this
list before it reaches command handling.

The `workspace` field of a binding can only be set through the REST API (the
dashboard), never from an inbound Telegram command — a chat that isn't
already bound to a workspace by an operator is told to ask them, instead of
being able to bind itself. Once a binding has a workspace, `/agent <id>` and
`/flow <id>` from the chat still pick a target within it, and bindings may be
removed via the REST API.
"""
from __future__ import annotations

import json

from datetime import datetime, timezone
from typing import Any, Optional

from common.docstore import DocStore
from common.paths import AGENTS_HUB_ROOT

#: Legacy JSON file this collection was imported from.
_TG_FILE = AGENTS_HUB_ROOT / "telegram.json"

# No ``legacy_file=`` here: telegram.json is one dict of settings, not a
# collection, so the store's own per-key import would split it into one
# document per top-level field. It is imported by hand, below, as a single
# document under the "state" key.
_store = DocStore("telegram")

_STATE_KEY = "state"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_legacy_imported() -> None:
    """Import ``telegram.json`` once, as the single "state" document.

    A store that already has rows is left alone (:meth:`DocStore.import_legacy`
    re-checks this itself, atomically); the cheap existence check here just
    avoids reading and parsing the file on every call once it is gone.
    """
    if not _TG_FILE.exists():
        return
    try:
        text = _TG_FILE.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else None
    except Exception:
        return
    if isinstance(data, dict):
        _store.import_legacy({_STATE_KEY: data}, _TG_FILE)


def _default_state() -> dict[str, Any]:
    return {
        "bot_token": "",
        "enabled": False,
        "update_offset": 0,
        # Chat ids allowed to talk to the bot. Empty means "reject everyone" once
        # a token is configured — an operator must add chat ids explicitly.
        "allowed_chat_ids": [],
        "bindings": [],
    }


def _coerce(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return _default_state()
    # Backfill missing fields
    out = _default_state()
    out.update({k: v for k, v in data.items() if k in out})
    if not isinstance(out.get("bindings"), list):
        out["bindings"] = []
    if not isinstance(out.get("allowed_chat_ids"), list):
        out["allowed_chat_ids"] = []
    return out


def load() -> dict[str, Any]:
    """Return the full state dict (token included; internal use only)."""
    _ensure_legacy_imported()
    return _coerce(_store.get(_STATE_KEY))


def get_token() -> str:
    return str(load().get("bot_token") or "")


def is_enabled() -> bool:
    return bool(load().get("enabled"))


def has_token() -> bool:
    return bool(get_token().strip())


def set_token(token: Optional[str]) -> None:
    """Set or clear the bot token."""
    _ensure_legacy_imported()
    with _store.transaction():
        data = _coerce(_store.get(_STATE_KEY))
        data["bot_token"] = (token or "").strip()
        _store.put(_STATE_KEY, data)


def set_enabled(enabled: bool) -> None:
    _ensure_legacy_imported()
    with _store.transaction():
        data = _coerce(_store.get(_STATE_KEY))
        data["enabled"] = bool(enabled)
        _store.put(_STATE_KEY, data)


def get_allowed_chat_ids() -> list[int]:
    """Chat ids the bot will process updates from. Empty = reject everyone."""
    raw = load().get("allowed_chat_ids") or []
    out = []
    for v in raw:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


def set_allowed_chat_ids(chat_ids: list[int]) -> None:
    """Replace the chat id allowlist."""
    cleaned = []
    for v in chat_ids or []:
        try:
            cleaned.append(int(v))
        except (TypeError, ValueError):
            continue
    _ensure_legacy_imported()
    with _store.transaction():
        data = _coerce(_store.get(_STATE_KEY))
        data["allowed_chat_ids"] = cleaned
        _store.put(_STATE_KEY, data)


def is_chat_allowed(chat_id: int) -> bool:
    """Whether a chat may talk to the bot. An empty allowlist allows no one."""
    try:
        chat_id = int(chat_id)
    except (TypeError, ValueError):
        return False
    return chat_id in set(get_allowed_chat_ids())


def get_update_offset() -> int:
    return int(load().get("update_offset") or 0)


def set_update_offset(offset: int) -> None:
    _ensure_legacy_imported()
    with _store.transaction():
        data = _coerce(_store.get(_STATE_KEY))
        data["update_offset"] = int(offset or 0)
        _store.put(_STATE_KEY, data)


# ── Bindings ─────────────────────────────────────────────────────────────────

def list_bindings() -> list[dict[str, Any]]:
    return list(load().get("bindings") or [])


def get_binding(chat_id: int) -> Optional[dict[str, Any]]:
    for b in list_bindings():
        if int(b.get("chat_id", 0)) == int(chat_id):
            return dict(b)
    return None


def upsert_binding(
    *,
    chat_id: int,
    agent_id: str,
    workspace: Optional[str] = None,
    conversation_id: Optional[str] = None,
    title: Optional[str] = None,
    flow_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create or update a chat→agent/flow binding. Returns the resulting binding.

    A binding targets either an agent (``agent_id``) or a flow (``flow_id``);
    setting one clears the other so the chat has a single unambiguous target.
    """
    _ensure_legacy_imported()
    with _store.transaction():
        data = _coerce(_store.get(_STATE_KEY))
        bindings = list(data.get("bindings") or [])
        existing = None
        for b in bindings:
            if int(b.get("chat_id", 0)) == int(chat_id):
                existing = b
                break
        if existing is None:
            existing = {
                "chat_id": int(chat_id),
                "agent_id": agent_id,
                "flow_id": flow_id,
                "workspace": workspace,
                "conversation_id": conversation_id,
                "title": title,
                "created_at": _utc_iso(),
                "last_message_at": None,
            }
            bindings.append(existing)
        else:
            existing["agent_id"] = agent_id
            existing["flow_id"] = flow_id
            if workspace is not None:
                existing["workspace"] = workspace
            if conversation_id is not None:
                existing["conversation_id"] = conversation_id
            if title is not None:
                existing["title"] = title
        data["bindings"] = bindings
        _store.put(_STATE_KEY, data)
        return dict(existing)


def touch_binding(chat_id: int) -> None:
    """Update last_message_at for a binding (best-effort, no error if missing)."""
    _ensure_legacy_imported()
    with _store.transaction():
        data = _coerce(_store.get(_STATE_KEY))
        for b in data.get("bindings") or []:
            if int(b.get("chat_id", 0)) == int(chat_id):
                b["last_message_at"] = _utc_iso()
                _store.put(_STATE_KEY, data)
                return


def reset_conversation(chat_id: int, new_conversation_id: str) -> Optional[dict[str, Any]]:
    """Replace conversation_id for a binding (used by `/reset`)."""
    _ensure_legacy_imported()
    with _store.transaction():
        data = _coerce(_store.get(_STATE_KEY))
        for b in data.get("bindings") or []:
            if int(b.get("chat_id", 0)) == int(chat_id):
                b["conversation_id"] = new_conversation_id
                _store.put(_STATE_KEY, data)
                return dict(b)
        return None


def remove_binding(chat_id: int) -> bool:
    _ensure_legacy_imported()
    with _store.transaction():
        data = _coerce(_store.get(_STATE_KEY))
        original = list(data.get("bindings") or [])
        kept = [b for b in original if int(b.get("chat_id", 0)) != int(chat_id)]
        if len(kept) == len(original):
            return False
        data["bindings"] = kept
        _store.put(_STATE_KEY, data)
        return True
