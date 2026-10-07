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

Two more keys serve a bot distributed through a catalog (the Slack
Marketplace, the Teams store or an organisation's app catalog,
docs/distribution.md)::

    "installs": {                                  # one per organisation
        "T0123": {"org_id": "T0123", "name": "Acme", "status": "pending",
                  "workspace": null, "agent_id": "", "bot_token": "<secret>",
                  "bot_user_id": "U0BOT", "installed_at": "...", ...}
    },
    "chat_orgs": {"C0123": "T0123"}                # which organisation a chat is in

An organisation is a Slack team or a Microsoft tenant. An approved one lets
every chat in it talk to the bot, bound to the installation's workspace and
agent; a pending one (installed from the catalog by somebody the hub does not
know) lets nobody in until an operator approves it on the Distribution page.

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


DEFAULT_WORKSPACE = "default"


def _key_for(workspace: Optional[str]) -> str:
    ws = str(workspace or "").strip()
    return _STATE_KEY if not ws or ws == DEFAULT_WORKSPACE else f"{_STATE_KEY}@{ws}"


def scope_workspace() -> Optional[str]:
    """The workspace the running code works in, for picking a connector's
    config: the run's context var, then ``AGENT_WORKSPACE``. Never the
    workspace a person last selected in the UI: a background loop or a
    request that names none uses the default workspace's connectors."""
    import os
    try:
        from common.workspace_context import _workspace_ctx, normalize_workspace_name
        return normalize_workspace_name(_workspace_ctx.get() or os.getenv("AGENT_WORKSPACE") or "")
    except Exception:  # noqa: BLE001 - no workspace context: the default's connectors
        return None


def in_workspace(workspace: Optional[str], fn, *args, **kwargs):
    """Call ``fn`` as code running in ``workspace``: a module's store
    (``ChannelStore("jira")``) then picks that workspace's connector, or the
    default's when it defines none, exactly as a run there would."""
    from common.workspace_context import _workspace_ctx
    token = _workspace_ctx.set(str(workspace or DEFAULT_WORKSPACE))
    try:
        return fn(*args, **kwargs)
    finally:
        _workspace_ctx.reset(token)


class ChannelStore:
    """A connector's config and state, one document per workspace that
    defines the connector (docs/connectors.md "Connectors per workspace").

    A connector lives in the workspace that defines it; the default
    workspace's live everywhere. The store a module creates
    (``ChannelStore("jira")``) follows the running code: it reads and writes
    the document of the current workspace (:func:`scope_workspace`) when that
    workspace defined the connector, else the default workspace's. A store
    bound with :meth:`for_workspace` reads and writes exactly one workspace's
    document, with no fallback: the routes that edit a workspace's
    connectors, and a channel loop that serves one workspace, use that.
    """

    def __init__(self, name: str, *, secret_fields: Iterable[str] = (),
                 defaults: Optional[dict[str, Any]] = None,
                 workspace: Optional[str] = None, _docstore: Optional[DocStore] = None) -> None:
        self.name = name
        self.secret_fields = tuple(secret_fields)
        self.defaults = dict(defaults or {})
        self._store = _docstore or DocStore(f"channel_{name}")
        #: None for the module's store (follows the run); a name when bound.
        self.workspace = (str(workspace).strip() or DEFAULT_WORKSPACE) if workspace is not None else None

    # ── workspaces ───────────────────────────────────────────────────────────

    def for_workspace(self, workspace: Optional[str]) -> "ChannelStore":
        """This connector's store for exactly ``workspace`` (default when empty)."""
        return ChannelStore(self.name, secret_fields=self.secret_fields, defaults=self.defaults,
                            workspace=workspace or DEFAULT_WORKSPACE, _docstore=self._store)

    def defines(self, workspace: Optional[str]) -> bool:
        """Whether ``workspace`` has a document of its own (the default always does)."""
        key = _key_for(workspace)
        return key == _STATE_KEY or self._store.exists(key)

    def effective_workspace(self) -> str:
        """The workspace whose document this store uses right now."""
        if self.workspace is not None:
            return self.workspace
        ws = scope_workspace()
        if ws and ws != DEFAULT_WORKSPACE and self._store.exists(_key_for(ws)):
            return ws
        return DEFAULT_WORKSPACE

    def defined_workspaces(self) -> list[str]:
        """The workspaces other than the default that define this connector."""
        prefix = f"{_STATE_KEY}@"
        return sorted(k[len(prefix):] for k in self._store.keys() if k.startswith(prefix))

    def remove_workspace(self, workspace: Optional[str]) -> bool:
        """Drop a workspace's own document, so it falls back to the default
        workspace's connector again. The default's own cannot be removed."""
        key = _key_for(workspace)
        if key == _STATE_KEY:
            return False
        return self._store.delete(key)

    def _key(self) -> str:
        return _key_for(self.effective_workspace())

    # ── state ────────────────────────────────────────────────────────────────

    def _default_state(self) -> dict[str, Any]:
        return {
            "config": dict(self.defaults),
            "enabled": False,
            "allowed": [],
            "cursor": {},
            "bindings": [],
            "installs": {},
            "chat_orgs": {},
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
        installs = data.get("installs")
        if isinstance(installs, dict):
            out["installs"] = {str(k): dict(v) for k, v in installs.items() if isinstance(v, dict)}
        chat_orgs = data.get("chat_orgs")
        if isinstance(chat_orgs, dict):
            out["chat_orgs"] = {str(k): str(v) for k, v in chat_orgs.items() if v}
        return out

    def load(self) -> dict[str, Any]:
        """The full state, secrets included (internal use only)."""
        return self._coerce(self._store.get(self._key()))

    def _update(self, fn) -> Any:
        key = self._key()
        with self._store.transaction():
            data = self._coerce(self._store.get(key))
            result = fn(data)
            self._store.put(key, data)
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
                       title: Optional[str] = None,
                       from_install: Optional[bool] = None) -> dict[str, Any]:
        """Create or update a chat's binding; an agent and a flow are exclusive.
        ``from_install`` marks a binding an approved installation made, which
        goes away with the installation."""
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
            if from_install:
                binding["from_install"] = True
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

    # ── catalog installations ────────────────────────────────────────────────

    #: Fields of an installation that never leave through the API.
    INSTALL_SECRETS = ("bot_token",)

    def list_installs(self) -> list[dict[str, Any]]:
        """Every installation, secrets included (internal use only)."""
        installs = self.load().get("installs") or {}
        return [dict(v, org_id=k) for k, v in sorted(installs.items())]

    def get_install(self, org_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not org_id:
            return None
        found = (self.load().get("installs") or {}).get(str(org_id))
        return dict(found, org_id=str(org_id)) if found else None

    def public_installs(self) -> list[dict[str, Any]]:
        """Installations safe for the UI: each secret as a ``has_<field>`` flag."""
        out = []
        for inst in self.list_installs():
            row = {k: v for k, v in inst.items() if k not in self.INSTALL_SECRETS}
            for key in self.INSTALL_SECRETS:
                row[f"has_{key}"] = bool(str(inst.get(key) or "").strip())
            out.append(row)
        return out

    def upsert_install(self, org_id: str, **fields: Any) -> dict[str, Any]:
        """Create or update one organisation's installation. A new one starts
        ``pending`` unless ``status`` says otherwise; an update keeps what it
        does not name (a reinstall refreshes the token, not the approval)."""
        org = str(org_id).strip()
        if not org:
            raise ValueError("an installation needs an organisation id")

        def _apply(data: dict[str, Any]) -> dict[str, Any]:
            current = data["installs"].get(org)
            if current is None:
                current = {"status": "pending", "workspace": None, "agent_id": "",
                           "installed_at": _utc_iso()}
            current.update({k: v for k, v in fields.items() if v is not None})
            current["org_id"] = org
            data["installs"][org] = current
            return dict(current)

        return self._update(_apply)

    def remove_install(self, org_id: str) -> bool:
        """Forget an organisation: its installation, its chats' organisation
        and the bindings those chats got from it."""
        org = str(org_id)

        def _apply(data: dict[str, Any]) -> bool:
            if data["installs"].pop(org, None) is None:
                return False
            chats = {k for k, v in data["chat_orgs"].items() if v == org}
            data["chat_orgs"] = {k: v for k, v in data["chat_orgs"].items() if v != org}
            data["bindings"] = [b for b in data["bindings"]
                                if not (str(b.get("chat_key")) in chats and b.get("from_install"))]
            return True

        return self._update(_apply)

    def note_chat_org(self, chat_key: str, org_id: Optional[str]) -> None:
        """Remember which organisation a chat is in. Written only when it
        changes, so a busy chat does not rewrite the document per message."""
        key, org = str(chat_key or ""), str(org_id or "")
        if not key or not org or self.org_of(key) == org:
            return
        self._update(lambda d: d["chat_orgs"].__setitem__(key, org))

    def org_of(self, chat_key: str) -> Optional[str]:
        return (self.load().get("chat_orgs") or {}).get(str(chat_key))

    def install_for_chat(self, chat_key: str) -> Optional[dict[str, Any]]:
        return self.get_install(self.org_of(chat_key))

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
