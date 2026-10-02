"""
The base class for a channel's background loop.

A channel service owns one asyncio task (a long poll, a websocket, an IMAP
sweep), a per-chat lock so each chat has at most one in-flight agent run,
and the status dict the Connectors page shows. Subclasses implement
:meth:`_connect` (verify credentials, fill ``status``) and :meth:`_run`
(the loop, which must return when ``self._stop_event`` is set), and call
:meth:`handle_message` for every inbound message that reached them.

The service is registered on ``common.singletons.supervisor`` through
:func:`leased_service`, so with several backend replicas exactly one runs
the loop, the same way the Telegram poller is held.

Inbound email, Slack, Discord and Teams channels differ only in the
transport, which is why the whole dispatch (allowlist, commands, unbound
chats, the run itself, the reply) lives here once.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from .commands import handle_command, unbound_reply
from .store import ChannelStore
from .turns import TurnResult, run_turn, wake_unanswered_async

log = logging.getLogger("channels.service")


def _utc_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class ChannelService:
    #: The channel name; also the store name and the run's ``source``.
    name: str = ""
    #: Config fields that must be set before the service can start.
    required_fields: tuple[str, ...] = ()

    def __init__(self, store: ChannelStore) -> None:
        self.store = store
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._status: dict[str, Any] = {
            "running": False,
            "last_poll": None,
            "last_error": None,
            "identity": None,
        }
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self._logged_disallowed: set[str] = set()

    # ── status ───────────────────────────────────────────────────────────────

    @property
    def status(self) -> dict[str, Any]:
        return dict(self._status, running=self.is_running())

    def is_running(self) -> bool:
        return bool(self._task and not self._task.done())

    def wanted(self) -> bool:
        """Whether the loop should run at all: enabled and configured."""
        try:
            return bool(self.store.is_enabled() and self.store.is_configured(*self.required_fields))
        except Exception:  # noqa: BLE001 - a store failure reads as "not wanted"
            return False

    def _set_error(self, exc: Any) -> None:
        self._status["last_error"] = str(exc)[:300] if exc else None

    def _touch(self) -> None:
        self._status["last_poll"] = _utc_iso()

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self.is_running():
            return
        if not self.store.is_configured(*self.required_fields):
            self._status["last_error"] = "not configured"
            return
        if not self.store.is_enabled():
            self._status["last_error"] = f"{self.name} integration disabled"
            return
        self._stop_event = asyncio.Event()
        try:
            await self._connect()
            self._status["last_error"] = None
        except Exception as exc:  # noqa: BLE001 - reported on the status card
            self._set_error(f"connect failed: {exc}")
            return
        self._status["running"] = True
        log.info("%s channel started: %s", self.name, self._status.get("identity") or "")
        self._task = asyncio.create_task(self._guarded_run(), name=f"channel-{self.name}")

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            except Exception:  # noqa: BLE001 - stopping must not raise
                log.debug("channel task ended with an error on stop", exc_info=True)
        self._task = None
        self._stop_event = None
        self._status["running"] = False

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def _guarded_run(self) -> None:
        backoff = 1.0
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self._run()
                backoff = 1.0
                if not self._stop_event.is_set():
                    # A loop that returned on its own (socket closed): reconnect.
                    await self._sleep(backoff)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 - the loop must keep running
                self._set_error(exc)
                log.warning("%s channel loop error: %s", self.name, exc)
                await self._sleep(backoff)
                backoff = min(backoff * 2, 30.0)
        self._status["running"] = False

    async def _sleep(self, seconds: float) -> None:
        assert self._stop_event is not None
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def stopping(self) -> bool:
        return self._stop_event is None or self._stop_event.is_set()

    # ── transport hooks ──────────────────────────────────────────────────────

    async def _connect(self) -> None:
        """Verify credentials and fill ``self._status['identity']``."""

    async def _run(self) -> None:
        """The loop. Return when :meth:`stopping` is true."""
        raise NotImplementedError

    async def test(self) -> dict[str, Any]:
        """Verify the saved credentials without changing the running state."""
        try:
            await self._connect()
            return {"ok": True, "identity": self._status.get("identity")}
        except Exception as exc:  # noqa: BLE001 - reported to the UI
            return {"ok": False, "error": str(exc)[:300]}

    async def send_text(self, chat_key: str, text: str, **kwargs: Any) -> None:
        """Send a plain message to a chat. Used for replies and notifications."""
        raise NotImplementedError

    async def send_typing(self, chat_key: str) -> None:
        """Show a typing indicator, if the transport has one."""

    async def send_result(self, chat_key: str, result: TurnResult, **kwargs: Any) -> None:
        """Send a turn's outcome. Override to render buttons natively."""
        await self.send_text(chat_key, result.reply, **kwargs)

    # ── dispatch ─────────────────────────────────────────────────────────────

    def _lock(self, chat_key: str) -> asyncio.Lock:
        lock = self._chat_locks.get(chat_key)
        if lock is None:
            lock = asyncio.Lock()
            self._chat_locks[chat_key] = lock
        return lock

    def chat_allowed(self, chat_key: str) -> bool:
        """Gate every inbound message on the allowlist; log once per chat."""
        if self.store.is_allowed(chat_key):
            return True
        if chat_key not in self._logged_disallowed:
            self._logged_disallowed.add(chat_key)
            log.info("%s message dropped: chat %s is not on the allowlist", self.name, chat_key)
        return False

    async def handle_message(self, chat_key: str, text: str, *,
                             attachments: Optional[list[dict[str, Any]]] = None,
                             title: Optional[str] = None,
                             **send_kwargs: Any) -> Optional[TurnResult]:
        """One inbound message: allowlist, command, binding check, run, reply.

        ``send_kwargs`` are passed through to :meth:`send_text` /
        :meth:`send_result` (a thread id, a reply-to header). Returns the turn
        result when a run happened, else None.
        """
        chat_key = str(chat_key)
        text = (text or "").strip()
        if not self.chat_allowed(chat_key):
            return None

        reply = handle_command(self.store, chat_key, text, title=title)
        if reply is not None:
            await self._safe_send(chat_key, reply, **send_kwargs)
            return None

        binding = self.store.get_binding(chat_key)
        if not binding or not binding.get("workspace") or not (
            binding.get("agent_id") or binding.get("flow_id")
        ):
            await self._safe_send(chat_key, unbound_reply(binding, chat_key), **send_kwargs)
            await wake_unanswered_async(self.name, chat_key,
                                        str((binding or {}).get("workspace") or "") or None, text)
            return None

        if not text and not attachments:
            return None

        async with self._lock(chat_key):
            async def _on_handoff(message: str) -> None:
                await self._safe_send(chat_key, message, **send_kwargs)

            async def _on_typing() -> None:
                await self.send_typing(chat_key)

            result = await run_turn(
                self.store, chat_key, binding, text, attachments,
                source=self.name, on_handoff=_on_handoff, on_typing=_on_typing,
            )
            try:
                await self.send_result(chat_key, result, **send_kwargs)
            except Exception as exc:  # noqa: BLE001 - fall back to plain text
                log.warning("%s send_result failed for chat %s: %s", self.name, chat_key, exc)
                await self._safe_send(chat_key, result.reply, **send_kwargs)
            return result

    async def _safe_send(self, chat_key: str, text: str, **kwargs: Any) -> None:
        try:
            await self.send_text(chat_key, text, **kwargs)
        except Exception as exc:  # noqa: BLE001 - logged, never raised into the loop
            log.warning("%s send failed for chat %s: %s", self.name, chat_key, exc)
            self._set_error(exc)


def leased_service(service: ChannelService):
    """Wrap a channel service for ``common.singletons.supervisor``."""
    from common.singletons import LeasedService

    return LeasedService(
        role=f"channel_{service.name}",
        start=service.start,
        stop=service.stop,
        is_running=service.is_running,
        wanted=service.wanted,
    )


__all__ = ["ChannelService", "leased_service"]
