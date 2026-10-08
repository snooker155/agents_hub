"""
The Dashboard's load and spend widgets (``GET /api/stats/overview``).

One call gathers what the front page shows about how the hub is used, so the
page does not fan out to five endpoints on every live refresh:

- ``runs``: agent runs per day for the last ``days`` days by outcome, and the
  last 24 hours' success rate and durations;
- ``costs``: spend today, over 7 and 30 days, per day, and the agents and
  models that cost the most over 30 days, priced exactly as the Costs page
  prices them (common/costs_report.py ``_run_cost``);
- ``endpoint``: calls through the hub's ``/v1`` endpoint (common/serving.py);
- ``local_models``: the model runtime's loaded models and its gateway counts
  (the Models page's Runtime load card). Reading it here never starts the
  runtime, unlike ``GET /api/models/local/runtime``: a front page that boots
  a runtime somebody left off would be a surprise;
- ``services``: the agent services, their replicas and the turns they
  answered in the last 24 hours;
- ``live``: what runs right now, of every kind (agent runs, flows, loops,
  teams, scenarios), the launch queue, and the last few runs that ended.

Each section is computed on its own and a failure leaves that section with
an ``error`` instead of failing the page.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

#: How long the dashboard waits for the model runtime before saying it did
#: not answer: the page polls, so a slow runtime must not hold it up.
RUNTIME_TIMEOUT = 2.0

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


def _runs_and_costs(runs: List[Dict[str, Any]], now: datetime, days: int) -> Dict[str, Any]:
    """Both sections walk the same runs, so they are built in one pass."""
    from common.costs_report import _run_cost
    from common.pricing import EVALUATION_CHANNELS, load_price_map, run_tokens

    prices = load_price_map()
    keys = _day_keys(now, days)
    per_day = {k: {"date": k, "completed": 0, "failed": 0, "other": 0, "cost": 0.0, "tokens": 0}
               for k in keys}
    day_since = keys[0]
    since_24h = now - timedelta(hours=24)
    today = now.date().isoformat()
    since_7d = (now - timedelta(days=7)).isoformat()
    since_30d = (now - timedelta(days=30)).isoformat()

    last24 = {"total": 0, "completed": 0, "failed": 0, "active": 0, "other": 0}
    durations: List[int] = []
    channels: Dict[str, int] = {}
    spend = {"today": 0.0, "last_7d": 0.0, "last_30d": 0.0, "tokens_30d": 0, "runs_30d": 0}
    by_agent: Dict[str, Dict[str, Any]] = {}
    by_model: Dict[str, Dict[str, Any]] = {}

    for r in runs:
        if (r.get("channel") or "") in EVALUATION_CHANNELS:
            continue
        ts = r.get("started_at") or r.get("created_at") or ""
        started = _parse(ts)
        if started is None:
            continue
        status = str(r.get("status") or "")
        iso = started.isoformat()
        day = started.date().isoformat()

        cost = 0.0
        tokens = 0
        if iso >= since_30d:
            cost = _run_cost(r, prices)
            inbound, outbound = run_tokens(r)
            tokens = inbound + outbound
            spend["last_30d"] += cost
            spend["tokens_30d"] += tokens
            spend["runs_30d"] += 1
            if iso >= since_7d:
                spend["last_7d"] += cost
            if day == today:
                spend["today"] += cost
            agent = (r.get("agent_id") or "").strip() or "(unknown)"
            a = by_agent.setdefault(agent, {"key": agent, "runs": 0, "cost": 0.0, "tokens": 0})
            a["runs"] += 1
            a["cost"] += cost
            a["tokens"] += tokens
            model = (r.get("model") or "").strip()
            if model:
                mkey = f"{(r.get('provider') or '').strip() or 'unknown'}/{model}"
                m = by_model.setdefault(mkey, {"key": mkey, "runs": 0, "cost": 0.0, "tokens": 0})
                m["runs"] += 1
                m["cost"] += cost
                m["tokens"] += tokens

        if day >= day_since and day in per_day:
            bucket = per_day[day]
            if status in _DONE:
                bucket["completed"] += 1
            elif status in _FAILED:
                bucket["failed"] += 1
            else:
                bucket["other"] += 1
            bucket["cost"] += cost
            bucket["tokens"] += tokens

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
    for b in per_day.values():
        b["cost"] = round(b["cost"], 4)

    def _top(bucket: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows = sorted(bucket.values(), key=lambda b: (-b["cost"], -b["runs"], b["key"]))[:5]
        return [{**b, "cost": round(b["cost"], 4)} for b in rows]

    return {
        "runs": {
            "days": days,
            "per_day": [{k: v for k, v in per_day[d].items() if k not in ("cost", "tokens")}
                        for d in keys],
            "last_24h": {
                **last24,
                "success_rate": round(last24["completed"] / finished * 100, 1) if finished else None,
                "avg_duration_ms": int(sum(durations) / len(durations)) if durations else None,
                "p95_duration_ms": _percentile(durations, 0.95),
                "channels": dict(sorted(channels.items(), key=lambda kv: -kv[1])),
            },
        },
        "costs": {
            "today": round(spend["today"], 4),
            "last_7d": round(spend["last_7d"], 4),
            "last_30d": round(spend["last_30d"], 4),
            "tokens_30d": spend["tokens_30d"],
            "runs_30d": spend["runs_30d"],
            "per_day": [{"date": d, "cost": per_day[d]["cost"], "tokens": per_day[d]["tokens"]}
                        for d in keys],
            "top_agents": _top(by_agent),
            "top_models": _top(by_model),
        },
    }


def _budget(workspace: Optional[str]) -> Optional[Dict[str, Any]]:
    from common import budget as budget_mod
    status = budget_mod.budget_status(workspace or "default")
    if not (status.get("hard_limit_usd") or status.get("soft_limit_usd")):
        return None
    return {k: status.get(k) for k in ("period", "spend", "hard_limit_usd", "soft_limit_usd",
                                       "hard_exceeded", "soft_exceeded")}


def _endpoint(now: datetime, days: int, user_id: Optional[str]) -> Dict[str, Any]:
    """Calls through ``/v1``: last 24 hours, last ``days`` days and per day."""
    from common import db, serving

    since_days = (now - timedelta(days=days - 1)).date().isoformat()
    week = serving.usage(since=since_days, limit_recent=0, user_id=user_id)
    day = serving.usage(since=(now - timedelta(hours=24)).isoformat(), limit_recent=0, user_id=user_id)

    clause, args = serving._where(since_days, None, user_id)
    keys = _day_keys(now, days)
    counts = {k: 0 for k in keys}
    for row in db.get_conn().execute(f"SELECT at FROM serving_usage{clause}", tuple(args)).fetchall():
        at = _parse(row["at"])
        if at is not None and at.date().isoformat() in counts:
            counts[at.date().isoformat()] += 1

    def _sum(usage: Dict[str, Any]) -> Dict[str, Any]:
        totals = dict(usage.get("totals") or {})
        totals["errors"] = sum(int(r.get("errors") or 0) for r in usage.get("rows") or [])
        return totals

    return {
        "last_24h": _sum(day),
        "window": _sum(week),
        "per_day": [{"date": k, "requests": counts[k]} for k in keys],
        "top_models": [{"key": f"{r['provider']}/{r['model']}", "requests": r["requests"],
                        "total_tokens": r["total_tokens"], "errors": r["errors"]}
                       for r in (week.get("rows") or [])[:5]],
    }


def _local_models() -> Dict[str, Any]:
    from providers import local_models as lm

    cfg = lm.runtime_settings()
    out: Dict[str, Any] = {"configured": bool(cfg["url"]), "managed": bool(cfg.get("managed")),
                           "state": None, "ok": False, "models": 0, "loaded": [],
                           "memory": None, "usage": None, "usage_outdated": False, "error": None}
    if not cfg["url"]:
        return out
    if cfg.get("managed"):
        from providers import model_runtime_host as host
        state = host.status()
        out["state"] = state.get("state")
        out["stopped_by_user"] = state.get("stopped_by_user")
        # The state is this process's: another process (a worker, or the
        # backend before a reload) may have started the runtime, so a
        # runtime answering on its port counts as running.
        if out["state"] != "running":
            if out["state"] in ("preparing", "starting") or host.probe(timeout=1.0) is None:
                return out
            out["state"] = "running"
    client = lm.RuntimeClient(timeout=RUNTIME_TIMEOUT)
    try:
        listing = client.listing()
    except lm.LocalModelError as exc:
        out["error"] = str(exc)
        return out
    out["ok"] = True
    models = list(listing.get("models") or [])
    out["models"] = len(models)
    out["loaded"] = [{"name": m.get("name") or m.get("file"), "kind": m.get("kind"),
                      "engine": m.get("engine"), "size_bytes": m.get("size_bytes")}
                     for m in models if m.get("loaded")]
    try:
        out["memory"] = client.memory()
    except lm.LocalModelError:
        out["memory"] = None
    try:
        usage = client.usage()
    except lm.LocalModelError as exc:
        out["usage_outdated"] = exc.status_code == 404
        return out
    rows = usage.get("rows") or []
    by_source: Dict[str, int] = {}
    by_model: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        by_source[r.get("source") or "direct"] = by_source.get(r.get("source") or "direct", 0) + int(r.get("requests") or 0)
        m = by_model.setdefault(r.get("model") or "?", {"model": r.get("model") or "?", "requests": 0,
                                                       "errors": 0, "gen_tokens": 0, "gen_ms": 0.0,
                                                       "duration_ms": 0})
        m["requests"] += int(r.get("requests") or 0)
        m["errors"] += int(r.get("errors") or 0)
        m["gen_tokens"] += int(r.get("gen_tokens") or 0)
        m["gen_ms"] += float(r.get("gen_ms") or 0.0)
        m["duration_ms"] += int(r.get("duration_ms") or 0)
    top = sorted(by_model.values(), key=lambda m: -m["requests"])[:5]
    for m in top:
        m["tokens_per_second"] = (round(m["gen_tokens"] * 1000 / m["gen_ms"], 1)
                                  if m["gen_ms"] > 0 and m["gen_tokens"] > 0 else None)
        m["avg_ms"] = int(m["duration_ms"] / m["requests"]) if m["requests"] else None
    out["usage"] = {"since": usage.get("since"), "totals": usage.get("totals") or {},
                    "by_source": by_source, "top_models": top}
    return out


def _services(workspace: Optional[str], runs: List[Dict[str, Any]], now: datetime,
              principal: Any) -> Dict[str, Any]:
    from common import access
    from services import replicas, store

    items = store.list_services(workspace=workspace, include_runners=True)
    if principal is not None:
        items = access.filter_by_workspace(principal, items)
    since = now - timedelta(hours=24)
    turns: Dict[str, Dict[str, int]] = {}
    for r in runs:
        sid = r.get("service_id")
        if not sid:
            continue
        started = _parse(r.get("started_at") or r.get("created_at"))
        if started is None or started < since:
            continue
        t = turns.setdefault(str(sid), {"turns": 0, "failed": 0})
        t["turns"] += 1
        if str(r.get("status") or "") in _FAILED:
            t["failed"] += 1

    rows = []
    totals = {"services": 0, "active": 0, "paused": 0, "replicas_live": 0, "replicas_total": 0,
              "turns_24h": 0, "failed_24h": 0}
    for s in items:
        sid = str(s["service_id"])
        counts = replicas.summary(sid)
        paused = s.get("status") == store.STATUS_PAUSED
        t = turns.get(sid, {"turns": 0, "failed": 0})
        rows.append({
            "service_id": sid, "name": s.get("name") or sid, "agent_id": s.get("agent_id"),
            "kind": s.get("kind"), "workspace": s.get("workspace"), "paused": paused,
            "replicas_live": counts.get("live", 0), "replicas_total": counts.get("total", 0),
            "replicas_max": s.get("replicas_max"),
            "turns_24h": t["turns"], "failed_24h": t["failed"],
        })
        totals["services"] += 1
        totals["paused" if paused else "active"] += 1
        totals["replicas_live"] += counts.get("live", 0)
        totals["replicas_total"] += counts.get("total", 0)
        totals["turns_24h"] += t["turns"]
        totals["failed_24h"] += t["failed"]
    rows.sort(key=lambda r: (-r["turns_24h"], -r["replicas_live"], r["name"]))
    return {"totals": totals, "items": rows[:6]}


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

    queue: Dict[str, Any] = {}
    try:
        from common import run_queue
        stats = run_queue.stats()
        queue = {"queued": stats.get("queued", 0),
                 "oldest_queued_seconds": stats.get("oldest_queued_seconds", 0.0)}
    except Exception:  # noqa: BLE001 - the queue is then left out
        log.debug("dashboard overview: run queue unavailable", exc_info=True)

    return {"total": len(live), "by_kind": by_kind, "items": live[:limit],
            "stale_total": len(stale), "stale": stale[:3],
            "recent": ended[:recent], "queue": queue}


def overview(workspace: Optional[str] = None, *, days: int = 14, principal: Any = None,
             serving_user_id: Optional[str] = None) -> Dict[str, Any]:
    """Everything the Dashboard's load and spend widgets show. ``workspace``
    narrows runs, spend and services; the endpoint and the model runtime are
    the whole hub's. ``serving_user_id`` narrows ``/v1`` calls to one
    person's (a non-administrator in ``multi`` mode)."""
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
    rc = _section("runs", lambda: _runs_and_costs(runs, now, days))
    out["runs"] = rc.get("runs", rc)
    out["costs"] = rc.get("costs", rc)
    if "error" not in out["costs"]:
        out["costs"]["budget"] = _section("budget", lambda: {"value": _budget(workspace)}).get("value")
    out["endpoint"] = _section("endpoint", lambda: _endpoint(now, days, serving_user_id))
    out["local_models"] = _section("local_models", _local_models)
    out["services"] = _section("services", lambda: _services(workspace, runs, now, principal))
    out["live"] = _section("live", lambda: _live(workspace, runs, now))
    return out
