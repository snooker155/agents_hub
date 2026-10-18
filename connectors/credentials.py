"""
Credential connectors: a service the hub reaches out to with a token, with
no inbound loop of its own.

Jira is a set of fields (a base URL, a token, an email) and a "test" call,
plus the tools that use it. This registry gives such connectors one shape,
so one set of routes (``dashboard/backend/routes/connectors.py``) and one
form on the Connectors page serve all of them.

A connector package exports ``CREDENTIALS`` (a :class:`CredentialSpec`) or
``CREDENTIALS_LIST``; the store is a :class:`connectors.store.ConnectorStore`
(secrets write-only, ``public_config`` masks them).
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from connectors.store import ConfigField, ConnectorStore

log = logging.getLogger("connectors.credentials")


@dataclass
class CredentialSpec:
    name: str
    store: ConnectorStore
    fields: list[ConfigField] = field(default_factory=list)
    #: Verifies the saved credentials; returns {"ok", "identity"?, "error"?}.
    #: Sync, run on a thread by the route.
    test: Optional[Callable[[], dict[str, Any]]] = None
    #: Extra public state the page shows (an OAuth status, a list of
    #: connections); sync, may be None.
    extra: Optional[Callable[[], dict[str, Any]]] = None
    #: Which fields must be set for the connector to count as configured.
    required: tuple[str, ...] = ()

    def is_configured(self) -> bool:
        return self.store.is_configured(*self.required)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "fields": [f.to_dict() for f in self.fields],
            "required": list(self.required),
        }


_SPECS: dict[str, CredentialSpec] = {}
_LOADED = False

#: Modules that export ``CREDENTIALS`` (one spec) or ``CREDENTIALS_LIST``
#: (several). Order is display order.
BUILTIN_MODULES = (
    "connectors.trackers",
)


def register(spec: CredentialSpec) -> CredentialSpec:
    _SPECS[spec.name] = spec
    return spec


def load_builtin() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    for mod_name in BUILTIN_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:  # noqa: BLE001 - one broken connector must not hide the rest
            log.warning("connector %s not loaded: %s", mod_name, exc)
            continue
        specs = list(getattr(mod, "CREDENTIALS_LIST", []) or [])
        one = getattr(mod, "CREDENTIALS", None)
        if isinstance(one, CredentialSpec):
            specs.append(one)
        for spec in specs:
            if isinstance(spec, CredentialSpec):
                register(spec)


def get(name: str) -> Optional[CredentialSpec]:
    load_builtin()
    return _SPECS.get(str(name or "").strip().lower())


def all_specs() -> list[CredentialSpec]:
    load_builtin()
    return list(_SPECS.values())


def names() -> list[str]:
    return [s.name for s in all_specs()]


__all__ = ["CredentialSpec", "ConfigField", "register", "load_builtin", "get", "all_specs", "names",
           "BUILTIN_MODULES"]
