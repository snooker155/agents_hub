"""Persistence for loop definitions, loop runs and the per-iteration log.

Mirrors :mod:`playground.store`: run-level knobs ride in a ``config`` JSON
column rather than as columns, so tightening a ceiling never needs a migration.

Loop *runs* are kept by the implementation flow and team runs share
(:mod:`common.entity_runs`); this module says what a loop run looks like and
keeps the definitions and the iteration log itself.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from common import db
from common.entity_runs import EntityRunStore
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
# what it has spent — is written to the ``progress`` column of its row (part of
# the baseline schema, common/migrations/baseline_schema.sql).

_PROGRESS_COLUMN = "progress"

_RUN_COLUMNS = (
    "loop_run_id", "loop_id", "workspace", "status", "goal", "task_id",
    "session_id", "iterations_done", "best_score", "final_score",
    "stop_reason", "result", "error", "total_cost", "started_at", "finished_at",
)


def _to_run(rec: Dict[str, Any]) -> LoopRun:
    # The resume position is one JSON column, and the watchdog's attempt
    # counter lives inside it rather than as a column of its own: both are
    # written together, by the same writer, on the same schedule.
    position = rec.get("position") or {}
    return LoopRun(
        position=position,
        resume_attempts=int(position.get("resume_attempts") or 0),
        loop_run_id=rec["loop_run_id"],
        loop_id=rec["loop_id"] or "",
        workspace=rec["workspace"],
        status=rec["status"] or "running",
        goal=rec["goal"] or "",
        task_id=rec["task_id"],
        session_id=rec["session_id"],
        iterations_done=int(rec["iterations_done"] or 0),
        best_score=rec["best_score"],
        final_score=rec["final_score"],
        stop_reason=rec["stop_reason"] or "",
        result=rec["result"] or "",
        error=rec["error"],
        total_cost=float(rec["total_cost"] or 0.0),
        started_at=rec["started_at"] or "",
        finished_at=rec["finished_at"],
    )


#: Loop-run records over the shared implementation (common/entity_runs.py):
#: plain columns, plus the ``progress`` JSON column exposed as ``position``.
_RUNS: EntityRunStore[LoopRun] = EntityRunStore(
    table="loop_runs",
    key="loop_run_id",
    columns=_RUN_COLUMNS,
    doc_column=_PROGRESS_COLUMN,
    doc_field="position",
    convert=_to_run,
    resource="loop_runs",
    parent_key="loop_id",
    order_by="started_at DESC",
    live_statuses=("running", "stopping"),
    stopping_status="stopping",
)


def save_position(loop_run_id: str, position: Dict[str, Any]) -> None:
    """Persist where a run has got to, so it can be resumed from here."""
    _RUNS.update(loop_run_id, {"position": position or {}}, notify=False)


def get_position(loop_run_id: str) -> Dict[str, Any]:
    """The stored resume position of a run, or ``{}`` when it has none."""
    rec = _RUNS.read(loop_run_id)
    return (rec or {}).get("position") or {}


def touch_heartbeat(loop_run_id: str) -> None:
    """Refresh the position's ``heartbeat_at``, without touching the rest.

    Called as each flow node of an iteration finishes: an iteration is a whole
    flow and can legitimately take many minutes, so the watchdog must be able to
    tell "still working" from "the process is gone" more often than once per
    iteration. Best-effort: a missed heartbeat is not worth failing a run over.
    """
    try:
        with db.transaction():
            position = get_position(loop_run_id)
            position["heartbeat_at"] = utc_iso()
            save_position(loop_run_id, position)
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
            r["loop_run_id"] for r in conn.execute(
                "SELECT loop_run_id FROM loop_runs WHERE loop_id = ?", (loop_id,)
            ).fetchall()
        ]
        for rid in run_ids:
            conn.execute("DELETE FROM loop_iterations WHERE loop_run_id = ?", (rid,))
        conn.execute("DELETE FROM loop_runs WHERE loop_id = ?", (loop_id,))
        cur = conn.execute("DELETE FROM loops WHERE loop_id = ?", (loop_id,))
        removed = cur.rowcount > 0
    if removed:
        _notify("loops", loop_id=loop_id)
    return removed


# ── Runs ─────────────────────────────────────────────────────────────────────

def save_run(run: LoopRun) -> LoopRun:
    # The whole row is written, resume position included: leaving it out
    # would quietly blank a running loop's position the next time its record
    # was saved. ``resume_attempts`` is not a column; it lives in the position.
    rec = run.to_dict()
    rec.pop("resume_attempts", None)
    rec["position"] = run.position or {}
    _RUNS.upsert(rec, merge=False)
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
        updates["position"] = position
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
    "save_position", "get_position", "touch_heartbeat",
    "request_stop", "stop_requested",
    "save_iteration", "list_iterations", "get_iteration",
]
