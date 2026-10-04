"""
Telegram bots per workspace (docs/connectors.md "Connectors per workspace").

The default workspace's bot is ``telegram_runner.service`` and serves every
workspace. A workspace that defines its own bot (its own document in
``telegram_store``) gets a :class:`TelegramService` of its own, bound to that
document, with its own poller and lease role (``telegram@team-a``). The same
shape as ``connectors/channels/registry.py`` for the other channels:
:func:`service_for`, :func:`effective_service`, :func:`resync`, :func:`drop`,
:func:`register_all` and the supervisor's :func:`discover`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from connectors.telegram import telegram_store

log = logging.getLogger("telegram.bots")

DEFAULT_WORKSPACE = telegram_store.DEFAULT_WORKSPACE

#: The services of workspaces that define their own bot, by workspace.
_INSTANCES: dict = {}


def _ws(workspace: Optional[str]) -> str:
    return str(workspace or "").strip() or DEFAULT_WORKSPACE


def _default_service():
    from connectors.telegram.telegram_runner import service
    return service


def _defines(ws: str) -> bool:
    try:
        return bool(telegram_store.defines(ws))
    except Exception:  # noqa: BLE001 - an unreadable store reads as "not defined"
        log.debug("telegram: could not tell whether %s defines a bot", ws, exc_info=True)
        return False


def service_for(workspace: Optional[str] = None, *, create: bool = True):
    """The service of the bot ``workspace`` defines for itself: the module's
    for the default workspace, else that workspace's (made on first use when
    it defines a bot). None when the workspace defines none."""
    ws = _ws(workspace)
    if ws == DEFAULT_WORKSPACE:
        return _default_service()
    svc = _INSTANCES.get(ws)
    if svc is not None or not create or not _defines(ws):
        return svc
    from connectors.telegram.telegram_runner import TelegramService
    # setdefault: a sender on another thread may have made it meanwhile.
    return _INSTANCES.setdefault(ws, TelegramService(telegram_store.for_workspace(ws)))


def effective_service(workspace: Optional[str] = None):
    """The bot that serves ``workspace``: its own, else the default's."""
    ws = _ws(workspace)
    if ws != DEFAULT_WORKSPACE and _defines(ws):
        return service_for(ws) or _default_service()
    return _default_service()


def lease_role(workspace: Optional[str] = None) -> str:
    ws = _ws(workspace)
    return "telegram" if ws == DEFAULT_WORKSPACE else f"telegram@{ws}"


def _supervisor():
    try:
        from common.singletons import supervisor
        return supervisor
    except Exception:  # noqa: BLE001 - no supervisor: nothing to register on
        return None


def _register(svc) -> None:
    sup = _supervisor()
    if sup is not None and not sup.has(svc.lease_role):
        from common.singletons import telegram_service
        sup.add(telegram_service(svc))


async def drop(workspace: Optional[str]) -> bool:
    """Stop a workspace's own bot and forget it. Never the default's."""
    ws = _ws(workspace)
    if ws == DEFAULT_WORKSPACE:
        return False
    svc = _INSTANCES.pop(ws, None)
    if svc is not None:
        try:
            await svc.stop()
        except Exception:  # noqa: BLE001 - dropping must not fail on a stuck poller
            log.debug("telegram@%s did not stop cleanly", ws, exc_info=True)
    sup = _supervisor()
    if sup is not None:
        await sup.remove(lease_role(ws))
    return svc is not None


async def resync(workspace: Optional[str] = None):
    """Bring the bot of ``workspace`` in line with its stored config: start,
    restart or stop it (made and registered when missing). A workspace that
    no longer defines its own bot has it dropped, and gets None."""
    ws = _ws(workspace)
    if ws != DEFAULT_WORKSPACE and not _defines(ws):
        await drop(ws)
        return None
    svc = service_for(ws)
    if svc is None:
        return None
    if ws != DEFAULT_WORKSPACE:
        _register(svc)
    if svc.wanted():
        await svc.restart()
    else:
        await svc.stop()
    return svc


def _defined() -> list[str]:
    try:
        return [w for w in telegram_store.defined_workspaces() if w and w != DEFAULT_WORKSPACE]
    except Exception:  # noqa: BLE001 - an unreadable store lists nothing
        log.debug("telegram: defined workspaces unreadable", exc_info=True)
        return []


def register_all(supervisor) -> list[str]:
    """Startup: the default bot and every workspace's own on ``supervisor``,
    plus :func:`discover` on every tick. Returns the roles registered."""
    from common.singletons import telegram_service
    roles: list[str] = []
    if not supervisor.has(lease_role()):
        supervisor.add(telegram_service())
        roles.append(lease_role())
    for ws in _defined():
        svc = service_for(ws)
        if svc is not None and not supervisor.has(svc.lease_role):
            supervisor.add(telegram_service(svc))
            roles.append(svc.lease_role)
    supervisor.add_discoverer(discover)
    return roles


async def discover() -> None:
    """Add the bots of workspaces that defined one since, drop the bots of
    workspaces that removed theirs."""
    defined = set(await asyncio.to_thread(_defined))
    for ws in sorted(defined):
        svc = service_for(ws)
        if svc is not None:
            _register(svc)
    for ws in [w for w in list(_INSTANCES) if w not in defined]:
        await drop(ws)


__all__ = ["service_for", "effective_service", "lease_role", "resync", "drop", "register_all", "discover"]
