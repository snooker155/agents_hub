"""
The channels this hub knows about.

A channel package (``connectors/slack``, ``connectors/discord``,
``connectors/teams``, ``connectors/mail``) exports one :class:`ChannelSpec`
named ``SPEC``; :func:`load_builtin` imports each and registers it. A package
that fails to import (a missing optional dependency) is logged and skipped,
so the Connectors page still lists the others.

Everything generic hangs off the registry: the shared routes
(``dashboard/backend/routes/channels.py``), the singleton supervisor
registration at startup, outbound notifications (``notify.py``) and the
``channel_send`` tool.
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from .service import ChannelService
from .store import ChannelStore

log = logging.getLogger("channels.registry")


@dataclass
class ConfigField:
    key: str
    secret: bool = False
    #: "text", "password", "number", "textarea", "select"
    kind: str = "text"
    placeholder: str = ""
    options: list[str] = field(default_factory=list)
    #: Set on fields the service cannot start without.
    required: bool = False
    #: ``{field: value}``: the form hides this field while another field
    #: holds that value (the mail channel's passwords under a Google sign in).
    hidden_when: dict[str, str] = field(default_factory=dict)
    #: ``{field: value}``: a required field becomes optional while another
    #: field holds that value (hosts a Google sign in fills by itself).
    optional_when: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "secret": self.secret, "kind": self.kind,
            "placeholder": self.placeholder, "options": list(self.options),
            "required": self.required,
            "hidden_when": dict(self.hidden_when), "optional_when": dict(self.optional_when),
        }


@dataclass
class ChannelSpec:
    name: str
    store: ChannelStore
    service: ChannelService
    fields: list[ConfigField] = field(default_factory=list)
    #: Whether an inbound webhook route exists for this channel, for the UI
    #: to show its URL (``/api/channels/<name>/events`` or similar).
    inbound_url: Optional[str] = None
    #: Whether the channel has a background loop (poll, websocket) at all.
    #: A channel that only receives inbound HTTP events has none.
    has_loop: bool = True
    #: Ready-made field values the form offers as a pick list (the mail
    #: channel's providers, ``connectors/mail/presets.py``): each one a dict
    #: with ``id``, ``label``, ``help_url``, ``auth`` and the ``channel``
    #: fields it fills. Empty for a channel with nothing to pre-fill.
    presets: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "fields": [f.to_dict() for f in self.fields],
            "inbound_url": self.inbound_url,
            "has_loop": self.has_loop,
            "presets": list(self.presets),
        }


_CHANNELS: dict[str, ChannelSpec] = {}
_LOADED = False

#: Modules that export ``SPEC``. Order is the order the Connectors page lists.
BUILTIN_MODULES = (
    "connectors.slack",
    "connectors.discord",
    "connectors.teams",
    "connectors.mail",
)


def register(spec: ChannelSpec) -> ChannelSpec:
    _CHANNELS[spec.name] = spec
    return spec


def load_builtin() -> None:
    """Import every built-in channel package once."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    for mod_name in BUILTIN_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:  # noqa: BLE001 - one broken channel must not hide the rest
            log.warning("channel %s not loaded: %s", mod_name, exc)
            continue
        spec = getattr(mod, "SPEC", None)
        if isinstance(spec, ChannelSpec):
            register(spec)
        else:
            log.warning("channel %s exports no SPEC", mod_name)


def get(name: str) -> Optional[ChannelSpec]:
    load_builtin()
    return _CHANNELS.get(str(name or "").strip().lower())


def all_channels() -> list[ChannelSpec]:
    load_builtin()
    ordered = [n.rsplit(".", 1)[-1] for n in BUILTIN_MODULES]
    known = sorted(_CHANNELS.values(), key=lambda s: ordered.index(s.name) if s.name in ordered else 99)
    return known


def names() -> list[str]:
    return [s.name for s in all_channels()]


__all__ = ["ChannelSpec", "ConfigField", "register", "load_builtin", "get", "all_channels", "names",
           "BUILTIN_MODULES"]
