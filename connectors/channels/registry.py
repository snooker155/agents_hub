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

Bots per workspace (docs/connectors.md "Connectors per workspace"): the
default workspace's bot is ``SPEC.service`` and serves every workspace. A
workspace that defines the channel for itself (its own document in the
store) gets its own service instance, bound to that document, with its own
loop and lease role (``channel_slack@team-a``): :func:`service_for` finds or
makes it, :func:`resync` brings it in line with its config, :func:`drop`
stops and forgets it, and :func:`effective_service` answers which bot serves
a workspace (its own, else the default's).
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .service import ChannelService, lease_role, leased_service
from .store import DEFAULT_WORKSPACE, ChannelStore

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
    #: Builds the service of a workspace's own bot from its bound store;
    #: by default the class of ``service``.
    make_service: Optional[Callable[[ChannelStore], ChannelService]] = None
    #: The services of workspaces that define the channel for themselves,
    #: by workspace name (the default's is ``service``).
    instances: dict[str, ChannelService] = field(default_factory=dict, repr=False)

    def inbound_url_for(self, workspace: Optional[str] = None) -> Optional[str]:
        """The inbound webhook a workspace's bot is reached at: the default's
        plain, a workspace's own with ``?workspace=``."""
        if not self.inbound_url:
            return None
        ws = str(workspace or DEFAULT_WORKSPACE)
        return self.inbound_url if ws == DEFAULT_WORKSPACE else f"{self.inbound_url}?workspace={ws}"

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


__all__ = ["ChannelSpec", "ConfigField", "register", "load_builtin", "get", "all_channels", "names", "resync",
           "service_for", "effective_service", "drop", "register_all", "discover", "BUILTIN_MODULES"]


# ── bots per workspace ───────────────────────────────────────────────────────

def _ws(workspace: Optional[str]) -> str:
    return str(workspace or "").strip() or DEFAULT_WORKSPACE


def _defines(spec: ChannelSpec, ws: str) -> bool:
    try:
        return bool(spec.store.defines(ws))
    except Exception:  # noqa: BLE001 - an unreadable store reads as "not defined"
        log.debug("channel %s: could not tell whether %s defines it", spec.name, ws, exc_info=True)
        return False


def service_for(name: str, workspace: Optional[str] = None, *,
                create: bool = True) -> Optional[ChannelService]:
    """The service of the bot ``workspace`` defines for itself: the
    channel's own for the default workspace, else that workspace's instance,
    made on first use when the workspace defines the channel (``create``).
    None when the channel is unknown or the workspace defines no bot."""
    spec = get(name)
    if spec is None:
        return None
    ws = _ws(workspace)
    if ws == DEFAULT_WORKSPACE:
        return spec.service
    svc = spec.instances.get(ws)
    if svc is not None or not create or not _defines(spec, ws):
        return svc
    factory = spec.make_service or type(spec.service)
    # setdefault: a sender on another thread may have made it meanwhile.
    return spec.instances.setdefault(ws, factory(spec.store.for_workspace(ws)))


def effective_service(name: str, workspace: Optional[str] = None) -> Optional[ChannelService]:
    """The bot that serves ``workspace``: its own when it defines one, else
    the default workspace's (which serves every workspace)."""
    spec = get(name)
    if spec is None:
        return None
    ws = _ws(workspace)
    if ws != DEFAULT_WORKSPACE and _defines(spec, ws):
        return service_for(name, ws) or spec.service
    return spec.service


def _supervisor():
    try:
        from common.singletons import supervisor
        return supervisor
    except Exception:  # noqa: BLE001 - no supervisor (a tool subprocess): nothing to register on
        return None


def _register(spec: ChannelSpec, svc: ChannelService) -> None:
    """Put a workspace's loop on the singleton supervisor, once."""
    if not spec.has_loop:
        return
    sup = _supervisor()
    if sup is not None and not sup.has(lease_role(spec.name, svc.workspace)):
        sup.add(leased_service(svc))


async def _unregister(spec: ChannelSpec, ws: str) -> None:
    sup = _supervisor()
    if sup is not None:
        await sup.remove(lease_role(spec.name, ws))


async def drop(name: str, workspace: Optional[str]) -> bool:
    """Stop a workspace's own bot and forget it (its document is the
    caller's to remove). The default workspace's bot is never dropped."""
    spec = get(name)
    ws = _ws(workspace)
    if spec is None or ws == DEFAULT_WORKSPACE:
        return False
    svc = spec.instances.pop(ws, None)
    if svc is not None:
        try:
            await svc.stop()
        except Exception:  # noqa: BLE001 - dropping must not fail on a stuck loop
            log.debug("channel %s@%s did not stop cleanly", name, ws, exc_info=True)
    await _unregister(spec, ws)
    return svc is not None


async def resync(name: str, workspace: Optional[str] = None) -> Optional[ChannelService]:
    """Bring the bot serving ``name`` in ``workspace`` in line with its
    stored config and return its service.

    The default workspace's bot is the channel's own service. A workspace
    that defines the channel gets its instance, made and registered on the
    supervisor when missing, then started, restarted or stopped like the
    default's. A workspace that no longer defines it has its instance
    stopped and dropped, and gets None.
    """
    spec = get(name)
    if spec is None:
        return None
    ws = _ws(workspace)
    if ws != DEFAULT_WORKSPACE and not _defines(spec, ws):
        await drop(name, ws)
        return None
    svc = service_for(name, ws)
    if svc is None:
        return None
    if ws != DEFAULT_WORKSPACE:
        _register(spec, svc)
    if spec.has_loop:
        if svc.wanted():
            await svc.restart()
        else:
            await svc.stop()
    return svc


def register_all(supervisor) -> list[str]:
    """Startup: every channel's default bot, and the bot of every workspace
    that defines one, on ``supervisor``; plus :func:`discover` on every tick
    so a workspace that defines a bot later (or on another replica) is picked
    up. Returns the lease roles registered."""
    roles: list[str] = []
    for spec in all_channels():
        if not spec.has_loop:
            continue
        if not supervisor.has(lease_role(spec.name)):
            supervisor.add(leased_service(spec.service))
            roles.append(lease_role(spec.name))
        for ws in _defined(spec):
            svc = service_for(spec.name, ws)
            if svc is not None and not supervisor.has(lease_role(spec.name, ws)):
                supervisor.add(leased_service(svc))
                roles.append(lease_role(spec.name, ws))
    supervisor.add_discoverer(discover)
    return roles


def _defined(spec: ChannelSpec) -> list[str]:
    try:
        return [w for w in spec.store.defined_workspaces() if w and w != DEFAULT_WORKSPACE]
    except Exception:  # noqa: BLE001 - an unreadable store lists nothing
        log.debug("channel %s: defined workspaces unreadable", spec.name, exc_info=True)
        return []


async def discover() -> None:
    """Make, register and drop workspace bots to match the stores: a
    workspace that defined a bot since startup gets one, a workspace that
    removed its own loses it. Runs at the start of every supervisor tick."""
    import asyncio

    sup = _supervisor()
    for spec in all_channels():
        if not spec.has_loop and not spec.instances:
            continue
        defined = set(await asyncio.to_thread(_defined, spec))
        for ws in sorted(defined):
            svc = service_for(spec.name, ws)
            if svc is not None and sup is not None:
                _register(spec, svc)
        for ws in [w for w in list(spec.instances) if w not in defined]:
            await drop(spec.name, ws)
