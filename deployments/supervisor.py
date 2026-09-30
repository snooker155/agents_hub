"""Keeps every running project deployment alive: one tick refreshes each
deployment whose desired state is ``running``, restarts a service that
exited (when the deployment asks for that) and pauses a crash loop. The
same shape as services/supervisor.py, on its own lease
(``project_deployments``), so with several backend replicas one of them
does the work."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from . import service, store

log = logging.getLogger(__name__)

LEASE_ROLE = "project_deployments"
TICK_SECONDS = float(os.environ.get("AGENTS_HUB_DEPLOYMENTS_TICK_SECONDS", "15"))


def reconcile_once() -> Dict[str, int]:
    totals = {"checked": 0, "restarted": 0, "paused": 0}
    for dep in store.list_all():
        if dep.desired != "running":
            continue
        totals["checked"] += 1
        try:
            counts = service.reconcile(dep)
        except Exception:  # noqa: BLE001 - one broken deployment must not stop the pass
            log.warning("deployment %s: reconcile failed", dep.id, exc_info=True)
            continue
        totals["restarted"] += counts.get("restarted", 0)
        totals["paused"] += counts.get("paused", 0)
    return totals


class DeploymentSupervisor:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._leader = False
        self.last: Dict[str, Any] = {}

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="deployment-supervisor")

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
                await asyncio.to_thread(leases.release, LEASE_ROLE)
            except Exception:  # noqa: BLE001 - the lease lapses on its own TTL
                log.debug("lease release failed during supervisor stop", exc_info=True)

    async def tick(self) -> Optional[Dict[str, int]]:
        from common import leases
        ttl = max(TICK_SECONDS * 3, leases.DEFAULT_TTL_SECONDS)
        self._leader = await asyncio.to_thread(leases.hold, LEASE_ROLE, ttl)
        if not self._leader:
            return None
        result = await asyncio.to_thread(reconcile_once)
        self.last = {**result, "at": datetime.now(timezone.utc).isoformat()}
        if result.get("restarted") or result.get("paused"):
            log.info("deployments: restarted %d service(s), paused %d deployment(s)",
                     result["restarted"], result["paused"])
        return result

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - a failed tick is retried on the next one
                log.exception("deployment supervisor tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                pass


supervisor = DeploymentSupervisor()

__all__ = ["DeploymentSupervisor", "reconcile_once", "supervisor", "LEASE_ROLE"]
