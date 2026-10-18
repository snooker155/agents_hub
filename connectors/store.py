"""
A connector's configuration, one document per workspace that defines it.

Held by :class:`common.docstore.DocStore` under the key ``"state"`` (store
name ``"channel_<name>"``, the name the documents were written under before
the chat channels went away, so existing rows keep working)::

    {
        "config": {"api_token": "<secret>", ...},   # connector-specific fields
        "enabled": false
    }

Per workspace (docs/connectors.md "Connectors per workspace"): the document
above is the default workspace's; another workspace that defines the
connector for itself keeps its own under ``"state@<workspace>"``. A
connector lives in the workspace that defines it, and the default
workspace's live everywhere.

Every setter is a read-modify-write inside ``store.transaction()``.

Secrets are write-only from the API: :meth:`ConnectorStore.public_config`
replaces each field listed in ``secret_fields`` with a ``has_<field>`` flag.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from common.docstore import DocStore

_STATE_KEY = "state"

DEFAULT_WORKSPACE = "default"


@dataclass
class ConfigField:
    """One field of a connector's form (the Connectors page reads these)."""
    key: str
    secret: bool = False
    #: "text", "password", "number", "textarea", "select"
    kind: str = "text"
    placeholder: str = ""
    options: list[str] = field(default_factory=list)
    #: Set on fields the connector cannot work without.
    required: bool = False
    #: ``{field: value}``: the form hides this field while another field
    #: holds that value.
    hidden_when: dict[str, str] = field(default_factory=dict)
    #: ``{field: value}``: a required field becomes optional while another
    #: field holds that value.
    optional_when: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "secret": self.secret, "kind": self.kind,
            "placeholder": self.placeholder, "options": list(self.options),
            "required": self.required,
            "hidden_when": dict(self.hidden_when), "optional_when": dict(self.optional_when),
        }


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
    (``ConnectorStore("jira")``) then picks that workspace's connector, or the
    default's when it defines none, exactly as a run there would."""
    from common.workspace_context import _workspace_ctx
    token = _workspace_ctx.set(str(workspace or DEFAULT_WORKSPACE))
    try:
        return fn(*args, **kwargs)
    finally:
        _workspace_ctx.reset(token)


class ConnectorStore:
    """A connector's config, one document per workspace that defines it.

    The store a module creates (``ConnectorStore("jira")``) follows the
    running code: it reads and writes the document of the current workspace
    (:func:`scope_workspace`) when that workspace defined the connector, else
    the default workspace's. A store bound with :meth:`for_workspace` reads
    and writes exactly one workspace's document, with no fallback: the routes
    that edit a workspace's connectors use that.
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

    def for_workspace(self, workspace: Optional[str]) -> "ConnectorStore":
        """This connector's store for exactly ``workspace`` (default when empty)."""
        return ConnectorStore(self.name, secret_fields=self.secret_fields, defaults=self.defaults,
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
        return {"config": dict(self.defaults), "enabled": False}

    def _coerce(self, data: Any) -> dict[str, Any]:
        out = self._default_state()
        if not isinstance(data, dict):
            return out
        cfg = data.get("config")
        if isinstance(cfg, dict):
            out["config"].update(cfg)
        out["enabled"] = bool(data.get("enabled"))
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


__all__ = ["ConfigField", "ConnectorStore", "DEFAULT_WORKSPACE", "in_workspace", "scope_workspace"]
