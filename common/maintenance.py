"""
Periodic state maintenance: run retention and orphan-file pruning.

`.agents_hub` grows without bound — run records, per-run structured payloads,
run logs and process sidecars accumulate forever. This module trims terminal
run records older than ``run_retention_days``, trims each external connection
back to its own run cap (``connections.retention``, a count rather than an age,
because a reporting graph outgrows an age limit), and deletes the on-disk log/
sidecar files left behind by any deleted or long-gone run.

It is invoked once a day by the plan scheduler (see ``plans.scheduler``), guarded
by a ``last_maintenance`` marker in the DB ``meta`` table so it runs at most once
per 24h regardless of how often the scheduler ticks or how often the process
restarts. Everything runs off the event loop in a worker thread.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict

from common import db
from common.paths import AGENTS_HUB_ROOT

log = logging.getLogger("common.maintenance")

_MAINTENANCE_INTERVAL_HOURS = 24
# Only these run states are ever pruned — an in-flight or paused run is kept
# regardless of age so retention can never delete active work.
_TERMINAL_STATUSES = ("completed", "failed", "stopped", "error")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _due(marker: str | None) -> bool:
    if not marker:
        return True
    try:
        last = datetime.fromisoformat(marker)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (_now() - last) >= timedelta(hours=_MAINTENANCE_INTERVAL_HOURS)
    except Exception:
        return True


def prune_old_runs(retention_days: int) -> int:
    """Delete terminal run records (and their payload rows) finished before the
    cutoff. Returns the number of run records removed. 0 disables pruning."""
    if retention_days <= 0:
        return 0
    cutoff = (_now() - timedelta(days=retention_days)).isoformat()
    placeholders = ",".join("?" * len(_TERMINAL_STATUSES))
    with db.transaction() as conn:
        rows = conn.execute(
            f"SELECT run_id, log_file FROM runs "
            f"WHERE status IN ({placeholders}) "
            f"AND finished_at IS NOT NULL AND finished_at < ?",
            (*_TERMINAL_STATUSES, cutoff),
        ).fetchall()
        removed = 0
        for r in rows:
            conn.execute("DELETE FROM run_payloads WHERE run_id = ?", (r["run_id"],))
            conn.execute("DELETE FROM runs WHERE run_id = ?", (r["run_id"],))
            lf = r["log_file"]
            if lf:
                try:
                    from pathlib import Path
                    Path(lf).unlink(missing_ok=True)
                except Exception:
                    pass
            removed += 1
    return removed


def prune_orphan_files() -> int:
    """Delete run-log and process-sidecar files whose run record no longer
    exists. Covers logs left by deleted runs and pre-SQLite ``run_process``
    sidecars superseded by the payload table. Returns files removed."""
    conn = db.get_conn()
    known = {row["run_id"] for row in conn.execute("SELECT run_id FROM runs").fetchall()}
    removed = 0

    logs_dir = AGENTS_HUB_ROOT / "run_logs"
    if logs_dir.is_dir():
        for f in logs_dir.glob("agent_run_*.log"):
            run_id = f.stem[len("agent_run_"):]
            if run_id not in known:
                try:
                    f.unlink()
                    removed += 1
                except Exception:
                    pass

    # Legacy sidecar dir (renamed to .migrated post-migration, but a partially
    # upgraded environment may still have the live dir): drop stale entries.
    for sidecar_dir in (AGENTS_HUB_ROOT / "run_process", AGENTS_HUB_ROOT / "run_process.migrated"):
        if sidecar_dir.is_dir():
            for f in sidecar_dir.glob("*.json"):
                if f.stem not in known:
                    try:
                        f.unlink()
                        removed += 1
                    except Exception:
                        pass
    return removed


def run_maintenance(*, force: bool = False) -> Dict[str, int]:
    """Run all maintenance passes if due (or ``force``); record the timestamp.

    Returns a summary dict. Safe to call from any process: the ``last_maintenance``
    marker is read and written under one transaction so concurrent schedulers
    do not double-run.
    """
    from common.config import settings

    with db.transaction() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key='last_maintenance'").fetchone()
        marker = row["value"] if row is not None else None
        if not force and not _due(marker):
            return {"skipped": 1}
        # Claim the slot immediately so a co-running scheduler in another process
        # sees "not due" and bows out.
        conn.execute(db.upsert_sql("meta", ("key", "value"), ("key",)),
                     ("last_maintenance", _now().isoformat()))

    pruned_runs = prune_old_runs(settings.run_retention_days)
    pruned_files = prune_orphan_files()
    summary = {"pruned_runs": pruned_runs, "pruned_files": pruned_files}
    # Connections are capped by run *count*, not by age: a graph reporting a few
    # hundred runs an hour outgrows a day-based limit long before the limit
    # notices. Isolated like the views pass below, so a connection store that
    # cannot be read never blocks run and file pruning.
    try:
        from connections.retention import prune_all
        summary.update(prune_all())
    except Exception:
        log.exception("connection retention failed")
    # OTLP traces whose root never arrived, on a connection that has since gone
    # quiet. A connection still exporting clears its own on its next request;
    # this is the backstop for the one that stopped exporting altogether.
    try:
        from connections.otel import flush_idle
        summary.update(flush_idle())
    except Exception:
        log.exception("otel trace flush failed")
    # Views retention: bound long op logs and drop orphan view dirs. Isolated so
    # a views failure never blocks run/file pruning.
    try:
        from views.store import run_view_maintenance
        summary.update(run_view_maintenance())
    except Exception:
        log.exception("view maintenance failed")
    if pruned_runs or pruned_files or summary.get("pruned_view_dirs") \
            or summary.get("pruned_connection_runs"):
        log.info("maintenance: pruned %d run(s), %d connection run(s), %d orphan file(s), "
                 "%d view dir(s)",
                 pruned_runs, summary.get("pruned_connection_runs", 0), pruned_files,
                 summary.get("pruned_view_dirs", 0))
    return summary


__all__ = ["run_maintenance", "prune_old_runs", "prune_orphan_files"]
