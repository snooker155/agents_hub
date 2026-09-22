"""Persistence for eval sets, runs and per-cell results (SQLite, see common/db.py)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from common import db
from evals.models import (
    Case, EvalResult, EvalRun, EvalSet, GraderSpec, RunConfig, utc_iso,
)


# ── Eval sets ─────────────────────────────────────────────────────────────────

def save_eval_set(evalset: EvalSet) -> EvalSet:
    """Insert or replace a set. Bumps ``updated_at``."""
    evalset.updated_at = utc_iso()
    with db.transaction() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO eval_sets
               (eval_set_id, name, description, workspace, agent_id,
                cases, graders, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                evalset.eval_set_id, evalset.name, evalset.description,
                evalset.workspace, evalset.agent_id,
                db.dumps([c.to_dict() for c in evalset.cases]),
                db.dumps([g.to_dict() for g in evalset.graders]),
                evalset.created_at, evalset.updated_at,
            ),
        )
    return evalset


def _row_to_set(row) -> EvalSet:
    return EvalSet(
        eval_set_id=row["eval_set_id"],
        name=row["name"] or "",
        description=row["description"] or "",
        workspace=row["workspace"],
        agent_id=row["agent_id"],
        cases=[Case.from_dict(c) for c in (db.loads(row["cases"], []) or [])],
        graders=[GraderSpec.from_dict(g) for g in (db.loads(row["graders"], []) or [])],
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
    )


def get_eval_set(eval_set_id: str) -> Optional[EvalSet]:
    row = db.get_conn().execute(
        "SELECT * FROM eval_sets WHERE eval_set_id = ?", (eval_set_id,)
    ).fetchone()
    return _row_to_set(row) if row else None


def list_eval_sets(workspace: Optional[str] = None) -> List[EvalSet]:
    """Sets for a workspace, plus workspace-less (global) ones."""
    conn = db.get_conn()
    if workspace:
        rows = conn.execute(
            "SELECT * FROM eval_sets WHERE workspace = ? OR workspace IS NULL "
            "ORDER BY updated_at DESC",
            (workspace,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM eval_sets ORDER BY updated_at DESC").fetchall()
    return [_row_to_set(r) for r in rows]


def delete_eval_set(eval_set_id: str) -> bool:
    """Delete a set and every run and result under it."""
    with db.transaction() as conn:
        run_ids = [
            r["eval_run_id"] for r in conn.execute(
                "SELECT eval_run_id FROM eval_runs WHERE eval_set_id = ?", (eval_set_id,)
            ).fetchall()
        ]
        for rid in run_ids:
            conn.execute("DELETE FROM eval_results WHERE eval_run_id = ?", (rid,))
        conn.execute("DELETE FROM eval_runs WHERE eval_set_id = ?", (eval_set_id,))
        cur = conn.execute("DELETE FROM eval_sets WHERE eval_set_id = ?", (eval_set_id,))
        return cur.rowcount > 0


def add_case(eval_set_id: str, case: Case) -> Optional[EvalSet]:
    """Append a case to an existing set — the "save this run as an eval case" path."""
    evalset = get_eval_set(eval_set_id)
    if not evalset:
        return None
    evalset.cases.append(case)
    return save_eval_set(evalset)


def remove_case(eval_set_id: str, case_id: str) -> Optional[EvalSet]:
    evalset = get_eval_set(eval_set_id)
    if not evalset:
        return None
    evalset.cases = [c for c in evalset.cases if c.case_id != case_id]
    return save_eval_set(evalset)


# ── Eval runs ─────────────────────────────────────────────────────────────────

def save_eval_run(run: EvalRun) -> EvalRun:
    with db.transaction() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO eval_runs
               (eval_run_id, eval_set_id, workspace, status, configs, summary,
                total_cost, error, started_at, finished_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                run.eval_run_id, run.eval_set_id, run.workspace, run.status,
                db.dumps([c.to_dict() for c in run.configs]),
                db.dumps(run.summary), run.total_cost, run.error,
                run.started_at, run.finished_at,
            ),
        )
    return run


def _row_to_run(row) -> EvalRun:
    return EvalRun(
        eval_run_id=row["eval_run_id"],
        eval_set_id=row["eval_set_id"] or "",
        workspace=row["workspace"],
        status=row["status"] or "running",
        configs=[RunConfig.from_dict(c) for c in (db.loads(row["configs"], []) or [])],
        summary=db.loads(row["summary"], {}) or {},
        total_cost=float(row["total_cost"] or 0.0),
        error=row["error"],
        started_at=row["started_at"] or "",
        finished_at=row["finished_at"],
    )


def get_eval_run(eval_run_id: str) -> Optional[EvalRun]:
    row = db.get_conn().execute(
        "SELECT * FROM eval_runs WHERE eval_run_id = ?", (eval_run_id,)
    ).fetchone()
    return _row_to_run(row) if row else None


def list_eval_runs(eval_set_id: Optional[str] = None, limit: int = 50) -> List[EvalRun]:
    conn = db.get_conn()
    if eval_set_id:
        rows = conn.execute(
            "SELECT * FROM eval_runs WHERE eval_set_id = ? ORDER BY started_at DESC LIMIT ?",
            (eval_set_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM eval_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_run(r) for r in rows]


# ── Results ───────────────────────────────────────────────────────────────────

def save_result(result: EvalResult) -> EvalResult:
    with db.transaction() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO eval_results
               (result_id, eval_run_id, case_id, config_label, run_id, ok, error,
                output, scores, score, passed, duration_ms, inbound_tokens,
                outbound_tokens, cost, attempt)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                result.result_id, result.eval_run_id, result.case_id,
                result.config_label, result.run_id, int(result.ok), result.error,
                result.output, db.dumps(result.scores), result.score,
                int(result.passed), result.duration_ms, result.inbound_tokens,
                result.outbound_tokens, result.cost, int(result.attempt or 1),
            ),
        )
    return result


def _row_to_result(row) -> EvalResult:
    return EvalResult(
        result_id=row["result_id"],
        eval_run_id=row["eval_run_id"],
        case_id=row["case_id"] or "",
        config_label=row["config_label"] or "",
        run_id=row["run_id"],
        attempt=int(row["attempt"] or 1),
        ok=bool(row["ok"]),
        error=row["error"],
        output=row["output"] or "",
        scores=db.loads(row["scores"], {}) or {},
        score=float(row["score"] or 0.0),
        passed=bool(row["passed"]),
        duration_ms=int(row["duration_ms"] or 0),
        inbound_tokens=int(row["inbound_tokens"] or 0),
        outbound_tokens=int(row["outbound_tokens"] or 0),
        cost=float(row["cost"] or 0.0),
    )


def list_results(eval_run_id: str) -> List[EvalResult]:
    rows = db.get_conn().execute(
        "SELECT * FROM eval_results WHERE eval_run_id = ?", (eval_run_id,)
    ).fetchall()
    return [_row_to_result(r) for r in rows]


def build_matrix(eval_run_id: str) -> Dict[str, Any]:
    """Results reshaped as ``{case_id: {config_label: result}}`` for the matrix UI."""
    matrix: Dict[str, Dict[str, Any]] = {}
    for r in list_results(eval_run_id):
        matrix.setdefault(r.case_id, {})[r.config_label] = r.to_dict()
    return matrix


__all__ = [
    "save_eval_set", "get_eval_set", "list_eval_sets", "delete_eval_set",
    "add_case", "remove_case",
    "save_eval_run", "get_eval_run", "list_eval_runs",
    "save_result", "list_results", "build_matrix",
]
