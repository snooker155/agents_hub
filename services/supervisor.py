"""
The service supervisor: keeps every service's replicas at its desired state.

One tick (:func:`reconcile_once`) walks every service:

- a paused service has every replica stopped;
- an active one is brought up to ``replicas_min`` (a replica that died is
  simply not live any more, so this is also what replaces it), and replicas
  beyond the minimum that have been idle for ``idle_stop_seconds`` are
  stopped, least recently used first;
- a service whose replicas keep dying (three failures or crashes within
  :data:`CRASH_WINDOW_SECONDS`) is paused with the reason ``crash loop``
  rather than restarted forever; the operator resumes it once the cause is
  fixed;
- replicas of a service that no longer exists are stopped.

With chat execution on ``instances`` the tick also makes sure the default
workspace's runner exists, so its warm replica is up before the first chat
turn rather than started by it.

With several backend replicas only the holder of the ``services`` lease
(common/leases.py) reconciles; the others tick and try to take it.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from instances import store as istore
from services import replicas, store

log = logging.getLogger(__name__)

LEASE_ROLE = "services"
TICK_SECONDS = float(os.environ.get("AGENTS_HUB_SERVICES_TICK_SECONDS", "10"))
CRASH_WINDOW_SECONDS = 300.0
CRASH_LIMIT = 3


def _utc(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _record_crashes(service: Dict[str, Any]) -> None:
    """Journal each replica that ended on its own (failed, or stopped without
    being asked) once, so the crash loop guard can count them."""
    sid = str(service["service_id"])
    page = istore.list_instances(limit=200, service_id=sid, live=False, kinds=istore.CARRIER_KINDS)
    for rep in page["items"]:
        if rep.get("crash_recorded"):
            continue
        ended_by_us = bool(rep.get("stop_requested_at")) or str(
            rep.get("carrier_error") or "").startswith("service:") or str(
            rep.get("last_activity") or "").startswith("service:")
        crashed = rep.get("state") == "failed" or (
            rep.get("state") == "stopped" and not ended_by_us
            and rep.get("carrier_exit_code") not in (0, None))
        istore.update(rep["instance_id"], crash_recorded=True)
        if crashed:
            store.add_event(sid, "replica_crashed",
                            str(rep.get("carrier_error") or rep.get("error") or "process exited"),
                            rep["instance_id"])


def _crash_looping(service: Dict[str, Any]) -> bool:
    since = (datetime.now(timezone.utc) - timedelta(seconds=CRASH_WINDOW_SECONDS)).isoformat()
    return store.recent_events(str(service["service_id"]),
                               ("replica_crashed", "replica_failed"), since) >= CRASH_LIMIT


def reconcile_service(service: Dict[str, Any]) -> Dict[str, int]:
    counts = {"started": 0, "stopped": 0}
    sid = str(service["service_id"])
    live = replicas.live_replicas(service)

    if service.get("status") != store.STATUS_ACTIVE:
        for rep in live:
            if replicas.stop_replica(service, rep, reason="paused"):
                counts["stopped"] += 1
        return counts

    _record_crashes(service)
    if _crash_looping(service):
        store.pause(sid, "crash loop: replicas kept dying, fix the cause and resume")
        for rep in live:
            if replicas.stop_replica(service, rep, reason="crash loop"):
                counts["stopped"] += 1
        return counts

    want_min = int(service.get("replicas_min") or 0)
    want_max = max(want_min, int(service.get("replicas_max") or 0))
    while len(live) < want_min:
        try:
            live.append(replicas.start_replica(service, reason="below minimum"))
            counts["started"] += 1
        except Exception:  # noqa: BLE001 - journaled by start_replica; the next tick tries again
            log.warning("service %s: could not start a replica", sid, exc_info=True)
            break

    # Idle replicas beyond the minimum, least recently active first.
    idle_after = int(service.get("idle_stop_seconds") or 0)
    surplus = len(live) - want_min
    if surplus > 0 and idle_after > 0:
        load = replicas.loads(live)
        idle = [r for r in live
                if r.get("state") == "standby" and load.get(str(r["instance_id"]), 0) == 0
                and replicas.idle_seconds(r) >= idle_after]
        idle.sort(key=lambda r: str(r.get("last_activity_at") or ""))
        for rep in idle[:surplus]:
            if replicas.stop_replica(service, rep, reason="idle"):
                counts["stopped"] += 1
    # More live replicas than the maximum allows (the maximum was lowered):
    # stop the idle ones beyond it.
    over = len(live) - counts["stopped"] - want_max
    if over > 0:
        load = replicas.loads(live)
        spare = [r for r in live if load.get(str(r["instance_id"]), 0) == 0]
        for rep in spare[:over]:
            if replicas.stop_replica(service, rep, reason="above maximum"):
                counts["stopped"] += 1
    return counts


def _stop_orphans() -> int:
    """Replicas whose service is gone."""
    from instances import carrier
    stopped = 0
    known = {s["service_id"] for s in store.list_services(limit=5000)}
    page = istore.list_instances(limit=1000, live=True, kinds=istore.CARRIER_KINDS)
    for rep in page["items"]:
        sid = rep.get("service_id")
        if sid and sid not in known:
            if carrier.stop(rep["instance_id"], reason="service: removed"):
                stopped += 1
            istore.update(rep["instance_id"], service_id=None)
    return stopped


def ensure_default_runner() -> Optional[Dict[str, Any]]:
    """The default workspace's runner, created so its warm replica is up
    before the first chat turn. Only when chat turns run on instances."""
    from common.config import chat_execution
    if chat_execution() != "instances":
        return None
    from services import routing
    env_id, env_name = routing.default_environment("default")
    return store.ensure_runner("default", env_id, env_name)


def reconcile_once() -> Dict[str, int]:
    totals = {"services": 0, "started": 0, "stopped": 0, "orphans": 0}
    try:
        ensure_default_runner()
    except Exception:  # noqa: BLE001 - the runner is created on first use anyway
        log.debug("default runner setup failed", exc_info=True)
    for service in store.list_services(limit=5000):
        totals["services"] += 1
        try:
            counts = reconcile_service(service)
        except Exception:  # noqa: BLE001 - one service's trouble must not stop the sweep
            log.exception("service %s: reconcile failed", service.get("service_id"))
            continue
        totals["started"] += counts["started"]
        totals["stopped"] += counts["stopped"]
    try:
        totals["orphans"] = _stop_orphans()
    except Exception:  # noqa: BLE001
        log.debug("orphan sweep failed", exc_info=True)
    return totals


class ServiceSupervisor:
    """The reconcile loop, on the ``services`` lease."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._leader = False
        self.last: Dict[str, Any] = {}

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def holds_lease(self) -> bool:
        return self._leader

    async def start(self) -> None:
        if self.is_running():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="service-supervisor")

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
        if result.get("started") or result.get("stopped") or result.get("orphans"):
            log.info("services: started %d, stopped %d replica(s)%s",
                     result["started"], result["stopped"],
                     f", {result['orphans']} orphan(s)" if result.get("orphans") else "")
        return result

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - a failed tick is retried on the next one
                log.exception("service supervisor tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                pass


supervisor = ServiceSupervisor()

__all__ = ["ServiceSupervisor", "reconcile_once", "reconcile_service", "supervisor",
           "ensure_default_runner", "LEASE_ROLE"]
