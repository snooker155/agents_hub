"""Keeping a connection's history bounded.

Observe mode is sold to production graphs, and a production graph reports runs
at a rate nothing else in this product does: a few hundred an hour is ordinary,
and every one of them writes a run row, a payload row and often a session. The
hub's existing retention is a *day* limit (``run_retention_days``), which is the
wrong instrument here — thirty days of that traffic is a quarter of a million
rows before anything is pruned.

So a connection is capped by *count*: keep the newest N runs, drop the rest.
The cap is per connection because their volumes differ by orders of magnitude,
and a hub-wide number would be either useless for the busy one or destructive
for the quiet one.

Three things this deliberately will not do:

* **It never deletes a run that is not finished.** A run still reporting is
  work in flight, whatever its age, and losing it would make the very case
  retention exists for — a busy graph — the case where records vanish mid-run.
* **It never touches another agent's runs.** The prune is scoped to the
  connection's own id, which is also the ``agent_id`` its runs carry.
* **It does not run inside an ingest request.** Pruning is a burst of deletes;
  doing it while a graph waits would put this product's maintenance on a
  customer's critical path. It runs in the daily maintenance pass, and on
  demand from the connection's page.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import db

log = logging.getLogger("connections.retention")

# Same set the hub-wide retention uses: an in-flight or paused run is never
# pruned, however old or however far past the cap it sits.
TERMINAL_STATUSES = ("completed", "failed", "stopped", "error")

# Upper bound on one prune pass, so a connection that has run away does not
# hold a transaction open for a hundred thousand deletes. What is left over is
# taken by the next pass.
MAX_DELETES_PER_PASS = 5_000


def effective_limit(connection: Dict[str, Any]) -> int:
    """How many runs this connection keeps. 0 means no cap."""
    own = connection.get("retention_runs")
    if own is not None:
        try:
            return max(0, int(own))
        except (TypeError, ValueError):
            pass
    from common.config import settings
    return max(0, int(getattr(settings, "connection_retention_runs", 0) or 0))


def _delete_runs(conn, run_ids: List[str]) -> None:
    for run_id in run_ids:
        row = conn.execute("SELECT log_file FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        conn.execute("DELETE FROM run_payloads WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
        log_file = row["log_file"] if row is not None else None
        if log_file:
            try:
                Path(log_file).unlink(missing_ok=True)
            except Exception:
                # A log that cannot be removed is a stray file, not a reason to
                # leave the database row behind.
                pass


def _drop_empty_ingest_sessions(conn, connection_id: str) -> int:
    """Remove this connection's sessions that no longer hold a run.

    Scoped by the ``conn:<id>:`` conversation id that ``connections.service``
    writes, so an ordinary chat that happens to have no runs yet — a
    conversation someone has just opened — is never swept up by a connection's
    retention.
    """
    rows = conn.execute(
        "SELECT session_id FROM sessions WHERE conversation_id LIKE ? "
        "AND session_id NOT IN (SELECT session_id FROM runs "
        "WHERE session_id IS NOT NULL AND session_id != '')",
        (f"conn:{connection_id}:%",),
    ).fetchall()
    for row in rows:
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (row["session_id"],))
    return len(rows)


def prune_connection(connection: Dict[str, Any]) -> Dict[str, int]:
    """Trim one connection back to its cap. Returns what was removed."""
    connection_id = str(connection.get("id") or "")
    limit = effective_limit(connection)
    if not connection_id or limit <= 0:
        return {"connection_id": connection_id, "removed_runs": 0, "removed_sessions": 0}

    with db.transaction() as conn:
        # Newest first, and the offset is what implements "keep N": the rows
        # after it are the ones past the cap. Ordering falls back to created_at
        # because a run that never started has no started_at.
        placeholders = ",".join("?" * len(TERMINAL_STATUSES))
        rows = conn.execute(
            "SELECT run_id FROM runs WHERE agent_id = ? "
            "ORDER BY COALESCE(started_at, created_at) DESC "
            "LIMIT ? OFFSET ?",
            (connection_id, MAX_DELETES_PER_PASS, limit),
        ).fetchall()
        candidates = [str(r["run_id"]) for r in rows]
        if not candidates:
            return {"connection_id": connection_id, "removed_runs": 0, "removed_sessions": 0}

        # Of those, only the finished ones may go. Filtering here rather than in
        # the query above keeps the cap counting *all* runs: a connection with
        # its cap's worth of live runs should not have its history deleted to
        # make room for them.
        marks = ",".join("?" * len(candidates))
        finished = [
            str(r["run_id"]) for r in conn.execute(
                f"SELECT run_id FROM runs WHERE run_id IN ({marks}) "
                f"AND status IN ({placeholders})",
                (*candidates, *TERMINAL_STATUSES),
            ).fetchall()
        ]
        _delete_runs(conn, finished)
        removed_sessions = _drop_empty_ingest_sessions(conn, connection_id)

    if finished:
        log.info("connection %s: pruned %d run(s), %d session(s)",
                 connection_id, len(finished), removed_sessions)
    return {
        "connection_id": connection_id,
        "removed_runs": len(finished),
        "removed_sessions": removed_sessions,
    }


def prune_all(connections: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    """Trim every connection. Called by the daily maintenance pass."""
    from connections import store

    records = connections if connections is not None else store.list_connections()
    removed_runs = removed_sessions = 0
    for record in records:
        try:
            result = prune_connection(record)
        except Exception:
            # One connection's prune failing must not stop the others, and must
            # not fail the maintenance pass it is part of.
            log.exception("prune failed for connection %s", record.get("id"))
            continue
        removed_runs += result["removed_runs"]
        removed_sessions += result["removed_sessions"]
    return {"pruned_connection_runs": removed_runs, "pruned_connection_sessions": removed_sessions}


__all__ = ["effective_limit", "prune_all", "prune_connection",
           "MAX_DELETES_PER_PASS", "TERMINAL_STATUSES"]
