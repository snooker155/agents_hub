"""Persistence for eval sets, runs and per-cell results (see common/db.py)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from common import db
from evals.models import (
    Case, EvalResult, EvalRun, EvalSet, GraderSpec, PromptSuggestion, RunConfig, utc_iso,
)


# ── Eval sets ─────────────────────────────────────────────────────────────────

def save_eval_set(evalset: EvalSet) -> EvalSet:
    """Insert or replace a set. Bumps ``updated_at``."""
    evalset.updated_at = utc_iso()
    with db.transaction() as conn:
        conn.execute(
            db.upsert_sql(
                "eval_sets",
                ("eval_set_id", "name", "description", "workspace", "agent_id",
                 "target_kind", "target_id",
                 "cases", "graders", "created_at", "updated_at", "suggest_on_failure"),
                ("eval_set_id",),
            ),
            (
                evalset.eval_set_id, evalset.name, evalset.description,
                evalset.workspace, evalset.agent_id,
                evalset.target_kind, evalset.target_id,
                db.dumps([c.to_dict() for c in evalset.cases]),
                db.dumps([g.to_dict() for g in evalset.graders]),
                evalset.created_at, evalset.updated_at, int(evalset.suggest_on_failure),
            ),
        )
    return evalset


def _cell(row, column: str):
    """A row's value for ``column``, or None on a row without that column."""
    try:
        return row[column]
    except (KeyError, IndexError):
        return None


def _row_to_set(row) -> EvalSet:
    # target_kind/target_id (migration 0015) win; a row written before them
    # has only agent_id, which EvalSet reads as an agent target.
    kind, ident = _cell(row, "target_kind"), _cell(row, "target_id")
    target = {"kind": kind, "id": ident} if kind and ident else None
    return EvalSet(
        eval_set_id=row["eval_set_id"],
        name=row["name"] or "",
        description=row["description"] or "",
        workspace=row["workspace"],
        agent_id=row["agent_id"],
        target=target,
        cases=[Case.from_dict(c) for c in (db.loads(row["cases"], []) or [])],
        graders=[GraderSpec.from_dict(g) for g in (db.loads(row["graders"], []) or [])],
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
        suggest_on_failure=bool(_cell(row, "suggest_on_failure") or False),
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
            db.upsert_sql(
                "eval_runs",
                ("eval_run_id", "eval_set_id", "workspace", "status", "configs",
                 "summary", "total_cost", "error", "started_at", "finished_at", "mode"),
                ("eval_run_id",),
            ),
            (
                run.eval_run_id, run.eval_set_id, run.workspace, run.status,
                db.dumps([c.to_dict() for c in run.configs]),
                db.dumps(run.summary), run.total_cost, run.error,
                run.started_at, run.finished_at, run.mode or "live",
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
        mode=_cell(row, "mode") or "live",
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
            db.upsert_sql(
                "eval_results",
                ("result_id", "eval_run_id", "case_id", "config_label", "run_id",
                 "ok", "error", "output", "scores", "score", "passed",
                 "duration_ms", "inbound_tokens", "outbound_tokens", "cost",
                 "attempt", "target_kind", "trajectory"),
                ("result_id",),
            ),
            (
                result.result_id, result.eval_run_id, result.case_id,
                result.config_label, result.run_id, int(result.ok), result.error,
                result.output, db.dumps(result.scores), result.score,
                int(result.passed), result.duration_ms, result.inbound_tokens,
                result.outbound_tokens, result.cost, int(result.attempt or 1),
                result.target_kind or "agent", db.dumps(list(result.trajectory or [])),
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
        target_kind=_cell(row, "target_kind") or "agent",
        trajectory=list(db.loads(_cell(row, "trajectory"), []) or []),
    )


def get_result(result_id: str) -> Optional[EvalResult]:
    row = db.get_conn().execute(
        "SELECT * FROM eval_results WHERE result_id = ?", (result_id,)
    ).fetchone()
    return _row_to_result(row) if row else None


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


# ── Prompt suggestions ───────────────────────────────────────────────────────

def save_prompt_suggestion(s: PromptSuggestion) -> PromptSuggestion:
    """Insert or replace a suggestion. Bumps ``updated_at``."""
    s.updated_at = utc_iso()
    with db.transaction() as conn:
        conn.execute(
            db.upsert_sql(
                "prompt_suggestions",
                ("suggestion_id", "eval_run_id", "eval_set_id", "agent_id", "workspace",
                 "status", "old_instructions", "new_instructions", "rationale",
                 "case_ids", "suggest_run_id", "applied_run_id", "cost_usd",
                 "created_at", "updated_at", "decided_at"),
                ("suggestion_id",),
            ),
            (
                s.suggestion_id, s.eval_run_id, s.eval_set_id, s.agent_id, s.workspace,
                s.status, s.old_instructions, s.new_instructions, s.rationale,
                db.dumps(list(s.case_ids)), s.suggest_run_id, s.applied_run_id, s.cost_usd,
                s.created_at, s.updated_at, s.decided_at,
            ),
        )
    return s


def _row_to_suggestion(row) -> PromptSuggestion:
    return PromptSuggestion(
        suggestion_id=row["suggestion_id"],
        eval_run_id=row["eval_run_id"] or "",
        eval_set_id=row["eval_set_id"] or "",
        agent_id=row["agent_id"] or "",
        workspace=row["workspace"],
        status=row["status"] or "pending",
        old_instructions=row["old_instructions"] or "",
        new_instructions=row["new_instructions"] or "",
        rationale=row["rationale"] or "",
        case_ids=list(db.loads(row["case_ids"], []) or []),
        suggest_run_id=row["suggest_run_id"],
        applied_run_id=row["applied_run_id"],
        cost_usd=float(row["cost_usd"] or 0.0),
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
        decided_at=row["decided_at"],
    )


def get_prompt_suggestion(suggestion_id: str) -> Optional[PromptSuggestion]:
    row = db.get_conn().execute(
        "SELECT * FROM prompt_suggestions WHERE suggestion_id = ?", (suggestion_id,)
    ).fetchone()
    return _row_to_suggestion(row) if row else None


def list_prompt_suggestions(eval_run_id: str) -> List[PromptSuggestion]:
    rows = db.get_conn().execute(
        "SELECT * FROM prompt_suggestions WHERE eval_run_id = ? ORDER BY created_at DESC",
        (eval_run_id,),
    ).fetchall()
    return [_row_to_suggestion(r) for r in rows]


__all__ = [
    "save_eval_set", "get_eval_set", "list_eval_sets", "delete_eval_set",
    "add_case", "remove_case",
    "save_eval_run", "get_eval_run", "list_eval_runs",
    "save_result", "get_result", "list_results", "build_matrix",
    "save_prompt_suggestion", "get_prompt_suggestion", "list_prompt_suggestions",
]
