"""
The Dashboard's load widgets (``GET /api/stats/overview``).

One call gathers what the front page shows about how the hub is used, so the
page does not fan out to five endpoints on every live refresh:

- ``runs``: agent runs per day for the last ``days`` days by outcome, and the
  last 24 hours' success rate and durations;
- ``services``: the agent services, their replicas and the turns they
  answered in the last 24 hours;
- ``live``: what runs right now, of every kind (agent runs, flows, loops,
  teams), the launch queue, and the last few runs that ended.

Each section is computed on its own and a failure leaves that section with
an ``error`` instead of failing the page.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

_DONE = {"completed"}
_FAILED = {"failed", "error", "timeout"}
_ACTIVE = {"running", "pending", "stopping", "awaiting_input", "stop"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: Any) -> Optional[datetime]:
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _section(name: str, fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - one widget's trouble must not blank the others
        log.warning("dashboard overview: %s failed", name, exc_info=True)
        return {"error": str(exc)}


def _day_keys(now: datetime, days: int) -> List[str]:
    start = (now - timedelta(days=days - 1)).date()
    return [(start + timedelta(days=i)).isoformat() for i in range(days)]


def _percentile(values: List[int], q: float) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return int(ordered[idx])


def _run_duration_ms(run: Dict[str, Any]) -> Optional[int]:
    started, finished = _parse(run.get("started_at")), _parse(run.get("finished_at"))
    if started and finished and finished >= started:
        return int((finished - started).total_seconds() * 1000)
    ms = run.get("duration_ms")
    return int(ms) if isinstance(ms, (int, float)) and ms > 0 else None


def _runs_section(runs: List[Dict[str, Any]], now: datetime, days: int) -> Dict[str, Any]:
    """Runs per day by outcome and the last 24 hours' success rate and durations."""
    from common.pricing import EVALUATION_CHANNELS

    keys = _day_keys(now, days)
    per_day = {k: {"date": k, "completed": 0, "failed": 0, "other": 0}
               for k in keys}
    day_since = keys[0]
    since_24h = now - timedelta(hours=24)

    last24 = {"total": 0, "completed": 0, "failed": 0, "active": 0, "other": 0}
    durations: List[int] = []
    channels: Dict[str, int] = {}

    for r in runs:
        if (r.get("channel") or "") in EVALUATION_CHANNELS:
            continue
        ts = r.get("started_at") or r.get("created_at") or ""
        started = _parse(ts)
        if started is None:
            continue
        status = str(r.get("status") or "")
        day = started.date().isoformat()

        if day >= day_since and day in per_day:
            bucket = per_day[day]
            if status in _DONE:
                bucket["completed"] += 1
            elif status in _FAILED:
                bucket["failed"] += 1
            else:
                bucket["other"] += 1

        if started >= since_24h:
            last24["total"] += 1
            if status in _DONE:
                last24["completed"] += 1
            elif status in _FAILED:
                last24["failed"] += 1
            elif status in _ACTIVE:
                last24["active"] += 1
            else:
                last24["other"] += 1
            ms = _run_duration_ms(r) if status in _DONE else None
            if ms is not None:
                durations.append(ms)
            channel = (r.get("channel") or "").strip() or "other"
            channels[channel] = channels.get(channel, 0) + 1

    finished = last24["completed"] + last24["failed"]
    return {
        "runs": {
            "days": days,
            "per_day": [per_day[d] for d in keys],
            "last_24h": {
                **last24,
                "success_rate": round(last24["completed"] / finished * 100, 1) if finished else None,
                "avg_duration_ms": int(sum(durations) / len(durations)) if durations else None,
                "p95_duration_ms": _percentile(durations, 0.95),
                "channels": dict(sorted(channels.items(), key=lambda kv: -kv[1])),
            },
        },
    }


#: An active record with no sign of life for this long is listed apart as
#: stale: its process most likely died without closing it.
STALE_AFTER = timedelta(minutes=10)
#: Without a heartbeat at all, only the start time tells.
STALE_WITHOUT_HEARTBEAT = timedelta(hours=1)
_LIVE_STATUSES = {"pending", "running", "stopping", "awaiting_input"}


def _stale(rec: Dict[str, Any], now: datetime) -> bool:
    beat = _parse(rec.get("heartbeat_at"))
    if beat is not None:
        return now - beat > STALE_AFTER
    started = _parse(rec.get("started_at") or rec.get("created_at"))
    return started is not None and now - started > STALE_WITHOUT_HEARTBEAT


def _agent_is_live(run: Dict[str, Any]) -> bool:
    status = str(run.get("status") or "")
    # ``stop`` is a stop request until the run ends; one with a finish time
    # ended (common.health.RUNNING_RUNS_SQL).
    return status in _LIVE_STATUSES or (status == "stop" and not run.get("finished_at"))


def _live(workspace: Optional[str], runs: List[Dict[str, Any]], now: datetime,
          limit: int = 8, recent: int = 5) -> Dict[str, Any]:
    from common import entity_runs

    names: Dict[str, str] = {}
    try:
        from agents import registry
        names = {a.id: getattr(a, "name", a.id) for a in registry.list_agents()}
    except Exception:  # noqa: BLE001 - agents are then shown by id
        log.debug("dashboard overview: agent names unavailable", exc_info=True)

    def agent_item(r: Dict[str, Any]) -> Dict[str, Any]:
        agent = r.get("agent_id") or ""
        return {"kind": "agent", "run_id": r.get("run_id"), "entity_id": agent,
                "name": names.get(agent, agent) or "?", "title": r.get("title") or "",
                "status": r.get("status"), "channel": r.get("channel") or "",
                "workspace": r.get("workspace") or "", "task_id": r.get("task_id"),
                "session_id": r.get("session_id"), "service_id": r.get("service_id"),
                "started_at": r.get("started_at"), "finished_at": r.get("finished_at"),
                "duration_ms": _run_duration_ms(r)}

    def entity_item(r: Dict[str, Any]) -> Dict[str, Any]:
        return {"kind": r.get("kind"), "run_id": r.get("run_id"), "entity_id": r.get("entity_id"),
                "name": r.get("title") or r.get("entity_id") or "?", "title": r.get("title") or "",
                "status": r.get("status"), "workspace": r.get("workspace") or "",
                "task_id": r.get("task_id"), "session_id": r.get("session_id"),
                "started_at": r.get("started_at"), "finished_at": r.get("finished_at"),
                "heartbeat_at": r.get("heartbeat_at"), "duration_ms": _run_duration_ms(r)}

    active = [{**agent_item(r), "stale": _stale(r, now)} for r in runs if _agent_is_live(r)]
    active += [{**entity_item(r), "stale": _stale(r, now)}
               for r in entity_runs.list_runs(statuses=sorted(_LIVE_STATUSES), workspace=workspace)]
    live = sorted((a for a in active if not a["stale"]), key=lambda a: a.get("started_at") or "", reverse=True)
    stale = sorted((a for a in active if a["stale"]), key=lambda a: a.get("started_at") or "", reverse=True)
    by_kind: Dict[str, int] = {}
    for a in live:
        by_kind[a["kind"]] = by_kind.get(a["kind"], 0) + 1

    # A run the watchdog closed long after its process died carries the
    # sweep's time as its finish (managers/run_watchdog.py): not "lately".
    ended = [agent_item(r) for r in runs
             if r.get("finished_at") and not _agent_is_live(r) and r.get("settled_by") != "watchdog"]
    ended += [entity_item(r) for r in entity_runs.list_runs(
        statuses=["completed", "failed", "stopped"], workspace=workspace, limit=recent * 2)
        if r.get("stop_reason") != "orphan"]
    ended.sort(key=lambda a: a.get("finished_at") or "", reverse=True)
    for e in ended:
        if e["status"] == "stop":
            e["status"] = "stopped"

    return {"total": len(live), "by_kind": by_kind, "items": live[:limit],
            "stale_total": len(stale), "stale": stale[:3],
            "recent": ended[:recent]}


def overview(workspace: Optional[str] = None, *, days: int = 14, principal: Any = None) -> Dict[str, Any]:
    """Everything the Dashboard's load and spend widgets show. ``workspace``
    narrows runs, spend and services."""
    from managers import run_manager

    days = max(1, min(int(days or 14), 90))
    now = _now()
    runs: List[Dict[str, Any]] = []
    try:
        runs = run_manager.load_runs()
        if workspace:
            runs = [r for r in runs if (r.get("workspace") or "").strip() == workspace]
    except Exception:  # noqa: BLE001 - the sections below then report empty
        log.warning("dashboard overview: runs unavailable", exc_info=True)

    out: Dict[str, Any] = {"generated_at": now.isoformat(), "days": days}
    out["runs"] = _section("runs", lambda: _runs_section(runs, now, days)["runs"])
    out["live"] = _section("live", lambda: _live(workspace, runs, now))
    return out
