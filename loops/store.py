"""Persistence for loop definitions, loop runs and the per-iteration log.

Mirrors :mod:`playground.store`: run-level knobs ride in a ``config`` JSON
column rather than as columns, so tightening a ceiling never needs a migration.

Loop *runs* live in the ``entity_runs`` table every kind of run shares
(:mod:`common.entity_runs`, kind ``loop``); this module says what a loop run
looks like and keeps the definitions and the iteration log itself.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from common import db
from common.entity_runs import EntityRunStore
from common.run_status import RunStatus
from loops.models import Iteration, Loop, LoopRun, utc_iso

_CONFIG_FIELDS = (
    "max_iterations", "min_iterations", "target_score", "patience",
    "cost_ceiling", "max_wall_seconds", "evaluator_mode", "evaluator_agent_id",
    "evaluator_provider", "evaluator_model",
)


# ── The resume position ──────────────────────────────────────────────────────
# A loop run that is interrupted (backend restart, crash, kill) has usually done
# real work: several iterations, each a whole flow. Losing that to a process
# ending is the most expensive kind of forgetting in the product, so after every
# iteration the run's position — what it has done, what the reviewer said and
# what it has spent — is written as the run's ``checkpoint`` (the column every
# kind of run keeps its resume point in, common/entity_runs.py). It is exposed
# on the record as ``position``, the name this package always used.


def _position_of(rec: Dict[str, Any]) -> Dict[str, Any]:
    """The run's resume position, with the heartbeat the shared table keeps
    as a column laid in: the position always said when the loop last showed
    a sign of life, and its readers still ask it."""
    position = dict(rec.get("checkpoint") or rec.get("position") or {})
    if rec.get("heartbeat_at"):
        position["heartbeat_at"] = rec["heartbeat_at"]
    if rec.get("resume_attempts") is not None:
        position["resume_attempts"] = int(rec.get("resume_attempts") or 0)
    return position


def _to_run(rec: Dict[str, Any]) -> LoopRun:
    position = _position_of(rec)
    # The watchdog's attempt counter is a column of the shared table; a
    # position written before the move still carries it too.
    attempts = rec.get("resume_attempts")
    if attempts is None:
        attempts = position.get("resume_attempts") or 0
    return LoopRun(
        position=position,
        resume_attempts=int(attempts or 0),
        loop_run_id=rec["loop_run_id"],
        loop_id=rec.get("loop_id") or "",
        workspace=rec.get("workspace"),
        status=rec.get("status") or RunStatus.pending.value,
        goal=rec.get("goal") or "",
        task_id=rec.get("task_id"),
        session_id=rec.get("session_id"),
        parent_run_id=rec.get("parent_run_id"),
        iterations_done=int(rec.get("iterations_done") or 0),
        best_score=rec.get("best_score"),
        final_score=rec.get("final_score"),
        stop_reason=rec.get("stop_reason") or "",
        result=rec.get("result") or "",
        error=rec.get("error"),
        total_cost=float(rec.get("total_cost") or 0.0),
        heartbeat_at=rec.get("heartbeat_at") or position.get("heartbeat_at"),
        host=rec.get("host") or position.get("host"),
        created_at=rec.get("created_at") or rec.get("started_at") or "",
        started_at=rec.get("started_at") or "",
        finished_at=rec.get("finished_at"),
    )


#: Loop-run records: kind ``loop`` of the shared table (common/entity_runs.py).
RUNS: EntityRunStore[LoopRun] = EntityRunStore(
    "loop",
    convert=_to_run,
    order_by="COALESCE(started_at, created_at, '') DESC, run_id",
    live_statuses=(RunStatus.pending.value, RunStatus.running.value,
                   RunStatus.stopping.value),
)
_RUNS = RUNS


def save_position(loop_run_id: str, position: Dict[str, Any]) -> None:
    """Persist where a run has got to, so it can be resumed from here. The
    position is the run's checkpoint; its heartbeat and attempt counter are
    mirrored into the columns the watchdog reads for every kind."""
    from common import entity_runs
    position = dict(position or {})
    entity_runs.save_checkpoint(loop_run_id, position, heartbeat=False)
    mirrored: Dict[str, Any] = {}
    if position.get("heartbeat_at"):
        mirrored["heartbeat_at"] = position["heartbeat_at"]
    if position.get("resume_attempts") is not None:
        mirrored["resume_attempts"] = int(position.get("resume_attempts") or 0)
    if position.get("host"):
        mirrored["host"] = position["host"]
    if mirrored:
        _RUNS.update(loop_run_id, mirrored, notify=False)


def get_position(loop_run_id: str) -> Dict[str, Any]:
    """The stored resume position of a run, or ``{}`` when it has none."""
    rec = _RUNS.read(loop_run_id)
    if rec is None:
        return {}
    position = _position_of(rec)
    return position if (rec.get("checkpoint") or position.get("heartbeat_at")) else {}


def touch_heartbeat(loop_run_id: str) -> None:
    """Refresh the run's heartbeat, without touching the rest.

    Called as each flow node of an iteration finishes: an iteration is a whole
    flow and can legitimately take many minutes, so the watchdog must be able to
    tell "still working" from "the process is gone" more often than once per
    iteration. Best-effort: a missed heartbeat is not worth failing a run over.
    """
    try:
        from common import entity_runs
        entity_runs.touch_heartbeat(loop_run_id)
    except Exception:
        pass


def _notify(resource: str, **meta) -> None:
    try:
        from common.session_broker import notify_change
        notify_change(resource, **meta)
    except Exception:
        pass


# ── Loop definitions ─────────────────────────────────────────────────────────

def save_loop(loop: Loop) -> Loop:
    loop.updated_at = utc_iso()
    config = {f: getattr(loop, f) for f in _CONFIG_FIELDS}
    with db.transaction() as conn:
        conn.execute(
            db.upsert_sql("loops", ("loop_id", "name", "description", "workspace",
                                    "flow_id", "exit_criterion", "config",
                                    "created_at", "updated_at"), ("loop_id",)),
            (
                loop.loop_id, loop.name, loop.description, loop.workspace,
                loop.flow_id, loop.exit_criterion, db.dumps(config),
                loop.created_at, loop.updated_at,
            ),
        )
    _notify("loops", loop_id=loop.loop_id)
    return loop


def _row_to_loop(row) -> Loop:
    config = db.loads(row["config"], {}) or {}
    return Loop.from_dict({
        "loop_id": row["loop_id"],
        "name": row["name"] or "",
        "description": row["description"] or "",
        "workspace": row["workspace"],
        "flow_id": row["flow_id"] or "",
        "exit_criterion": row["exit_criterion"] or "",
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
        **config,
    })


def get_loop(loop_id: str) -> Optional[Loop]:
    row = db.get_conn().execute(
        "SELECT * FROM loops WHERE loop_id = ?", (loop_id,)
    ).fetchone()
    return _row_to_loop(row) if row else None


def list_loops(workspace: Optional[str] = None) -> List[Loop]:
    conn = db.get_conn()
    if workspace:
        rows = conn.execute(
            "SELECT * FROM loops WHERE workspace = ? OR workspace IS NULL "
            "ORDER BY updated_at DESC",
            (workspace,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM loops ORDER BY updated_at DESC").fetchall()
    return [_row_to_loop(r) for r in rows]


def delete_loop(loop_id: str) -> bool:
    """Delete a loop and every run/iteration it produced."""
    with db.transaction() as conn:
        run_ids = [
            r["run_id"] for r in conn.execute(
                "SELECT run_id FROM entity_runs WHERE kind = 'loop' AND entity_id = ?",
                (loop_id,)
            ).fetchall()
        ]
        for rid in run_ids:
            conn.execute("DELETE FROM loop_iterations WHERE loop_run_id = ?", (rid,))
        conn.execute("DELETE FROM entity_runs WHERE kind = 'loop' AND entity_id = ?",
                     (loop_id,))
        cur = conn.execute("DELETE FROM loops WHERE loop_id = ?", (loop_id,))
        removed = cur.rowcount > 0
    if removed:
        _notify("loops", loop_id=loop_id)
    return removed


# ── Runs ─────────────────────────────────────────────────────────────────────

def save_run(run: LoopRun) -> LoopRun:
    # The whole record is written, resume position included: leaving it out
    # would quietly blank a running loop's position the next time its record
    # was saved. The position is the run's checkpoint column; the document
    # does not carry a copy.
    rec = run.to_dict()
    rec.pop("position", None)
    rec["checkpoint"] = run.position or {}
    _RUNS.upsert(rec, merge=True)
    return run


#: Columns :func:`update_progress` may touch. ``status`` is deliberately absent:
#: a progress write happens while the run is in flight, and a stop request that
#: landed in between must survive it.
_PROGRESS_FIELDS = frozenset({
    "iterations_done", "best_score", "final_score", "result", "total_cost", "error",
})


def update_progress(loop_run_id: str, **fields: Any) -> None:
    """Write mid-run progress without touching ``status``.

    Saving the whole record here would resurrect the in-memory ``running``
    status over a ``stopping`` one written by :func:`request_stop`, and the loop
    would run on after the user pressed stop.

    ``position=`` is accepted alongside the columns and stored as the run's
    resume point (see :func:`save_position`).
    """
    position = fields.pop("position", None)
    updates = {k: v for k, v in fields.items() if k in _PROGRESS_FIELDS}
    if position is not None:
        updates["checkpoint"] = dict(position)
        if position.get("heartbeat_at"):
            updates["heartbeat_at"] = position["heartbeat_at"]
        if position.get("host"):
            updates["host"] = position["host"]
    if updates:
        _RUNS.update(loop_run_id, updates)


def get_run(loop_run_id: str) -> Optional[LoopRun]:
    return _RUNS.get(loop_run_id)


def list_runs(loop_id: Optional[str] = None, limit: int = 50) -> List[LoopRun]:
    return _RUNS.list({"loop_id": loop_id} if loop_id else None, limit=limit)


def request_stop(loop_run_id: str) -> bool:
    """Ask a running loop to stop. The runner checks between nodes and between
    iterations, so the flow is never left half-applied."""
    return _RUNS.request_stop(loop_run_id)


def stop_requested(loop_run_id: str) -> bool:
    return _RUNS.stop_requested(loop_run_id)


# ── Iterations ───────────────────────────────────────────────────────────────

def save_iteration(it: Iteration) -> Iteration:
    with db.transaction() as conn:
        conn.execute(
            db.upsert_sql("loop_iterations", (
                "loop_run_id", "iteration", "flow_run_id", "status", "score", "verdict",
                "reason", "feedback", "output", "node_outputs", "state", "evaluator_agent",
                "evaluator_raw", "cost", "duration_ms", "started_at", "finished_at"),
                ("loop_run_id", "iteration")),
            (
                it.loop_run_id, it.iteration, it.flow_run_id, it.status, it.score,
                it.verdict, it.reason, it.feedback, it.output,
                db.dumps(it.node_outputs), db.dumps(it.state), it.evaluator_agent,
                it.evaluator_raw, it.cost, it.duration_ms, it.started_at,
                it.finished_at,
            ),
        )
    return it


def _row_to_iteration(row) -> Dict[str, Any]:
    return {
        "loop_run_id": row["loop_run_id"],
        "iteration": int(row["iteration"]),
        "flow_run_id": row["flow_run_id"] or "",
        "status": row["status"] or "",
        "score": row["score"],
        "verdict": row["verdict"] or "",
        "reason": row["reason"] or "",
        "feedback": row["feedback"] or "",
        "output": row["output"] or "",
        "node_outputs": db.loads(row["node_outputs"], {}) or {},
        "state": db.loads(row["state"], {}) or {},
        "evaluator_agent": row["evaluator_agent"] or "",
        "evaluator_raw": row["evaluator_raw"] or "",
        "cost": float(row["cost"] or 0.0),
        "duration_ms": int(row["duration_ms"] or 0),
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def list_iterations(loop_run_id: str, since: int = 0) -> List[Dict[str, Any]]:
    """Iterations of a run in order. ``since`` returns only later ones so a live
    page can poll cheaply."""
    rows = db.get_conn().execute(
        "SELECT * FROM loop_iterations WHERE loop_run_id = ? AND iteration > ? "
        "ORDER BY iteration",
        (loop_run_id, since),
    ).fetchall()
    return [_row_to_iteration(r) for r in rows]


def get_iteration(loop_run_id: str, iteration: int) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM loop_iterations WHERE loop_run_id = ? AND iteration = ?",
        (loop_run_id, iteration),
    ).fetchone()
    return _row_to_iteration(row) if row else None


__all__ = [
    "save_loop", "get_loop", "list_loops", "delete_loop",
    "save_run", "get_run", "list_runs", "update_progress",
    "save_position", "get_position", "touch_heartbeat", "RUNS",
    "request_stop", "stop_requested",
    "save_iteration", "list_iterations", "get_iteration",
]
