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

Bots per workspace (docs/connectors.md "Connectors per workspace"), the same
shape as ``connectors/channels/store.py``: the default workspace's bot is the
document under ``"state"`` and serves every workspace; a workspace that
defines its own bot has a document of its own under ``"state@<workspace>"``,
with its own token, allowlist, update offset and bindings, and that bot
serves that workspace only. The module level functions below follow the
running code (:func:`connectors.channels.store.scope_workspace`): the current
workspace's bot when it defines one, else the default's.
:func:`for_workspace` gives a :class:`TelegramStore` bound to exactly one
workspace's document, for the routes that edit a workspace's bot and for the
poller serving it.

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
DEFAULT_WORKSPACE = "default"


def _key_for(workspace: Optional[str]) -> str:
    ws = str(workspace or "").strip()
    return _STATE_KEY if not ws or ws == DEFAULT_WORKSPACE else f"{_STATE_KEY}@{ws}"


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
    except (OSError, ValueError):
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


class TelegramStore:
    """One workspace's Telegram bot, or (``workspace=None``) the bot of the
    running code: its workspace's own when it defines one, else the default's."""

    def __init__(self, workspace: Optional[str] = None) -> None:
        self.workspace = (str(workspace).strip() or DEFAULT_WORKSPACE) if workspace is not None else None

    # ── workspaces ───────────────────────────────────────────────────────────

    def for_workspace(self, workspace: Optional[str]) -> "TelegramStore":
        return TelegramStore(workspace or DEFAULT_WORKSPACE)

    def defines(self, workspace: Optional[str]) -> bool:
        key = _key_for(workspace)
        return key == _STATE_KEY or _store.exists(key)

    def effective_workspace(self) -> str:
        if self.workspace is not None:
            return self.workspace
        from connectors.channels.store import scope_workspace
        ws = scope_workspace()
        if ws and ws != DEFAULT_WORKSPACE and _store.exists(_key_for(ws)):
            return ws
        return DEFAULT_WORKSPACE

    def defined_workspaces(self) -> list[str]:
        prefix = f"{_STATE_KEY}@"
        return sorted(k[len(prefix):] for k in _store.keys() if k.startswith(prefix))

    def remove_workspace(self, workspace: Optional[str]) -> bool:
        key = _key_for(workspace)
        if key == _STATE_KEY:
            return False
        return _store.delete(key)

    def _key(self) -> str:
        return _key_for(self.effective_workspace())

    def _update(self, fn) -> Any:
        _ensure_legacy_imported()
        key = self._key()
        with _store.transaction():
            data = _coerce(_store.get(key))
            result = fn(data)
            if result is not _NO_WRITE:
                _store.put(key, data)
            return None if result is _NO_WRITE else result

    # ── state ────────────────────────────────────────────────────────────────

    def load(self) -> dict[str, Any]:
        """Return the full state dict (token included; internal use only)."""
        _ensure_legacy_imported()
        return _coerce(_store.get(self._key()))

    def get_token(self) -> str:
        return str(self.load().get("bot_token") or "")

    def is_enabled(self) -> bool:
        return bool(self.load().get("enabled"))

    def has_token(self) -> bool:
        return bool(self.get_token().strip())

    def set_token(self, token: Optional[str]) -> None:
        """Set or clear the bot token."""
        self._update(lambda d: d.__setitem__("bot_token", (token or "").strip()))

    def set_enabled(self, enabled: bool) -> None:
        self._update(lambda d: d.__setitem__("enabled", bool(enabled)))

    def get_allowed_chat_ids(self) -> list[int]:
        """Chat ids the bot will process updates from. Empty = reject everyone."""
        out = []
        for v in self.load().get("allowed_chat_ids") or []:
            try:
                out.append(int(v))
            except (TypeError, ValueError):
                continue
        return out

    def set_allowed_chat_ids(self, chat_ids: list[int]) -> None:
        """Replace the chat id allowlist."""
        cleaned = []
        for v in chat_ids or []:
            try:
                cleaned.append(int(v))
            except (TypeError, ValueError):
                continue
        self._update(lambda d: d.__setitem__("allowed_chat_ids", cleaned))

    def is_chat_allowed(self, chat_id: int) -> bool:
        """Whether a chat may talk to the bot. An empty allowlist allows no one."""
        try:
            chat_id = int(chat_id)
        except (TypeError, ValueError):
            return False
        return chat_id in set(self.get_allowed_chat_ids())

    def get_update_offset(self) -> int:
        return int(self.load().get("update_offset") or 0)

    def set_update_offset(self, offset: int) -> None:
        self._update(lambda d: d.__setitem__("update_offset", int(offset or 0)))

    # ── bindings ─────────────────────────────────────────────────────────────

    def list_bindings(self) -> list[dict[str, Any]]:
        return list(self.load().get("bindings") or [])

    def get_binding(self, chat_id: int) -> Optional[dict[str, Any]]:
        for b in self.list_bindings():
            if int(b.get("chat_id", 0)) == int(chat_id):
                return dict(b)
        return None

    def upsert_binding(
        self,
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
        def _apply(data: dict[str, Any]) -> dict[str, Any]:
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
            return dict(existing)

        return self._update(_apply)

    def touch_binding(self, chat_id: int) -> None:
        """Update last_message_at for a binding (best-effort, no error if missing)."""
        def _apply(data: dict[str, Any]) -> Any:
            for b in data.get("bindings") or []:
                if int(b.get("chat_id", 0)) == int(chat_id):
                    b["last_message_at"] = _utc_iso()
                    return None
            return _NO_WRITE

        self._update(_apply)

    def reset_conversation(self, chat_id: int, new_conversation_id: str) -> Optional[dict[str, Any]]:
        """Replace conversation_id for a binding (used by `/reset`)."""
        def _apply(data: dict[str, Any]) -> Any:
            for b in data.get("bindings") or []:
                if int(b.get("chat_id", 0)) == int(chat_id):
                    b["conversation_id"] = new_conversation_id
                    return dict(b)
            return _NO_WRITE

        return self._update(_apply)

    def remove_binding(self, chat_id: int) -> bool:
        def _apply(data: dict[str, Any]) -> Any:
            original = list(data.get("bindings") or [])
            kept = [b for b in original if int(b.get("chat_id", 0)) != int(chat_id)]
            if len(kept) == len(original):
                return _NO_WRITE
            data["bindings"] = kept
            return True

        return bool(self._update(_apply))


#: Returned by an update function that changed nothing: skip the write.
_NO_WRITE = object()

#: The bot of the running code (see the module docstring).
STORE = TelegramStore()


def for_workspace(workspace: Optional[str]) -> TelegramStore:
    """The store of exactly ``workspace``'s bot (the default's when empty)."""
    return STORE.for_workspace(workspace)


def defines(workspace: Optional[str]) -> bool:
    return STORE.defines(workspace)


def effective_workspace() -> str:
    return STORE.effective_workspace()


def defined_workspaces() -> list[str]:
    return STORE.defined_workspaces()


def remove_workspace(workspace: Optional[str]) -> bool:
    return STORE.remove_workspace(workspace)


def load() -> dict[str, Any]:
    return STORE.load()


def get_token() -> str:
    return STORE.get_token()


def is_enabled() -> bool:
    return STORE.is_enabled()


def has_token() -> bool:
    return STORE.has_token()


def set_token(token: Optional[str]) -> None:
    STORE.set_token(token)


def set_enabled(enabled: bool) -> None:
    STORE.set_enabled(enabled)


def get_allowed_chat_ids() -> list[int]:
    return STORE.get_allowed_chat_ids()


def set_allowed_chat_ids(chat_ids: list[int]) -> None:
    STORE.set_allowed_chat_ids(chat_ids)


def is_chat_allowed(chat_id: int) -> bool:
    return STORE.is_chat_allowed(chat_id)


def get_update_offset() -> int:
    return STORE.get_update_offset()


def set_update_offset(offset: int) -> None:
    STORE.set_update_offset(offset)


def list_bindings() -> list[dict[str, Any]]:
    return STORE.list_bindings()


def get_binding(chat_id: int) -> Optional[dict[str, Any]]:
    return STORE.get_binding(chat_id)


def upsert_binding(**kwargs: Any) -> dict[str, Any]:
    return STORE.upsert_binding(**kwargs)


def touch_binding(chat_id: int) -> None:
    STORE.touch_binding(chat_id)


def reset_conversation(chat_id: int, new_conversation_id: str) -> Optional[dict[str, Any]]:
    return STORE.reset_conversation(chat_id, new_conversation_id)


def remove_binding(chat_id: int) -> bool:
    return STORE.remove_binding(chat_id)
