"""Prometheus metrics for ``GET /metrics``, hand-written (no ``prometheus_client``
dependency: the text exposition format is a handful of lines per metric, and a
dependency earns its place by saving more than that).

:func:`render` lives here rather than in the route so the Service Agent (or a
future scrape-adjacent tool) can call it directly, the same reason
``common/health.py`` sits apart from ``dashboard/backend/routes/health.py``.

Kept cheap on purpose: every number below comes from a handful of aggregate
SQL queries (``GROUP BY``, ``COUNT``, ``SUM``) or from the small in-memory
structures ``common.leases``, ``common.run_queue`` and ``notify.outbound``
already keep for the health page. Nothing here calls ``load_runs()`` — a
scrape target that gets polled every fifteen seconds must never be the query
that loads the whole run table.

Never raises. One collector failing (a missing table on a fresh database, an
unreadable price catalog) drops that metric rather than the whole page: an
operator staring at a 500 from their own metrics endpoint has lost the one
thing that was supposed to tell them what is wrong.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from common import db

log = logging.getLogger(__name__)

# (labels, value) pairs for one metric name.
_Sample = Tuple[Dict[str, Any], Any]


def _esc(value: Any) -> str:
    """Escape a label value per the Prometheus text format."""
    s = str(value)
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _line(name: str, labels: Dict[str, Any], value: Any) -> str:
    if isinstance(value, bool):
        value = int(value)
    if labels:
        label_str = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items())
        return f"{name}{{{label_str}}} {value}"
    return f"{name} {value}"


#: One metric as collected: the same numbers feed the text exposition
#: (:func:`render`) and the OTLP export (common/otel_export.py).
Metric = Dict[str, Any]


def _emit(metrics: List[Metric], name: str, help_text: str, mtype: str,
          samples: List[_Sample]) -> None:
    metrics.append({"name": name, "help": help_text, "type": mtype, "samples": samples})


def _emit_histogram(metrics: List[Metric], name: str, help_text: str, bounds: List[float],
                    counts: List[int], total: float, count: int) -> None:
    """A histogram from per-bucket (not cumulative) *counts*, one more than *bounds*
    for the overflow bucket."""
    metrics.append({"name": name, "help": help_text, "type": "histogram", "samples": [],
                    "histogram": {"bounds": bounds, "counts": counts, "sum": total, "count": count}})


def _format(metrics: List[Metric]) -> str:
    lines: List[str] = []
    for m in metrics:
        name = m["name"]
        lines.append(f"# HELP {name} {m['help']}")
        lines.append(f"# TYPE {name} {m['type']}")
        hist = m.get("histogram")
        if hist:
            running = 0
            for bound, n in zip(hist["bounds"], hist["counts"]):
                running += n
                lines.append(_line(f"{name}_bucket", {"le": f"{bound:g}"}, running))
            lines.append(_line(f"{name}_bucket", {"le": "+Inf"}, hist["count"]))
            lines.append(_line(f"{name}_sum", {}, round(hist["sum"], 6)))
            lines.append(_line(f"{name}_count", {}, hist["count"]))
            continue
        for labels, value in m["samples"]:
            lines.append(_line(name, labels, value))
    return "\n".join(lines) + "\n"


# ── collectors ───────────────────────────────────────────────────────────────

def _database_ok() -> bool:
    try:
        db.get_conn().execute("SELECT 1").fetchone()
        return True
    except Exception:  # noqa: BLE001 - never raises, one collector failing must not break /metrics
        log.debug("database collector failed", exc_info=True)
        return False


def _runs_by_status() -> Dict[str, int]:
    try:
        rows = db.get_conn().execute(
            "SELECT status, COUNT(*) AS n FROM runs GROUP BY status").fetchall()
        return {str(r["status"] or "unknown"): int(r["n"] or 0) for r in rows}
    except Exception:  # noqa: BLE001 - never raises, one collector failing must not break /metrics
        log.debug("runs_by_status collector failed", exc_info=True)
        return {}


def _running_runs() -> int:
    try:
        from common.health import RUNNING_RUNS_SQL
        row = db.get_conn().execute(f"SELECT COUNT(*) FROM runs WHERE {RUNNING_RUNS_SQL}").fetchone()
        return int(row[0] or 0)
    except Exception:  # noqa: BLE001 - never raises, one collector failing must not break /metrics
        log.debug("running runs collector failed", exc_info=True)
        return 0


def _tokens_by_workspace() -> Dict[str, int]:
    """Total (input+output) tokens per workspace, straight off the ``runs``
    table's own token columns — the same numbers the costs page sums, just
    grouped by the database rather than in Python over every row."""
    try:
        rows = db.get_conn().execute(
            "SELECT COALESCE(NULLIF(workspace, ''), 'default') AS ws, "
            "SUM(COALESCE(total_tokens, 0)) AS n FROM runs GROUP BY ws").fetchall()
        return {str(r["ws"]): int(r["n"] or 0) for r in rows}
    except Exception:  # noqa: BLE001 - never raises, one collector failing must not break /metrics
        log.debug("tokens_by_workspace collector failed", exc_info=True)
        return {}


def _cost_by_workspace() -> Tuple[Dict[str, float], bool]:
    """Estimated USD spend per workspace, from the same catalog pricing
    ``dashboard/backend/routes/costs.py`` uses (``common.pricing``).

    Grouped by (workspace, provider, model) in SQL first — a few dozen rows on
    any real deployment, never one row per run — and priced in Python from
    there, which is what keeps this cheap without giving up on cost entirely
    the way a plain token count would.

    Returns ``(costs, computable)``. ``computable`` is False when the price
    catalog itself could not be read, so the caller omits the metric rather
    than publish an all-zero line that looks like real, if quiet, spend.
    """
    try:
        from common.pricing import load_price_map
        prices = load_price_map()
    except Exception:  # noqa: BLE001 - never raises, one collector failing must not break /metrics
        log.debug("price catalog load failed", exc_info=True)
        return {}, False
    try:
        rows = db.get_conn().execute(
            "SELECT COALESCE(NULLIF(workspace, ''), 'default') AS ws, "
            "COALESCE(provider, '') AS provider, COALESCE(model, '') AS model, "
            "SUM(COALESCE(prompt_tokens, 0)) AS inbound, "
            "SUM(COALESCE(cached_prompt_tokens, 0)) AS cached, "
            "SUM(COALESCE(completion_tokens, 0)) AS outbound "
            "FROM runs GROUP BY ws, provider, model").fetchall()
    except Exception:  # noqa: BLE001 - never raises, one collector failing must not break /metrics
        log.debug("cost_by_workspace collector failed", exc_info=True)
        return {}, False

    costs: Dict[str, float] = {}
    for row in rows:
        ws = str(row["ws"])
        in_price, out_price, cached_price = prices.get(
            (str(row["provider"]), str(row["model"])), (0.0, 0.0, 0.0))
        inbound = int(row["inbound"] or 0)
        # Cached tokens are a subset of inbound, never in addition to it
        # (mirrors common.pricing.run_cost_usd's own clamp).
        cached = max(0, min(int(row["cached"] or 0), inbound))
        fresh = inbound - cached
        outbound = int(row["outbound"] or 0)
        cost = (fresh / 1_000_000 * in_price
                + cached / 1_000_000 * cached_price
                + outbound / 1_000_000 * out_price)
        costs[ws] = costs.get(ws, 0.0) + cost
    return costs, True


def render() -> str:
    """The whole ``/metrics`` body, Prometheus text exposition format 0.0.4."""
    return _format(collect())


def collect() -> List[Metric]:
    """Every metric as data, for :func:`render` and the OTLP metrics export."""
    lines: List[Metric] = []

    db_ok = _database_ok()
    _emit(lines, "agents_hub_database_up",
          "Whether the database answered a trivial query just now (1) or not (0).",
          "gauge", [({}, db_ok)])

    by_status = _runs_by_status()
    _emit(lines, "agents_hub_runs_total", "Run records by status.", "gauge",
          [({"status": status}, n) for status, n in sorted(by_status.items())])
    # Mirrors common.health's own "running_runs": a run mid-stop is still
    # occupying a slot, not yet free; one stopped and finished is not.
    running = _running_runs()
    _emit(lines, "agents_hub_runs_running", "Runs currently in a running state.",
          "gauge", [({}, running)])

    try:
        from common import run_queue
        qstats = run_queue.stats()
    except Exception:  # noqa: BLE001 - never raises, one collector failing must not break /metrics
        log.debug("run_queue collector failed", exc_info=True)
        qstats = {}
    _emit(lines, "agents_hub_run_queue",
          "Launch-queue rows by status (common/run_queue.py); empty everywhere "
          "except AGENTS_HUB_ROLE=api/worker deployments.",
          "gauge", [({"status": s}, qstats.get(s, 0))
                    for s in ("queued", "leased", "running", "done", "failed")])
    _emit(lines, "agents_hub_run_queue_oldest_seconds",
          "Age in seconds of the oldest still-queued launch.", "gauge",
          [({}, qstats.get("oldest_queued_seconds", 0.0))])

    try:
        from notify import outbound
        obstats = outbound.stats()
    except Exception:  # noqa: BLE001 - never raises, one collector failing must not break /metrics
        log.debug("outbox collector failed", exc_info=True)
        obstats = {"pending": 0, "dead": 0}
    _emit(lines, "agents_hub_outbox",
          "Outbox rows waiting to be delivered, and rows given up on after "
          "MAX_ATTEMPTS.", "gauge",
          [({"state": "pending"}, obstats.get("pending", 0)),
           ({"state": "dead"}, obstats.get("dead", 0))])

    try:
        from common import leases
        lease_rows = leases.all_leases()
    except Exception:  # noqa: BLE001 - never raises, one collector failing must not break /metrics
        log.debug("leases collector failed", exc_info=True)
        lease_rows = []
    age_samples = [({"role": r["role"]}, r["age_seconds"]) for r in lease_rows
                   if r.get("age_seconds") is not None]
    held_samples = [({"role": r["role"], "owner": r.get("owner") or ""},
                      0 if r.get("expired") else 1) for r in lease_rows]
    _emit(lines, "agents_hub_lease_age_seconds",
          "Seconds since a singleton role's lease was last renewed.",
          "gauge", age_samples)
    _emit(lines, "agents_hub_lease_held",
          "1 if the named owner currently holds this role's lease, 0 if it has "
          "lapsed (the row is kept for one look back at who had it).",
          "gauge", held_samples)

    tokens = _tokens_by_workspace()
    _emit(lines, "agents_hub_tokens_total",
          "Total tokens (input + output) recorded per workspace.", "gauge",
          [({"workspace": ws}, n) for ws, n in sorted(tokens.items())])

    costs, computable = _cost_by_workspace()
    if computable:
        _emit(lines, "agents_hub_cost_usd_total",
              "Estimated USD spend per workspace, from catalog pricing "
              "(common.pricing) — the same estimate the costs page shows.",
              "gauge", [({"workspace": ws}, round(c, 6))
                        for ws, c in sorted(costs.items())])
    # else: the price catalog could not be read. Omitted rather than
    # published as a false all-zero reading.

    try:
        from common.config import hub_role
        role = hub_role()
    except Exception:  # noqa: BLE001 - never raises, one collector failing must not break /metrics
        log.debug("hub_role collector failed", exc_info=True)
        role = "unknown"
    try:
        from common.leases import owner_id
        instance = owner_id()
    except Exception:  # noqa: BLE001 - never raises, one collector failing must not break /metrics
        log.debug("owner_id collector failed", exc_info=True)
        instance = "unknown"
    _emit(lines, "agents_hub_info",
          "Constant 1, labeled with this process's role and instance id.",
          "gauge", [({"role": role, "instance": instance}, 1)])

    try:
        from common.slo import evaluate as evaluate_slo
        slo = evaluate_slo()
        objectives = slo.get("objectives") or {}
    except Exception:  # noqa: BLE001 - never raises, one collector failing must not break /metrics
        log.debug("slo collector failed", exc_info=True)
        objectives = {}
    start_p95 = objectives.get("start_p95") or {}
    if start_p95.get("value_seconds") is not None:
        # Summary convention: one quantile line per sample this window holds
        # (just the one, p95 — a full histogram is common/slo.py's job to
        # grow if a second quantile earns its place).
        _emit(lines, "agents_hub_run_start_seconds",
              "Seconds between a run's record being created and it actually "
              "starting (common/slo.py), the p95 over the SLO window.",
              "summary", [({"quantile": "0.95"}, start_p95["value_seconds"])])
        _emit(lines, "agents_hub_run_start_seconds_count",
              "Samples behind the p95 above.", "gauge", [({}, start_p95.get("sample", 0))])
    breach_samples = []
    for objective_key, objective in objectives.items():
        status = objective.get("status")
        if status in ("ok", "breach"):
            breach_samples.append(({"objective": objective_key}, 1 if status == "breach" else 0))
    _emit(lines, "agents_hub_slo_breach",
          "1 when an SLO objective is in breach right now, 0 when it holds; "
          "the label omitted while there is not yet enough data to judge it.",
          "gauge", breach_samples)

    _collect_operator_metrics(lines)
    return lines


# ── the metrics operators ask for ────────────────────────────────────────────
#
# Runs finished by agent and status, model tokens and cost by provider and
# model, a run duration histogram, and tool calls by tool. Label cardinality
# is bounded: the busiest TOP_N agents (and models) keep their name, the rest
# add up under "other".

TOP_N = 20
OTHER = "other"
#: Run duration buckets, seconds.
DURATION_BOUNDS = [1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0, 3600.0]
_TERMINAL = ("completed", "stopped", "failed", "error")


def _top_n(rows: Dict[Tuple[str, ...], Dict[str, float]], keep_index: int, n: int = TOP_N,
           weight: str = "n") -> Dict[Tuple[str, ...], Dict[str, float]]:
    """*rows* (label tuple -> numbers) with every label at *keep_index* outside
    the *n* heaviest folded into :data:`OTHER`, the numbers summed."""
    weights: Dict[str, float] = {}
    for key, nums in rows.items():
        weights[key[keep_index]] = weights.get(key[keep_index], 0.0) + float(nums.get(weight, 0))
    keep = {k for k, _ in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))[:n]}
    out: Dict[Tuple[str, ...], Dict[str, float]] = {}
    for key, nums in rows.items():
        folded = key if key[keep_index] in keep else key[:keep_index] + (OTHER,) + key[keep_index + 1:]
        into = out.setdefault(folded, {})
        for field, value in nums.items():
            into[field] = into.get(field, 0) + value
    return out


def _finished_by_agent() -> Dict[Tuple[str, ...], Dict[str, float]]:
    try:
        marks = ",".join("?" for _ in _TERMINAL)
        rows = db.get_conn().execute(
            f"SELECT COALESCE(NULLIF(agent_id, ''), 'unknown') AS agent, status, COUNT(*) AS n "
            f"FROM runs WHERE status IN ({marks}) GROUP BY agent, status", _TERMINAL).fetchall()
        return _top_n({(str(r["agent"]), str(r["status"])): {"n": int(r["n"] or 0)} for r in rows}, 0)
    except Exception:  # noqa: BLE001 - never raises, one collector failing must not break /metrics
        log.debug("finished_by_agent collector failed", exc_info=True)
        return {}


def _model_usage() -> Tuple[Dict[Tuple[str, ...], Dict[str, float]], bool]:
    """Tokens (input, cached, output) and estimated USD per provider and model."""
    try:
        from common.pricing import load_price_map
        prices = load_price_map()
    except Exception:  # noqa: BLE001 - never raises, one collector failing must not break /metrics
        log.debug("price catalog load failed", exc_info=True)
        prices = None
    try:
        rows = db.get_conn().execute(
            "SELECT COALESCE(provider, '') AS provider, COALESCE(model, '') AS model, "
            "SUM(COALESCE(prompt_tokens, 0)) AS inbound, "
            "SUM(COALESCE(cached_prompt_tokens, 0)) AS cached, "
            "SUM(COALESCE(completion_tokens, 0)) AS outbound "
            "FROM runs GROUP BY provider, model").fetchall()
    except Exception:  # noqa: BLE001 - never raises, one collector failing must not break /metrics
        log.debug("model_usage collector failed", exc_info=True)
        return {}, False
    out: Dict[Tuple[str, ...], Dict[str, float]] = {}
    for r in rows:
        key = (str(r["provider"] or "unknown"), str(r["model"] or "unknown"))
        inbound = int(r["inbound"] or 0)
        cached = max(0, min(int(r["cached"] or 0), inbound))
        outbound = int(r["outbound"] or 0)
        cost = 0.0
        if prices is not None:
            in_p, out_p, cached_p = prices.get((str(r["provider"]), str(r["model"])), (0.0, 0.0, 0.0))
            cost = ((inbound - cached) / 1e6 * in_p + cached / 1e6 * cached_p + outbound / 1e6 * out_p)
        nums = out.setdefault(key, {})
        for field, value in (("input", inbound - cached), ("cached", cached), ("output", outbound),
                             ("cost", cost), ("n", inbound + outbound)):
            nums[field] = nums.get(field, 0) + value
    return _top_n(out, 1), prices is not None


def _duration_histogram() -> Optional[Tuple[List[int], float, int]]:
    """Per-bucket counts, the sum (seconds) and the count of finished runs
    that recorded a duration: one aggregate query, no per-run rows."""
    try:
        marks = ",".join("?" for _ in _TERMINAL)
        cols = []
        for i, bound in enumerate(DURATION_BOUNDS):
            low = f"duration_ms > {int(DURATION_BOUNDS[i - 1] * 1000)} AND " if i else ""
            cols.append(f"SUM(CASE WHEN {low}duration_ms <= {int(bound * 1000)} THEN 1 ELSE 0 END) AS b{i}")
        cols.append(f"SUM(CASE WHEN duration_ms > {int(DURATION_BOUNDS[-1] * 1000)} THEN 1 ELSE 0 END) AS b{len(DURATION_BOUNDS)}")
        row = db.get_conn().execute(
            f"SELECT {', '.join(cols)}, SUM(duration_ms) AS total, COUNT(*) AS n FROM runs "
            f"WHERE status IN ({marks}) AND duration_ms IS NOT NULL AND duration_ms >= 0",
            _TERMINAL).fetchone()
        counts = [int(row[f"b{i}"] or 0) for i in range(len(DURATION_BOUNDS) + 1)]
        return counts, float(row["total"] or 0) / 1000.0, int(row["n"] or 0)
    except Exception:  # noqa: BLE001 - never raises, one collector failing must not break /metrics
        log.debug("duration histogram collector failed", exc_info=True)
        return None


# Tool calls live as JSON in each run's payload, so counting them from the
# table on every scrape would read every payload. Instead a watermark walks
# forward through newly finished runs, a bounded batch per scrape, and the
# counts accumulate in this process: a counter that restarts from zero with
# the process, which is what Prometheus counters are allowed to do.
_TOOL_BATCH = 200
_tool_lock = threading.Lock()
_tool_counts: Dict[Tuple[str, str], int] = {}
_tool_watermark: Optional[str] = None


def _advance_tool_counts() -> None:
    global _tool_watermark
    from datetime import datetime, timedelta, timezone
    with _tool_lock:
        if _tool_watermark is None:
            _tool_watermark = datetime.now(timezone.utc).isoformat()
            return  # counting starts now; earlier runs belong to earlier processes
        mark = _tool_watermark
        try:
            floor = (datetime.fromisoformat(mark) - timedelta(hours=6)).isoformat()
            marks = ",".join("?" for _ in _TERMINAL)
            rows = db.get_conn().execute(
                f"SELECT run_id, finished_at FROM runs WHERE started_at >= ? AND finished_at > ? "
                f"AND status IN ({marks}) ORDER BY finished_at LIMIT {_TOOL_BATCH}",
                (floor, mark, *_TERMINAL)).fetchall()
        except Exception:  # noqa: BLE001 - never raises, one collector failing must not break /metrics
            log.debug("tool call watermark query failed", exc_info=True)
            return
        if not rows:
            return
        from managers.runs.store import get_run_process
        for r in rows:
            try:
                calls = get_run_process(r["run_id"]).get("tool_calls") or []
            except Exception:  # noqa: BLE001 - one unreadable payload is skipped, the walk goes on
                log.debug("tool calls of %s unreadable", r["run_id"], exc_info=True)
                continue
            for call in calls:
                if not isinstance(call, dict):
                    continue
                key = (str(call.get("tool") or "unknown"), "error" if call.get("status") == "error" else "ok")
                _tool_counts[key] = _tool_counts.get(key, 0) + 1
        _tool_watermark = str(rows[-1]["finished_at"])


def reset_tool_counts() -> None:
    """Forget the tool counters and the watermark (tests)."""
    global _tool_watermark
    with _tool_lock:
        _tool_counts.clear()
        _tool_watermark = None


def _collect_operator_metrics(metrics: List[Metric]) -> None:
    finished = _finished_by_agent()
    _emit(metrics, "agents_hub_runs_finished_total",
          f"Finished runs by agent and final status; the {TOP_N} busiest agents by name, the rest as 'other'.",
          "counter", [({"agent": a, "status": st}, int(v["n"])) for (a, st), v in sorted(finished.items())])

    usage, priced = _model_usage()
    token_samples: List[_Sample] = []
    for (provider, model), v in sorted(usage.items()):
        for direction in ("input", "cached", "output"):
            token_samples.append(({"provider": provider, "model": model, "direction": direction}, int(v[direction])))
    _emit(metrics, "agents_hub_model_tokens_total",
          f"Model tokens by provider, model and direction (input excludes cached); the {TOP_N} busiest models by name.",
          "counter", token_samples)
    if priced:
        _emit(metrics, "agents_hub_model_cost_usd_total",
              "Estimated USD spend by provider and model, from catalog pricing (common.pricing).",
              "counter", [({"provider": p, "model": m}, round(v["cost"], 6)) for (p, m), v in sorted(usage.items())])

    hist = _duration_histogram()
    if hist is not None:
        counts, total, n = hist
        _emit_histogram(metrics, "agents_hub_run_duration_seconds",
                        "How long finished runs took, in seconds.", DURATION_BOUNDS, counts, total, n)

    _advance_tool_counts()
    with _tool_lock:
        tool_samples = [({"tool": t, "status": st}, n) for (t, st), n in sorted(_tool_counts.items())]
    _emit(metrics, "agents_hub_tool_calls_total",
          "Tool calls by tool and outcome, of runs finished since this process started.",
          "counter", _bounded_tool_samples(tool_samples))


def _bounded_tool_samples(samples: List[_Sample]) -> List[_Sample]:
    folded = _top_n({(s[0]["tool"], s[0]["status"]): {"n": s[1]} for s in samples}, 0)
    return [({"tool": t, "status": st}, int(v["n"])) for (t, st), v in sorted(folded.items())]


__all__ = ["Metric", "collect", "render", "reset_tool_counts"]
