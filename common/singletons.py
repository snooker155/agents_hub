"""
Supervisor for background services that may run on one replica only.

Some of the backend's background work is a loop that can check a lease on
every tick itself (the plan scheduler, the run watchdog, the outbox drainer:
each calls ``common.leases.hold`` and skips the tick when another replica
holds the role). The Telegram poller is not like that: it is a long-poll on
``getUpdates`` that must not run twice on one bot token, and it is started
and stopped as a whole. This supervisor owns that shape: every few seconds
it tries to hold the role's lease, starts the service when it gets it, and
stops the service when it loses it (a database outage long enough for the
lease to lapse, or an operator taking the role elsewhere).

With one replica the effect is that the service starts a moment after
startup rather than during it, which the health page shows the same way.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

log = logging.getLogger("common.singletons")

CHECK_SECONDS = 10.0


@dataclass
class LeasedService:
    role: str
    start: Callable[[], Awaitable[None]]
    stop: Callable[[], Awaitable[None]]
    is_running: Callable[[], bool]
    #: Whether the service is wanted at all right now (configuration). Checked
    #: on every tick so a Settings change reaches it without a restart.
    wanted: Callable[[], bool] = lambda: True
    #: The last outcome, for the health page.
    state: Dict[str, Any] = field(default_factory=dict)


class SingletonSupervisor:
    def __init__(self, services: Optional[List[LeasedService]] = None,
                 *, check_seconds: float = CHECK_SECONDS) -> None:
        self.services: List[LeasedService] = list(services or [])
        self.check_seconds = check_seconds
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    def add(self, service: LeasedService) -> None:
        self.services.append(service)

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> Dict[str, Dict[str, Any]]:
        return {s.role: dict(s.state, running=bool(s.is_running())) for s in self.services}

    async def start(self) -> None:
        if self.is_running():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="singleton-supervisor")

    async def stop(self) -> None:
        if self._task:
            self._stop.set()
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        from common import leases
        for svc in self.services:
            try:
                if svc.is_running():
                    await svc.stop()
            except Exception:
                log.debug("stopping %s failed", svc.role, exc_info=True)
            try:
                await asyncio.to_thread(leases.release, svc.role)
            except Exception:
                pass

    async def tick(self) -> None:
        """One pass over every service: hold what is wanted, drop what is not."""
        from common import leases

        ttl = max(self.check_seconds * 4, leases.DEFAULT_TTL_SECONDS)
        for svc in self.services:
            try:
                wanted = bool(svc.wanted())
            except Exception:
                wanted = False
            running = bool(svc.is_running())
            if not wanted:
                if running:
                    await svc.stop()
                    await asyncio.to_thread(leases.release, svc.role)
                svc.state = {"leader": False, "reason": "not configured"}
                continue
            held = await asyncio.to_thread(leases.hold, svc.role, ttl)
            if held and not running:
                try:
                    await svc.start()
                    svc.state = {"leader": True, "reason": ""}
                    log.info("%s started on this replica (lease held)", svc.role)
                except Exception as exc:  # noqa: BLE001 - reported, never fatal
                    svc.state = {"leader": True, "reason": f"start failed: {exc}"}
                    log.warning("%s could not start: %s", svc.role, exc)
            elif held:
                svc.state = {"leader": True, "reason": ""}
            elif running:
                # Lost the lease while running: another replica has the role now.
                await svc.stop()
                svc.state = {"leader": False, "reason": "lease lost"}
                log.warning("%s stopped on this replica: lease held elsewhere", svc.role)
            else:
                holder = await asyncio.to_thread(leases.holder, svc.role)
                svc.state = {"leader": False,
                             "reason": f"held by {holder.get('owner')}" if holder else "waiting"}

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:
                log.exception("singleton supervisor tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.check_seconds)
            except asyncio.TimeoutError:
                pass


supervisor = SingletonSupervisor()


def telegram_service() -> LeasedService:
    """The Telegram poller as a leased service."""
    from connectors.telegram.telegram_runner import service as tg
    from connectors.telegram import telegram_store

    return LeasedService(
        role="telegram",
        start=tg.start,
        stop=tg.stop,
        is_running=tg.is_running,
        wanted=lambda: bool(telegram_store.is_enabled() and telegram_store.has_token()),
    )


__all__ = ["LeasedService", "SingletonSupervisor", "supervisor", "telegram_service"]
