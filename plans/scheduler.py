"""
Asyncio scheduler loop for plan jobs.

Started once at FastAPI startup (same pattern as the Telegram poller). Each
tick scans plans.json for due jobs and fires them in a worker thread so file
locks and subprocess launches never block the event loop.

Agent subprocesses that create jobs via tools write to the same plans.json,
so their jobs are picked up on the next tick without any IPC.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

log = logging.getLogger("plans.scheduler")


class PlanScheduler:
    TICK_SECONDS = 20.0
    # Awaiting-input escalation is hour-scale; sweeping every 5 min is plenty and
    # keeps the 20s job tick cheap.
    ESCALATION_INTERVAL_SECONDS = 300.0

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._last_escalation: float = 0.0

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="plan-scheduler")

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        from plans import service

        while not self._stop.is_set():
            try:
                results = await asyncio.to_thread(service.run_due_jobs)
                for r in results:
                    if r.get("ok"):
                        log.info("fired job %s (%s)", r.get("job_id"), r.get("kind"))
                    else:
                        log.warning("job %s failed: %s", r.get("job_id"), r.get("error"))
            except Exception:
                log.exception("scheduler tick failed")
            # Daily state maintenance (run retention + orphan-file pruning). The
            # call is self-guarding: it no-ops until 24h have passed, so ticking
            # it every cycle is cheap and survives restarts.
            try:
                from common.maintenance import run_maintenance
                await asyncio.to_thread(run_maintenance)
            except Exception:
                log.exception("maintenance tick failed")
            # Awaiting-input escalation: remind on / auto-answer long-parked tasks.
            # Self-guarded to a 5-min cadence so the fast job tick stays cheap.
            now = time.monotonic()
            if now - self._last_escalation >= self.ESCALATION_INTERVAL_SECONDS:
                self._last_escalation = now
                try:
                    from plans.escalation import sweep_awaiting_input
                    await asyncio.to_thread(sweep_awaiting_input)
                except Exception:
                    log.exception("awaiting-input escalation tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.TICK_SECONDS)
            except asyncio.TimeoutError:
                pass


scheduler = PlanScheduler()

__all__ = ["PlanScheduler", "scheduler"]
