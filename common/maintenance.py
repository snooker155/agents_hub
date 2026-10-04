"""
Periodic state maintenance: run retention and orphan-file pruning.

`.agents_hub` grows without bound — run records, per-run structured payloads,
run logs and process sidecars accumulate forever. This module trims terminal
run records older than ``run_retention_days``, trims each external connection
back to its own run cap (``connections.retention``, a count rather than an age,
because a reporting graph outgrows an age limit), deletes the on-disk log/
sidecar files left behind by any deleted or long-gone run, and prunes the
scheduler's firing journal (``plans.storage.FireStore``) past
``AGENTS_HUB_PLAN_FIRES_RETENTION_DAYS`` (default 90 days, 0 disables it).

It is invoked once a day by the plan scheduler (see ``plans.scheduler``), guarded
by a ``last_maintenance`` marker in the DB ``meta`` table so it runs at most once
per 24h regardless of how often the scheduler ticks or how often the process
restarts. Everything runs off the event loop in a worker thread.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict

from common import db
from common.paths import AGENTS_HUB_ROOT

log = logging.getLogger("common.maintenance")

_MAINTENANCE_INTERVAL_HOURS = 24
# Only these run states are ever pruned — an in-flight or paused run is kept
# regardless of age so retention can never delete active work.
_TERMINAL_STATUSES = ("completed", "failed", "stopped", "error")
# Not on common.config.Settings (this module is the only reader): a plain
# env var kept it out of that shared file. 0 disables the prune.
_DEFAULT_PLAN_FIRES_RETENTION_DAYS = 90
#: After this many days a heartbeat's quiet ticks are folded into daily counts.
_DEFAULT_HEARTBEAT_COMPACT_DAYS = 7


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
    except ValueError:
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
                    # Local file and any mirrored copy (common/blobs.py): a
                    # pruned run's log must not linger in the object store
                    # either.
                    from common import blobs
                    blobs.delete(blobs.rel(lf))
                except Exception:  # noqa: BLE001 - best-effort mirror cleanup, must not block pruning
                    log.debug("blob cleanup failed for %s", lf, exc_info=True)
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
                except OSError:
                    log.debug("orphan log delete failed for %s", f, exc_info=True)
                try:
                    from common import blobs
                    blobs.delete(blobs.rel(f))
                except Exception:  # noqa: BLE001 - best-effort mirror cleanup, must not block pruning
                    log.debug("blob cleanup failed for %s", f, exc_info=True)

    # Registry snapshots written for run containers (common/snapshot.py):
    # gone once their run has finished. Node snapshots (node-<id>) stay while
    # the node record exists.
    try:
        from common import snapshot
        live = {row["run_id"] for row in conn.execute(
            "SELECT run_id FROM runs WHERE status IN ('running', 'pending', 'stop', 'awaiting_approval')"
        ).fetchall()}
        live |= {f"node-{row['node_id']}" for row in conn.execute("SELECT node_id FROM nodes").fetchall()}
        removed += snapshot.prune_snapshots(live)
    except Exception:  # noqa: BLE001 - isolated cleanup pass, must not block orphan file pruning
        log.debug("snapshot pruning failed", exc_info=True)

    # Legacy sidecar dir (renamed to .migrated post-migration, but a partially
    # upgraded environment may still have the live dir): drop stale entries.
    for sidecar_dir in (AGENTS_HUB_ROOT / "run_process", AGENTS_HUB_ROOT / "run_process.migrated"):
        if sidecar_dir.is_dir():
            for f in sidecar_dir.glob("*.json"):
                if f.stem not in known:
                    try:
                        f.unlink()
                        removed += 1
                    except OSError:
                        log.debug("stale sidecar delete failed for %s", f, exc_info=True)
    return removed


def prune_old_fires(retention_days: int) -> int:
    """Delete scheduler firing-journal rows (``plans.storage.FireStore``,
    ``plan_fires``) recorded before the cutoff. Returns the number removed.
    0 disables pruning.

    The journal gets one row per tick that finds a job due, forever, with
    nothing else bounding it (unlike run retention, which only touches
    terminal runs); this is its only cleanup.
    """
    if retention_days <= 0:
        return 0
    from plans.service import fire_store

    cutoff = _now() - timedelta(days=retention_days)
    return fire_store.prune_older_than(cutoff)


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
    # Members that stopped beating a day ago are history, not the map.
    try:
        from common import members
        summary["pruned_members"] = members.prune()
    except Exception:
        log.exception("member pruning failed")
        summary["pruned_members"] = 0
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
    # The audit trail (common/audit.py): rows past AUDIT_RETENTION_DAYS. Isolated
    # like the passes above, and a no-op (0 rows, 0 disables it) outside
    # single-mode where the trail is off, so this stays safe to call always.
    try:
        from common import audit
        summary["pruned_audit_rows"] = audit.prune()
    except Exception:
        log.exception("audit prune failed")
        summary["pruned_audit_rows"] = 0
    # Scheduler firing journal (plans.storage.FireStore): unlike run retention
    # above, every row is terminal the moment it is written, so there is no
    # "still active" check here, only an age cutoff. Isolated like the passes
    # above.
    try:
        retention_days = int(
            os.environ.get(
                "AGENTS_HUB_PLAN_FIRES_RETENTION_DAYS", str(_DEFAULT_PLAN_FIRES_RETENTION_DAYS)
            )
            or _DEFAULT_PLAN_FIRES_RETENTION_DAYS
        )
        summary["pruned_fires"] = prune_old_fires(retention_days)
    except Exception:
        log.exception("fire journal pruning failed")
        summary["pruned_fires"] = 0
    # Run tokens (common/run_tokens.py) a week past their expiry: they already
    # authenticate nothing, the rows only grow the table.
    try:
        from common import run_tokens
        summary["pruned_run_tokens"] = run_tokens.prune(7)
    except Exception:
        log.exception("run token pruning failed")
        summary["pruned_run_tokens"] = 0
    # A proactive agent's quiet ticks (proactive/service.py): rows older than
    # AGENTS_HUB_HEARTBEAT_COMPACT_DAYS fold into one counted row per day and
    # outcome, so a five-minute pulse does not keep 288 identical rows a day
    # until the retention above removes them. 0 disables it.
    try:
        compact_days = int(
            os.environ.get("AGENTS_HUB_HEARTBEAT_COMPACT_DAYS", str(_DEFAULT_HEARTBEAT_COMPACT_DAYS))
            or _DEFAULT_HEARTBEAT_COMPACT_DAYS
        )
        from proactive.service import compact_journal
        summary["compacted_ticks"] = compact_journal(compact_days)
    except Exception:
        log.exception("heartbeat journal compaction failed")
        summary["compacted_ticks"] = 0
    # Guardrail findings (guardrails/runtime.py, AGENTS_HUB_GUARDRAIL_EVENTS_
    # RETENTION_DAYS) and tool policy decisions (tools/permission_policy.py,
    # AGENTS_HUB_TOOL_POLICY_RETENTION_DAYS): both are append-only logs
    # bounded by age. Isolated like the passes above.
    try:
        from guardrails.runtime import prune_events
        summary["pruned_guardrail_events"] = int(prune_events() or 0)
    except Exception:
        log.exception("guardrail event pruning failed")
        summary["pruned_guardrail_events"] = 0
    try:
        from tools.permission_policy import prune
        summary["pruned_tool_decisions"] = int(prune(force=True) or 0)
    except Exception:
        log.exception("tool policy decision pruning failed")
        summary["pruned_tool_decisions"] = 0
    if pruned_runs or pruned_files or summary.get("pruned_view_dirs") \
            or summary.get("pruned_connection_runs"):
        log.info("maintenance: pruned %d run(s), %d connection run(s), %d orphan file(s), "
                 "%d view dir(s)",
                 pruned_runs, summary.get("pruned_connection_runs", 0), pruned_files,
                 summary.get("pruned_view_dirs", 0))
    return summary


__all__ = ["run_maintenance", "prune_old_runs", "prune_orphan_files", "prune_old_fires"]
