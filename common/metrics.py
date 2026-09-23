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
from typing import Any, Dict, List, Tuple

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


def _emit(lines: List[str], name: str, help_text: str, mtype: str,
          samples: List[_Sample]) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} {mtype}")
    for labels, value in samples:
        lines.append(_line(name, labels, value))


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
    lines: List[str] = []

    db_ok = _database_ok()
    _emit(lines, "agents_hub_database_up",
          "Whether the database answered a trivial query just now (1) or not (0).",
          "gauge", [({}, db_ok)])

    by_status = _runs_by_status()
    _emit(lines, "agents_hub_runs_total", "Run records by status.", "gauge",
          [({"status": status}, n) for status, n in sorted(by_status.items())])
    # Mirrors common.health's own "running_runs": a run mid-stop is still
    # occupying a slot, not yet free.
    running = by_status.get("running", 0) + by_status.get("stop", 0)
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

    return "\n".join(lines) + "\n"


__all__ = ["render"]
