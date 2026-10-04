"""
Outbound notifications over a channel (proactive, not request/response).

The sibling of ``connectors/telegram/notify.py`` for every registered
channel: a scheduled job firing, an alert rule, an agent calling
``notify_user`` with ``channels=["slack"]`` pushes a message into the chats
bound in a workspace.

Synchronous and best-effort on purpose: the callers (``plans/service.py``,
the alert rules) run in the backend, in the scheduler thread or in an agent
subprocess, none of which share a channel's event loop. A service's async
``send_text`` is driven on a throwaway loop in a worker thread; a service may
override ``send_text_sync`` to avoid that. Failures are logged and swallowed,
the inbox stays the source of truth.

A workspace's notifications go out through the bot that serves it
(``registry.effective_service``): its own when it defines one, else the
default workspace's.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
from typing import Any, Optional

from . import registry

log = logging.getLogger("channels.notify")

_SEND_TIMEOUT = 30.0


def run_sync(coro: Any, timeout: float = _SEND_TIMEOUT) -> Any:
    """Run a coroutine to completion from sync code, on its own thread and
    loop, so it works whether or not the calling thread runs a loop."""
    box: dict[str, Any] = {}
    # The caller's context (a bot's workspace scope) goes along to the thread.
    ctx = contextvars.copy_context()

    def _target() -> None:
        try:
            box["value"] = ctx.run(asyncio.run, coro)
        except Exception as exc:  # noqa: BLE001 - re-raised below
            box["error"] = exc

    thread = threading.Thread(target=_target, name="channel-send", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError("channel send timed out")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def send_text_sync(channel: str, chat_key: str, text: str, workspace: Optional[str] = None,
                   *, service: Any = None) -> bool:
    """Send ``text`` to one chat through ``service``, else the bot serving
    ``workspace`` (its own, else the default workspace's)."""
    svc = service or registry.effective_service(channel, workspace)
    if svc is None:
        return False
    try:
        custom = getattr(svc, "send_text_sync", None)
        with _scope(svc):
            if callable(custom):
                custom(chat_key, text)
            else:
                run_sync(svc.send_text(chat_key, text))
        return True
    except Exception as exc:  # noqa: BLE001 - best-effort
        log.warning("%s send to %s failed: %s", channel, chat_key, exc)
        return False


def _scope(svc: Any):
    """The bot's workspace scope, when the service has one."""
    import contextlib
    scope = getattr(svc, "scope", None)
    return scope() if callable(scope) else contextlib.nullcontext()


def notify_workspace(channel: str, workspace: Optional[str], title: str, body: str = "") -> int:
    """Push a notification to every chat bound in ``workspace`` on ``channel``,
    through the bot serving ``workspace``.

    Returns the number of chats reached. 0 when the channel is disabled, not
    configured or has no bound chats.
    """
    svc = registry.effective_service(channel, workspace)
    if svc is None:
        return 0
    store = svc.store
    if not store.is_enabled() or not store.is_configured(*svc.required_fields):
        return 0
    text = title if not body else f"{title}\n\n{body}"
    # A workspace's own bot: every chat of it is that workspace's.
    target = None if getattr(svc, "own_workspace", None) else workspace
    sent = 0
    for key in store.chat_keys_for_workspace(target):
        if send_text_sync(channel, key, text, service=svc):
            sent += 1
    return sent


__all__ = ["notify_workspace", "send_text_sync", "run_sync"]
