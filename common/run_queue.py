"""
The queue between "a run was requested" and "a process was started for it".

With the default role (``AGENTS_HUB_ROLE=all``) nothing here is used: the
backend that receives a request spawns the subprocess or container itself,
exactly as it always has. With ``AGENTS_HUB_ROLE=api`` the backend still
does every database-side preparation (the run record, the session, the
instance, the log path) and then puts a launch request here instead of
spawning; a process in the ``worker`` role (``ah worker``,
``runtime/worker.py``) claims it, spawns the child on its own host and keeps
the row leased while the child is alive. See docs/workers.md.

Rows carry a JSON ``payload`` that is everything the launcher needs to spawn
the process, and nothing secret: the worker builds the child's environment
itself from its own configuration, the same way the backend did.

Claiming is one read-modify-write under ``db.transaction()``, so two workers
never take the same row: on SQLite the transaction is exclusive, on Postgres
it holds the advisory lock every writer takes. A leased row whose lease ran
out is claimable again (the worker died before it could launch, or after it
launched and stopped renewing; :func:`sweep` sorts the two apart with the
run's own heartbeat before handing the row back).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from common import db
from common.leases import owner_id

log = logging.getLogger("common.run_queue")

STATUS_QUEUED = "queued"
STATUS_LEASED = "leased"      # claimed, child not yet confirmed alive
STATUS_RUNNING = "running"    # child spawned; the worker renews the lease
STATUS_DONE = "done"
STATUS_FAILED = "failed"

#: How many times a launch may be handed back to the queue before it fails.
MAX_ATTEMPTS = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _row(row: Any) -> Dict[str, Any]:
    rec = dict(row)
    rec["payload"] = db.loads(rec.get("payload"), {}) or {}
    return rec


def enqueue(run_id: str, kind: str, payload: Dict[str, Any], *,
            workspace: Optional[str] = None, execution_mode: Optional[str] = None,
            priority: int = 0) -> Dict[str, Any]:
    """Put a launch request on the queue (replacing an earlier row for the
    same run, which is how a resume re-queues a run under its own id)."""
    now = _iso(_now())
    with db.transaction() as conn:
        conn.execute("DELETE FROM run_queue WHERE run_id = ?", (str(run_id),))
        conn.execute(
            "INSERT INTO run_queue (run_id, kind, workspace, execution_mode, priority, payload, "
            "status, attempts, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (str(run_id), kind, workspace, execution_mode or "local", int(priority),
             db.dumps(payload), STATUS_QUEUED, now))
    log.info("queued %s run %s (%s, %s)", kind, str(run_id)[:8], workspace or "-",
             execution_mode or "local")
    return get(run_id) or {}


def get(run_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM run_queue WHERE run_id = ?", (str(run_id),)).fetchone()
    return _row(row) if row is not None else None


def claim(owner: Optional[str] = None, *, ttl_seconds: float = 60.0,
          kinds: Optional[Iterable[str]] = None,
          execution_modes: Optional[Iterable[str]] = None) -> Optional[Dict[str, Any]]:
    """Take the next launch this worker can do, or None.

    Highest priority first, then oldest. A row is claimable when queued, or
    leased with a lapsed lease. ``execution_modes`` restricts the claim to
    what this host can run (a worker without a Docker socket says ``local``).
    """
    owner = owner or owner_id()
    now = _now()
    modes = tuple(execution_modes) if execution_modes else None
    wanted_kinds = tuple(kinds) if kinds else None
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM run_queue WHERE status IN (?, ?) "
            "ORDER BY priority DESC, created_at ASC LIMIT 50",
            (STATUS_QUEUED, STATUS_LEASED)).fetchall()
        for row in rows:
            if modes and str(row["execution_mode"] or "local") not in modes:
                continue
            if wanted_kinds and str(row["kind"]) not in wanted_kinds:
                continue
            if str(row["status"]) == STATUS_LEASED:
                until = _parse(row["lease_until"])
                if until is not None and until > now:
                    continue
            conn.execute(
                "UPDATE run_queue SET status = ?, lease_owner = ?, lease_until = ?, "
                "attempts = attempts + 1, claimed_at = ? WHERE run_id = ?",
                (STATUS_LEASED, owner, _iso(now + timedelta(seconds=ttl_seconds)),
                 _iso(now), row["run_id"]))
            rec = _row(row)
            rec.update({"status": STATUS_LEASED, "lease_owner": owner,
                        "attempts": int(row["attempts"] or 0) + 1})
            return rec
    return None


def renew(run_ids: Sequence[str], owner: Optional[str] = None, *,
          ttl_seconds: float = 60.0) -> int:
    """Extend the lease on rows this worker holds. Returns how many were
    renewed (a row someone else took over is not)."""
    if not run_ids:
        return 0
    owner = owner or owner_id()
    until = _iso(_now() + timedelta(seconds=ttl_seconds))
    with db.transaction() as conn:
        n = 0
        for run_id in run_ids:
            cur = conn.execute(
                "UPDATE run_queue SET lease_until = ? WHERE run_id = ? AND lease_owner = ? "
                "AND status IN (?, ?)",
                (until, str(run_id), owner, STATUS_LEASED, STATUS_RUNNING))
            n += int(getattr(cur, "rowcount", 0) or 0)
        return n


def mark_running(run_id: str, owner: Optional[str] = None) -> None:
    owner = owner or owner_id()
    with db.transaction() as conn:
        conn.execute(
            "UPDATE run_queue SET status = ? WHERE run_id = ? AND lease_owner = ?",
            (STATUS_RUNNING, str(run_id), owner))


def finish(run_id: str, *, error: Optional[str] = None) -> None:
    """Close the row: the child exited (or the launch failed for good)."""
    with db.transaction() as conn:
        conn.execute(
            "UPDATE run_queue SET status = ?, finished_at = ?, last_error = ?, "
            "lease_owner = NULL, lease_until = NULL WHERE run_id = ?",
            (STATUS_FAILED if error else STATUS_DONE, _iso(_now()), error, str(run_id)))


def requeue(run_id: str, error: str) -> bool:
    """Hand a launch back to the queue after a failed attempt. Returns False,
    and marks the row failed, once ``MAX_ATTEMPTS`` is reached."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT attempts FROM run_queue WHERE run_id = ?", (str(run_id),)).fetchone()
        if row is None:
            return False
        if int(row["attempts"] or 0) >= MAX_ATTEMPTS:
            conn.execute(
                "UPDATE run_queue SET status = ?, finished_at = ?, last_error = ?, "
                "lease_owner = NULL, lease_until = NULL WHERE run_id = ?",
                (STATUS_FAILED, _iso(_now()), error, str(run_id)))
            return False
        conn.execute(
            "UPDATE run_queue SET status = ?, lease_owner = NULL, lease_until = NULL, "
            "last_error = ? WHERE run_id = ?",
            (STATUS_QUEUED, error, str(run_id)))
        return True


def delete(run_id: str) -> bool:
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM run_queue WHERE run_id = ?", (str(run_id),))
        return int(getattr(cur, "rowcount", 0) or 0) > 0


def list_queue(statuses: Optional[Iterable[str]] = None, limit: int = 200) -> List[Dict[str, Any]]:
    conn = db.get_conn()
    if statuses:
        wanted = tuple(statuses)
        marks = ",".join("?" for _ in wanted)
        rows = conn.execute(
            f"SELECT * FROM run_queue WHERE status IN ({marks}) "
            "ORDER BY priority DESC, created_at ASC LIMIT ?", (*wanted, int(limit))).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM run_queue ORDER BY created_at DESC LIMIT ?", (int(limit),)).fetchall()
    return [_row(r) for r in rows]


def stats() -> Dict[str, Any]:
    """Counts by status plus the age of the oldest waiting launch, for
    ``/readyz`` and ``/metrics``."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM run_queue GROUP BY status").fetchall()
    counts = {str(r["status"]): int(r["n"]) for r in rows}
    oldest = conn.execute(
        "SELECT MIN(created_at) AS t FROM run_queue WHERE status = ?", (STATUS_QUEUED,)
    ).fetchone()
    oldest_at = _parse(oldest["t"]) if oldest is not None else None
    return {
        "queued": counts.get(STATUS_QUEUED, 0),
        "leased": counts.get(STATUS_LEASED, 0),
        "running": counts.get(STATUS_RUNNING, 0),
        "done": counts.get(STATUS_DONE, 0),
        "failed": counts.get(STATUS_FAILED, 0),
        "oldest_queued_seconds": (
            (_now() - oldest_at).total_seconds() if oldest_at else 0.0),
    }


def sweep(*, stale_after_seconds: float = 0.0) -> Dict[str, int]:
    """Reconcile rows whose worker stopped renewing.

    A ``running`` row with a lapsed lease is a worker that died after
    spawning: the child is detached and keeps going on its own heartbeat, so
    the row is closed rather than the launch repeated. A ``leased`` row with
    a lapsed lease is a worker that died before spawning (or a launch that
    hung): it goes back to ``queued`` for another worker, up to
    ``MAX_ATTEMPTS``. Returns counts of what it did.
    """
    now = _now()
    closed = requeued = failed = 0
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT run_id, status, lease_until, attempts FROM run_queue "
            "WHERE status IN (?, ?)", (STATUS_LEASED, STATUS_RUNNING)).fetchall()
        for row in rows:
            until = _parse(row["lease_until"])
            if until is not None and until + timedelta(seconds=stale_after_seconds) > now:
                continue
            run_id = str(row["run_id"])
            if str(row["status"]) == STATUS_RUNNING:
                conn.execute(
                    "UPDATE run_queue SET status = ?, finished_at = ?, lease_owner = NULL, "
                    "lease_until = NULL, last_error = ? WHERE run_id = ?",
                    (STATUS_DONE, _iso(now), "worker stopped renewing; run detached", run_id))
                closed += 1
            elif int(row["attempts"] or 0) >= MAX_ATTEMPTS:
                conn.execute(
                    "UPDATE run_queue SET status = ?, finished_at = ?, lease_owner = NULL, "
                    "lease_until = NULL, last_error = ? WHERE run_id = ?",
                    (STATUS_FAILED, _iso(now), "no worker completed the launch", run_id))
                failed += 1
            else:
                conn.execute(
                    "UPDATE run_queue SET status = ?, lease_owner = NULL, lease_until = NULL, "
                    "last_error = ? WHERE run_id = ?",
                    (STATUS_QUEUED, "worker lease lapsed before launch", run_id))
                requeued += 1
    return {"closed": closed, "requeued": requeued, "failed": failed}


__all__ = [
    "MAX_ATTEMPTS", "STATUS_DONE", "STATUS_FAILED", "STATUS_LEASED", "STATUS_QUEUED",
    "STATUS_RUNNING", "claim", "delete", "enqueue", "finish", "get", "list_queue",
    "mark_running", "renew", "requeue", "stats", "sweep",
]
