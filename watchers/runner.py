"""
The watcher loop: polls the due watchers every few seconds (docs/watchers.md).

Started with the backend next to the plan scheduler (dashboard/backend/
main.py). Only the replica holding the ``watchers`` service lease
(common/leases.py) polls, so two backends never read the same mailbox at
once and report the same message twice; the others wait and take over when
the lease lapses. Each pass runs in a worker thread: an IMAP round trip must
never block the event loop.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

log = logging.getLogger("watchers.runner")


class WatcherRunner:
    TICK_SECONDS = 10.0
    LEASE_ROLE = "watchers"

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._leader = False

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def holds_lease(self) -> bool:
        return self._leader

    async def start(self) -> None:
        if self.is_running():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="watcher-runner")

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        self._task = None
        if self._leader:
            self._leader = False
            try:
                from common import leases
                await asyncio.to_thread(leases.release, self.LEASE_ROLE)
            except Exception:  # noqa: BLE001 - the lease lapses on its own
                log.debug("watchers lease release failed", exc_info=True)

    async def _wait(self) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=self.TICK_SECONDS)
        except asyncio.TimeoutError:
            pass

    async def _loop(self) -> None:
        from common import leases
        from watchers import service

        ttl = max(self.TICK_SECONDS * 3, leases.DEFAULT_TTL_SECONDS)
        while not self._stop.is_set():
            try:
                self._leader = await asyncio.to_thread(leases.hold, self.LEASE_ROLE, ttl)
            except Exception:  # noqa: BLE001 - no lease, no polling this round
                self._leader = False
            if not self._leader:
                await self._wait()
                continue
            try:
                results = await asyncio.to_thread(service.run_due)
                for r in results:
                    if r.get("ok"):
                        if r.get("events"):
                            log.info("watcher %s: %s, woke %s", r["watcher_id"], r.get("summary"), r.get("woken"))
                    else:
                        log.warning("watcher %s failed: %s", r.get("watcher_id"), r.get("error"))
            except Exception:
                log.exception("watcher pass failed")
            await self._wait()


runner = WatcherRunner()

__all__ = ["WatcherRunner", "runner"]
