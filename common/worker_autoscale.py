"""
Autoscaling the workers: one more when launches wait, one fewer when a
worker has been idle a while, inside the bounds the Cluster page sets.

The policy lives in the ``cluster`` doc store (key ``autoscale``), off by
default:

- ``min_workers`` / ``max_workers``: the bounds. Fewer live workers than the
  minimum starts the difference.
- ``scale_up_wait_seconds``: a run waiting in the queue longer than this,
  with no free slot on any worker, starts more: as many as the waiting runs
  need at the workers' concurrency, up to the maximum. An instance waiting
  for a worker counts when no worker lives at all (a carrier takes no slot).
- ``scale_down_idle_minutes``: a worker that has run nothing and carries no
  instance for this long is asked to drain (common/worker_fleet.py), the
  hub's own workers first, never below the minimum and never while
  something waits.
- ``cooldown_seconds``: the pause after any action, so a worker that has
  just been started gets time to register before the next decision.

One backend decides (lease ``worker_autoscale``), every
:data:`TICK_SECONDS`, and only in the ``api`` role: a backend in the ``all``
role launches its runs itself. Each action is kept with its reason
(``autoscale_events``) for the page.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

LEASE_ROLE = "worker_autoscale"
TICK_SECONDS = 15.0
EVENTS_KEPT = 30

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "min_workers": 1,
    "max_workers": 4,
    "scale_up_wait_seconds": 30,
    "scale_down_idle_minutes": 10,
    "cooldown_seconds": 90,
}
LIMITS = {"min_workers": (0, 100), "max_workers": (1, 100), "scale_up_wait_seconds": (0, 3600),
          "scale_down_idle_minutes": (1, 1440), "cooldown_seconds": (10, 3600)}

CARRIER_KIND = "instance"


def _store():
    from common.docstore import DocStore
    return DocStore("cluster")


def policy() -> Dict[str, Any]:
    stored = _store().get("autoscale") or {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def save_policy(changes: Dict[str, Any]) -> Dict[str, Any]:
    """Merge ``changes`` into the policy. Raises ValueError for a value out
    of bounds or a maximum below the minimum."""
    merged = policy()
    for key, value in (changes or {}).items():
        if key not in DEFAULTS:
            continue
        if key == "enabled":
            merged[key] = bool(value)
            continue
        lo, hi = LIMITS[key]
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a whole number") from None
        if not lo <= number <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
        merged[key] = number
    if merged["max_workers"] < merged["min_workers"]:
        raise ValueError("max_workers must not be below min_workers")
    _store().put("autoscale", merged)
    return merged


def events() -> List[Dict[str, Any]]:
    return list(_store().get("autoscale_events") or [])


def _record(action: str, reason: str, **details: Any) -> Dict[str, Any]:
    event = {"at": datetime.now(timezone.utc).isoformat(), "action": action, "reason": reason, **details}
    store = _store()
    store.put("autoscale_events", [event, *(store.get("autoscale_events") or [])][:EVENTS_KEPT])
    log.info("autoscale %s: %s %s", action, reason, details or "")
    return event


# ── the decision ─────────────────────────────────────────────────────────────

def queue_waiting() -> Dict[str, Any]:
    """Launches waiting for a worker: one-shot runs (count, oldest wait) and
    instance carriers."""
    from common import db
    rows = db.get_conn().execute(
        "SELECT kind, COUNT(*) AS n, MIN(created_at) AS oldest FROM run_queue "
        "WHERE status = 'queued' GROUP BY kind").fetchall()
    now = datetime.now(timezone.utc)
    runs, carriers, oldest = 0, 0, 0.0
    for r in rows:
        if str(r["kind"]) == CARRIER_KIND:
            carriers += int(r["n"])
            continue
        runs += int(r["n"])
        try:
            at = datetime.fromisoformat(str(r["oldest"]).replace("Z", "+00:00"))
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            oldest = max(oldest, (now - at).total_seconds())
        except (TypeError, ValueError):
            pass
    return {"runs": runs, "carriers": carriers, "oldest_seconds": oldest}


def decide(pol: Dict[str, Any], workers: List[Dict[str, Any]], waiting: Dict[str, Any],
           idle_for: Dict[str, float], pending: int = 0) -> Dict[str, Any]:
    """What to do now: ``{"action": "up", "count", "reason"}``,
    ``{"action": "down", "member_id", "reason"}`` or ``{"action": None,
    "reason"}``. ``pending`` is the workers started but not registered yet:
    they count toward the bounds, and nothing more starts while they come
    up. Pure: the caller supplies every input."""
    live = [w for w in workers if not w.get("draining")]
    n = len(live)
    lo, hi = int(pol["min_workers"]), int(pol["max_workers"])
    if n + pending < lo:
        return {"action": "up", "count": lo - n - pending, "reason": "below_min"}
    if pending:
        return {"action": None, "reason": "starting"}
    free = sum(max(0, int(w.get("concurrency") or 0) - int(w.get("runs") or 0)) for w in live)
    if waiting["runs"] and waiting["oldest_seconds"] >= pol["scale_up_wait_seconds"] and free == 0:
        if n >= hi:
            return {"action": None, "reason": "at_max"}
        per = max((int(w.get("concurrency") or 0) for w in live), default=0) or 4
        return {"action": "up", "count": max(1, min(hi - n, math.ceil(waiting["runs"] / per))),
                "reason": "queue_waiting"}
    if waiting["carriers"] and n == 0:
        return {"action": "up", "count": 1, "reason": "instances_waiting"} if hi else {
            "action": None, "reason": "at_max"}
    if waiting["runs"] or waiting["carriers"] or n <= lo or any(w.get("draining") for w in workers):
        return {"action": None, "reason": "steady"}
    idle_after = float(pol["scale_down_idle_minutes"]) * 60
    candidates = [w for w in live if w.get("stoppable") and not w.get("runs")
                  and idle_for.get(w["member_id"], 0.0) >= idle_after]
    if not candidates:
        return {"action": None, "reason": "steady"}
    # The hub's own first, then the one idle longest.
    candidates.sort(key=lambda w: (w.get("origin") != "hub", -idle_for.get(w["member_id"], 0.0)))
    return {"action": "down", "member_id": candidates[0]["member_id"], "reason": "idle"}


class Autoscaler:
    """The loop. Keeps how long each worker has been idle (in this process:
    a backend that takes the lease over starts counting afresh)."""

    def __init__(self) -> None:
        self.idle_since: Dict[str, float] = {}
        self.last_action = float("-inf")
        self.last_decision: Dict[str, Any] = {}
        self._halt = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _idle_for(self, workers: List[Dict[str, Any]], now: float) -> Dict[str, float]:
        out = {}
        for w in workers:
            mid = w["member_id"]
            if w.get("runs") or w.get("carriers") or w.get("draining"):
                self.idle_since.pop(mid, None)
                continue
            out[mid] = now - self.idle_since.setdefault(mid, now)
        for mid in list(self.idle_since):
            if mid not in out:
                self.idle_since.pop(mid, None)
        return out

    def tick(self, now: Optional[float] = None) -> Dict[str, Any]:
        from common import worker_fleet
        now = time.time() if now is None else now
        pol = policy()
        # A drained Kubernetes worker is taken away whether or not
        # autoscaling is on: the Cluster page's Stop needs it too.
        for pod in worker_fleet.reconcile():
            _record("retire", "drained", member_id=pod)
        workers = worker_fleet.workers()
        idle = self._idle_for(workers, now)
        if not pol["enabled"]:
            self._remember({"action": None, "reason": "off"})
            return self.last_decision
        decision = decide(pol, workers, queue_waiting(), idle, worker_fleet.pending(workers))
        self._remember(decision)
        if not decision["action"] or now - self.last_action < float(pol["cooldown_seconds"]):
            return decision
        try:
            if decision["action"] == "up":
                started = worker_fleet.add(decision["count"])
                _record("up", decision["reason"], started=started)
            else:
                worker_fleet.stop(decision["member_id"])
                self.idle_since.pop(decision["member_id"], None)
                _record("down", decision["reason"], member_id=decision["member_id"])
        except Exception as exc:  # noqa: BLE001 - the failure is the event the page shows
            _record("error", decision["reason"], error=f"{type(exc).__name__}: {exc}"[:400])
        self.last_action = now
        return decision

    def _remember(self, decision: Dict[str, Any]) -> None:
        """Keep the latest decision where every replica's page reads it;
        written only when it changes."""
        if decision != self.last_decision:
            _store().put("autoscale_last", {**decision, "at": datetime.now(timezone.utc).isoformat()})
        self.last_decision = decision

    def _loop(self) -> None:
        from common import leases
        while not self._halt.wait(TICK_SECONDS):
            try:
                if leases.hold(LEASE_ROLE, TICK_SECONDS * 4):
                    self.tick()
            except Exception:  # noqa: BLE001 - the loop must outlive one bad tick
                log.exception("autoscale tick failed")

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._halt.clear()
            self._thread = threading.Thread(target=self._loop, name="worker-autoscale", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._halt.set()


autoscaler = Autoscaler()


def status() -> Dict[str, Any]:
    return {"policy": policy(), "events": events(), "last_decision": _store().get("autoscale_last") or {}}


__all__ = ["Autoscaler", "DEFAULTS", "autoscaler", "decide", "events", "policy", "queue_waiting",
           "save_policy", "status"]
