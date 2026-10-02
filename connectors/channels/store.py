"""
One document per channel, in the database.

Held by :class:`common.docstore.DocStore` under the key ``"state"`` (store
name ``"channel_<name>"``), shaped like the Telegram document it generalises::

    {
        "config": {"bot_token": "<secret>", ...},   # channel-specific fields
        "enabled": false,
        "allowed": ["C0123", "U0456"],              # chat keys, strings
        "cursor": {},                               # transport bookkeeping
        "bindings": [
            {
                "chat_key": "C0123",
                "agent_id": "swe_agent",
                "flow_id": null,
                "workspace": "demo",
                "conversation_id": "uuid-...",
                "title": "#general",
                "created_at": "2026-...",
                "last_message_at": null
            }
        ]
    }

A chat key is whatever identifies a conversation on the transport: a Slack
channel id, a Discord channel id, a Teams conversation id, an email address.
Always a string, compared as one.

Every setter is a read-modify-write inside ``store.transaction()``, atomic
across every process and host.

Secrets are write-only from the API: :meth:`ChannelStore.public_config`
replaces each field listed in ``secret_fields`` with a ``has_<field>`` flag.

The allowlist is the gate on who the channel will talk to at all: an empty
list rejects every chat once the channel is configured (the safe default,
the operator opts chats in). A binding's ``workspace`` can only be set
through the REST API, never from an inbound command (see ``commands.py``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from common.docstore import DocStore

_STATE_KEY = "state"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChannelStore:
    def __init__(self, name: str, *, secret_fields: Iterable[str] = (),
                 defaults: Optional[dict[str, Any]] = None) -> None:
        self.name = name
        self.secret_fields = tuple(secret_fields)
        self.defaults = dict(defaults or {})
        self._store = DocStore(f"channel_{name}")

    # ── state ────────────────────────────────────────────────────────────────

    def _default_state(self) -> dict[str, Any]:
        return {
            "config": dict(self.defaults),
            "enabled": False,
            "allowed": [],
            "cursor": {},
            "bindings": [],
        }

    def _coerce(self, data: Any) -> dict[str, Any]:
        out = self._default_state()
        if not isinstance(data, dict):
            return out
        cfg = data.get("config")
        if isinstance(cfg, dict):
            out["config"].update(cfg)
        out["enabled"] = bool(data.get("enabled"))
        allowed = data.get("allowed")
        out["allowed"] = [str(a) for a in allowed] if isinstance(allowed, list) else []
        cursor = data.get("cursor")
        out["cursor"] = dict(cursor) if isinstance(cursor, dict) else {}
        bindings = data.get("bindings")
        out["bindings"] = [dict(b) for b in bindings if isinstance(b, dict)] if isinstance(bindings, list) else []
        return out

    def load(self) -> dict[str, Any]:
        """The full state, secrets included (internal use only)."""
        return self._coerce(self._store.get(_STATE_KEY))

    def _update(self, fn) -> Any:
        with self._store.transaction():
            data = self._coerce(self._store.get(_STATE_KEY))
            result = fn(data)
            self._store.put(_STATE_KEY, data)
            return result

    # ── config ───────────────────────────────────────────────────────────────

    def get_config(self) -> dict[str, Any]:
        return dict(self.load().get("config") or {})

    def get(self, field: str, default: Any = "") -> Any:
        value = self.get_config().get(field)
        return default if value in (None, "") else value

    def set_config(self, values: dict[str, Any], *, clear: Iterable[str] = ()) -> dict[str, Any]:
        """Merge ``values`` into the config; ``clear`` names fields to empty.

        An empty string for a secret field means "no change" (a form that did
        not load the secret must not wipe it); clearing is explicit.
        """
        clear = set(clear)

        def _apply(data: dict[str, Any]) -> dict[str, Any]:
            cfg = data["config"]
            for key, value in (values or {}).items():
                if key in self.secret_fields and (value is None or str(value).strip() == ""):
                    continue
                cfg[key] = value.strip() if isinstance(value, str) else value
            for key in clear:
                cfg[key] = "" if isinstance(self.defaults.get(key, ""), str) else None
            return dict(cfg)

        return self._update(_apply)

    def public_config(self) -> dict[str, Any]:
        """Config safe for the UI: every secret replaced by ``has_<field>``."""
        cfg = self.get_config()
        out: dict[str, Any] = {}
        for key, value in cfg.items():
            if key in self.secret_fields:
                out[f"has_{key}"] = bool(str(value or "").strip())
            else:
                out[key] = value
        for key in self.secret_fields:
            out.setdefault(f"has_{key}", False)
        return out

    def is_configured(self, *required: str) -> bool:
        """Whether every ``required`` field (default: every secret) is set."""
        fields = required or self.secret_fields
        cfg = self.get_config()
        return all(str(cfg.get(f) or "").strip() for f in fields)

    # ── enabled ──────────────────────────────────────────────────────────────

    def is_enabled(self) -> bool:
        return bool(self.load().get("enabled"))

    def set_enabled(self, enabled: bool) -> None:
        self._update(lambda d: d.__setitem__("enabled", bool(enabled)))

    # ── allowlist ────────────────────────────────────────────────────────────

    def get_allowed(self) -> list[str]:
        return list(self.load().get("allowed") or [])

    def set_allowed(self, keys: Iterable[str]) -> None:
        cleaned = [str(k).strip() for k in (keys or []) if str(k).strip()]
        self._update(lambda d: d.__setitem__("allowed", cleaned))

    def is_allowed(self, chat_key: str) -> bool:
        """An empty allowlist allows no one."""
        key = str(chat_key or "").strip()
        return bool(key) and key in set(self.get_allowed())

    # ── cursor ───────────────────────────────────────────────────────────────

    def get_cursor(self, key: str, default: Any = None) -> Any:
        return (self.load().get("cursor") or {}).get(key, default)

    def set_cursor(self, key: str, value: Any) -> None:
        self._update(lambda d: d["cursor"].__setitem__(key, value))

    # ── bindings ─────────────────────────────────────────────────────────────

    def list_bindings(self) -> list[dict[str, Any]]:
        return list(self.load().get("bindings") or [])

    def get_binding(self, chat_key: str) -> Optional[dict[str, Any]]:
        key = str(chat_key)
        for b in self.list_bindings():
            if str(b.get("chat_key")) == key:
                return dict(b)
        return None

    def upsert_binding(self, *, chat_key: str, agent_id: str = "", flow_id: Optional[str] = None,
                       workspace: Optional[str] = None, conversation_id: Optional[str] = None,
                       title: Optional[str] = None) -> dict[str, Any]:
        """Create or update a chat's binding; an agent and a flow are exclusive."""
        key = str(chat_key)

        def _apply(data: dict[str, Any]) -> dict[str, Any]:
            for b in data["bindings"]:
                if str(b.get("chat_key")) == key:
                    b["agent_id"] = agent_id or ""
                    b["flow_id"] = flow_id
                    if workspace is not None:
                        b["workspace"] = workspace
                    if conversation_id is not None:
                        b["conversation_id"] = conversation_id
                    if title is not None:
                        b["title"] = title
                    return dict(b)
            binding = {
                "chat_key": key,
                "agent_id": agent_id or "",
                "flow_id": flow_id,
                "workspace": workspace,
                "conversation_id": conversation_id,
                "title": title,
                "created_at": _utc_iso(),
                "last_message_at": None,
            }
            data["bindings"].append(binding)
            return dict(binding)

        return self._update(_apply)

    def touch_binding(self, chat_key: str) -> None:
        key = str(chat_key)

        def _apply(data: dict[str, Any]) -> None:
            for b in data["bindings"]:
                if str(b.get("chat_key")) == key:
                    b["last_message_at"] = _utc_iso()
                    return

        self._update(_apply)

    def reset_conversation(self, chat_key: str, new_conversation_id: str) -> Optional[dict[str, Any]]:
        key = str(chat_key)

        def _apply(data: dict[str, Any]) -> Optional[dict[str, Any]]:
            for b in data["bindings"]:
                if str(b.get("chat_key")) == key:
                    b["conversation_id"] = new_conversation_id
                    return dict(b)
            return None

        return self._update(_apply)

    def remove_binding(self, chat_key: str) -> bool:
        key = str(chat_key)

        def _apply(data: dict[str, Any]) -> bool:
            before = len(data["bindings"])
            data["bindings"] = [b for b in data["bindings"] if str(b.get("chat_key")) != key]
            return len(data["bindings"]) != before

        return self._update(_apply)

    def chat_keys_for_workspace(self, workspace: Optional[str]) -> list[str]:
        """Chats eligible for a workspace's notifications: a binding with no
        workspace, or one matching. A falsy workspace means every bound chat."""
        target = (workspace or "").strip()
        out: list[str] = []
        for b in self.list_bindings():
            key = str(b.get("chat_key") or "").strip()
            if not key:
                continue
            bws = str(b.get("workspace") or "").strip()
            if not target or not bws or bws == target:
                out.append(key)
        return out


__all__ = ["ChannelStore"]
