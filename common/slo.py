"""
Two service level objectives, judged over a rolling window: how long a run
waits between being recorded and actually starting, and what share of the
runs that finish come back failed.

Both read straight off the ``runs`` (leaf, agent) and ``entity_runs`` (flow,
loop, team, scenario) tables with a couple of aggregate-shaped queries each,
the same discipline ``common/metrics.py`` holds itself to: a window that gets
scraped or ticked often must never be the query that loads a whole run table.

Thresholds are hub-wide toggles (``common.config.live_setting``), so an
operator can tighten or loosen them without a restart, the same as every
other ``AGENTS_HUB_...`` setting. :func:`evaluate` is the one entry point;
``GET /api/support/slo``, the Health page's SLO card, ``ah support-bundle``
and the periodic alert check in ``notify/rules.py`` all call it.

Never raises: a query that fails reports that objective as ``no_data`` rather
than taking down the health page or the alert tick with it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from common import db

log = logging.getLogger(__name__)

DEFAULT_WINDOW_SECONDS = 3600.0
DEFAULT_START_P95_SECONDS = 30.0
DEFAULT_ERROR_RATE = 0.05
#: Below this many finished runs in the window, an error rate is noise, not
#: a signal: one bad run out of three is not a 33% error rate worth paging
#: anyone over.
MIN_ERROR_SAMPLE = 20

# Leaf ``runs.status`` failure spellings plus the shared ``entity_runs``
# vocabulary's own (common/run_status.py normalises both to "failed").
_FAILED_STATUSES = ("failed", "error")
_FINISHED_STATUSES = ("completed", "stopped", "failed", "error", "done", "finished")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _window_start_iso(window_seconds: float) -> str:
    return (_now() - timedelta(seconds=window_seconds)).isoformat()


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def start_p95_seconds(window_seconds: float) -> Tuple[Optional[float], int]:
    """The 95th percentile of ``started_at - created_at`` across every run
    (leaf and entity) that started within the window. ``(None, 0)`` when
    nothing started in the window at all."""
    since = _window_start_iso(window_seconds)
    conn = db.get_conn()
    samples: List[float] = []
    for table in ("runs", "entity_runs"):
        try:
            rows = conn.execute(
                f"SELECT created_at, started_at FROM {table} "
                f"WHERE started_at IS NOT NULL AND started_at >= ?", (since,),
            ).fetchall()
        except Exception:  # noqa: BLE001 - one table missing must not blank the other
            log.debug("slo: start_p95 query failed on %s", table, exc_info=True)
            continue
        for row in rows:
            rec = dict(row)
            created = _parse_iso(rec.get("created_at"))
            started = _parse_iso(rec.get("started_at"))
            if created is None or started is None:
                continue
            delta = (started - created).total_seconds()
            if delta >= 0:
                samples.append(delta)
    if not samples:
        return None, 0
    samples.sort()
    index = min(len(samples) - 1, int(round(0.95 * (len(samples) - 1))))
    return samples[index], len(samples)


def error_rate(window_seconds: float) -> Tuple[Optional[float], int]:
    """``failed / finished`` across every run (leaf and entity) that finished
    within the window. ``(None, sample)`` below :data:`MIN_ERROR_SAMPLE`."""
    since = _window_start_iso(window_seconds)
    conn = db.get_conn()
    total = 0
    failed = 0
    for table in ("runs", "entity_runs"):
        try:
            rows = conn.execute(
                f"SELECT status, COALESCE(finished_at, started_at, created_at) AS finished "
                f"FROM {table} WHERE COALESCE(finished_at, started_at, created_at) >= ?",
                (since,),
            ).fetchall()
        except Exception:  # noqa: BLE001 - see start_p95_seconds
            log.debug("slo: error_rate query failed on %s", table, exc_info=True)
            continue
        for row in rows:
            rec = dict(row)
            from common.run_status import normalize
            status = normalize(rec.get("status"))
            if status not in ("completed", "stopped", "failed"):
                continue  # not a finished run (still active or parked)
            total += 1
            if status == "failed":
                failed += 1
    if total < MIN_ERROR_SAMPLE:
        return None, total
    return failed / total, total


def _thresholds() -> Tuple[float, float]:
    from common.config import live_setting
    try:
        p95 = float(live_setting("AGENTS_HUB_SLO_START_P95_SECONDS", str(DEFAULT_START_P95_SECONDS)))
    except (TypeError, ValueError):
        p95 = DEFAULT_START_P95_SECONDS
    try:
        rate = float(live_setting("AGENTS_HUB_SLO_ERROR_RATE", str(DEFAULT_ERROR_RATE)))
    except (TypeError, ValueError):
        rate = DEFAULT_ERROR_RATE
    return p95, rate


def evaluate(window_seconds: Optional[float] = None) -> Dict[str, Any]:
    """Both objectives over the window (default 1h). Never raises.

    Returns ``{status, checked_at, window_seconds, objectives: {start_p95,
    error_rate}}``. Each objective is ``{status: ok|breach|no_data, ...}``;
    the top-level status is the worst of the two (``no_data`` beats ``ok``
    but never beats ``breach``).
    """
    window = float(window_seconds or DEFAULT_WINDOW_SECONDS)
    p95_threshold, error_threshold = _thresholds()

    try:
        p95, p95_sample = start_p95_seconds(window)
    except Exception:  # noqa: BLE001 - a query failure is no_data, not a crash
        log.debug("slo: start_p95_seconds failed", exc_info=True)
        p95, p95_sample = None, 0
    try:
        rate, rate_sample = error_rate(window)
    except Exception:  # noqa: BLE001 - see above
        log.debug("slo: error_rate failed", exc_info=True)
        rate, rate_sample = None, 0

    objectives: Dict[str, Any] = {}
    if p95 is None:
        objectives["start_p95"] = {
            "status": "no_data", "value_seconds": None, "sample": p95_sample,
            "threshold_seconds": p95_threshold, "window_seconds": window,
        }
    else:
        objectives["start_p95"] = {
            "status": "ok" if p95 <= p95_threshold else "breach",
            "value_seconds": round(p95, 3), "sample": p95_sample,
            "threshold_seconds": p95_threshold, "window_seconds": window,
        }
    if rate is None:
        objectives["error_rate"] = {
            "status": "no_data", "value": rate, "sample": rate_sample,
            "threshold": error_threshold, "min_sample": MIN_ERROR_SAMPLE, "window_seconds": window,
        }
    else:
        objectives["error_rate"] = {
            "status": "ok" if rate <= error_threshold else "breach",
            "value": round(rate, 4), "sample": rate_sample,
            "threshold": error_threshold, "min_sample": MIN_ERROR_SAMPLE, "window_seconds": window,
        }

    rank = {"ok": 0, "no_data": 1, "breach": 2}
    overall = "ok"
    for obj in objectives.values():
        if rank[obj["status"]] > rank[overall]:
            overall = obj["status"]

    return {"status": overall, "checked_at": _now().isoformat(),
            "window_seconds": window, "objectives": objectives}


__all__ = ["evaluate", "start_p95_seconds", "error_rate",
           "DEFAULT_WINDOW_SECONDS", "DEFAULT_START_P95_SECONDS", "DEFAULT_ERROR_RATE",
           "MIN_ERROR_SAMPLE"]
