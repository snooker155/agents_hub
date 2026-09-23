"""Online evals: graders run on a sample of production runs.

An ``online_eval`` alert rule (``notify/store.py``) names an agent filter, a
sample rate, a list of grader specs in the same form an eval set uses
(``evals.models.GraderSpec``), a minimum score and a severity. The flow:

1. ``notify.rules.evaluate_run_finished`` calls :func:`maybe_enqueue` for each
   enabled ``online_eval`` rule when a run reaches a terminal status. The
   sampling decision is a hash of the rule id and the run id against the
   rate, so it is the same on every replica and on a re-evaluation, and the
   insert is one row: the run's finish path never waits on a grader.
2. The ``online_evals`` singleton (``common/singletons.py``, one replica at a
   time under a lease) calls :func:`process_pending` every few seconds. It
   loads the run's output and structured payload, runs the graders, writes
   ``online_eval_results`` and raises the rule's notification when the score
   is below ``min_score``.

Jobs live in ``online_eval_jobs`` (migration 0012), so a restart picks up
where it left off. A job whose grading raised is marked ``failed`` with the
error; a job left ``running`` by a process that died is put back once and
failed after :data:`MAX_ATTEMPTS`.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from common import db

log = logging.getLogger(__name__)

RULE_KIND = "online_eval"
SEVERITIES = ("info", "warning", "error")
#: Grading attempts before a job is given up on.
MAX_ATTEMPTS = 3
#: A job ``running`` longer than this is treated as abandoned.
STALE_RUNNING_SECONDS = 15 * 60
POLL_SECONDS = 10.0
BATCH = 20

_GRADED_STATUSES = ("completed",)
_SKIP_CHANNELS = frozenset({"eval", "replay"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Rule fields ─────────────────────────────────────────────────────────────

def normalize_graders(graders: Any) -> List[Dict[str, Any]]:
    """Validate a rule's grader list; returns ``GraderSpec.to_dict()`` shapes."""
    from evals.graders import GRADERS
    from evals.models import GraderSpec

    if not isinstance(graders, list) or not graders:
        raise ValueError("an online_eval rule needs at least one grader")
    out = []
    for item in graders:
        if not isinstance(item, dict):
            raise ValueError("each grader is an object with kind and params")
        spec = GraderSpec.from_dict(item)
        if spec.kind not in GRADERS:
            raise ValueError(f"unknown grader {spec.kind!r}")
        out.append(spec.to_dict())
    return out


def normalize_rule_fields(data: Dict[str, Any], *, partial: bool = False) -> Dict[str, Any]:
    """The ``online_eval`` fields of a rule create or update, validated.

    With ``partial`` only the keys present in ``data`` are returned.
    """
    out: Dict[str, Any] = {}
    if not partial or "sample_rate" in data:
        try:
            rate = float(data.get("sample_rate", 1.0) if data.get("sample_rate") is not None else 1.0)
        except (TypeError, ValueError):
            raise ValueError("sample_rate must be a number between 0 and 1")
        if not (0.0 <= rate <= 1.0):
            raise ValueError("sample_rate must be between 0 and 1")
        out["sample_rate"] = rate
    if not partial or "min_score" in data:
        try:
            min_score = float(data.get("min_score", 0.5) if data.get("min_score") is not None else 0.5)
        except (TypeError, ValueError):
            raise ValueError("min_score must be a number between 0 and 1")
        if not (0.0 <= min_score <= 1.0):
            raise ValueError("min_score must be between 0 and 1")
        out["min_score"] = min_score
    if not partial or "severity" in data:
        severity = str(data.get("severity") or "warning").strip().lower()
        if severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}")
        out["severity"] = severity
    if not partial or "graders" in data:
        out["graders"] = normalize_graders(data.get("graders"))
    for key in ("expected", "rubric"):
        if not partial or key in data:
            value = data.get(key)
            out[key] = str(value) if value not in (None, "") else None
    return out


# ── Sampling and the queue ──────────────────────────────────────────────────

def sample_point(rule_id: str, run_id: str) -> float:
    """A stable number in [0, 1) for this (rule, run) pair."""
    digest = hashlib.sha256(f"{rule_id}:{run_id}".encode("utf-8")).hexdigest()
    return int(digest[:15], 16) / float(16 ** 15)


def is_sampled(rule: Dict[str, Any], run_id: str) -> bool:
    rate = float(rule.get("sample_rate") or 0.0)
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True
    return sample_point(str(rule.get("id") or ""), str(run_id)) < rate


def maybe_enqueue(workspace: str, rule: Dict[str, Any], run: Dict[str, Any]) -> bool:
    """Queue a grading job when ``run`` matches the rule and is sampled.

    Returns True when a job row was written. Only completed runs are graded
    (a failed run has its own ``run_failed`` rule), and eval or replay runs
    never are.
    """
    run_id = str(run.get("run_id") or "")
    if not run_id:
        return False
    if str(run.get("status") or "") not in _GRADED_STATUSES:
        return False
    if (run.get("channel") or "") in _SKIP_CHANNELS:
        return False
    agent_id = str(run.get("agent_id") or "")
    wanted = rule.get("agent_id")
    if wanted and wanted != agent_id:
        return False
    if not is_sampled(rule, run_id):
        return False
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO online_eval_jobs "
            "(run_id, rule_id, workspace, agent_id, rule_json, status, attempts, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', 0, ?) "
            "ON CONFLICT (run_id, rule_id) DO NOTHING",
            (run_id, str(rule.get("id") or ""), workspace, agent_id,
             db.dumps(rule), _now().isoformat()))
        return bool(getattr(cur, "rowcount", 1))


def list_jobs(status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    conn = db.get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM online_eval_jobs WHERE status = ? ORDER BY id LIMIT ?",
            (status, int(limit))).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM online_eval_jobs ORDER BY id LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def _requeue_stale() -> None:
    cutoff = (_now() - timedelta(seconds=STALE_RUNNING_SECONDS)).isoformat()
    with db.transaction() as conn:
        conn.execute(
            "UPDATE online_eval_jobs SET status = 'failed', finished_at = ?, "
            "error = 'abandoned while grading' "
            "WHERE status = 'running' AND started_at < ? AND attempts >= ?",
            (_now().isoformat(), cutoff, MAX_ATTEMPTS))
        conn.execute(
            "UPDATE online_eval_jobs SET status = 'pending' "
            "WHERE status = 'running' AND started_at < ? AND attempts < ?",
            (cutoff, MAX_ATTEMPTS))


def _claim(limit: int) -> List[Dict[str, Any]]:
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM online_eval_jobs WHERE status = 'pending' ORDER BY id LIMIT ?",
            (int(limit),)).fetchall()
        jobs = [dict(r) for r in rows]
        now = _now().isoformat()
        for job in jobs:
            conn.execute(
                "UPDATE online_eval_jobs SET status = 'running', started_at = ?, "
                "attempts = attempts + 1 WHERE id = ?", (now, job["id"]))
    return jobs


def _finish(job_id: int, status: str, error: Optional[str] = None) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE online_eval_jobs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
            (status, (error or None) and str(error)[:500], _now().isoformat(), job_id))


# ── Grading ─────────────────────────────────────────────────────────────────

def _load_run(run_id: str) -> Dict[str, Any]:
    """The run record, its output and the user message it answered."""
    from managers import run_manager as rm

    run = rm.get_run_by_id(run_id)
    if not run:
        raise ValueError(f"run {run_id} not found")
    try:
        proc = rm.get_run_process(run_id) or {}
    except Exception:
        proc = {}
    input_ctx = proc.get("input_context") or {}
    output = str((proc.get("response") or {}).get("text") or run.get("output") or "")
    user_message = str(input_ctx.get("user_message") or run.get("input") or "")
    return {"run": run, "output": output, "input": user_message}


def _definition_version(agent_id: str, run_id: str, definition_hash: Optional[str]
                        ) -> Dict[str, Any]:
    """The stored version behind a run: its experiment arm when it had one,
    else the newest history row with the run's definition hash."""
    from evals import experiments

    assignment = experiments.assignment_for_run(run_id)
    if assignment:
        return {"version": int(assignment["version"]),
                "experiment_id": assignment["experiment_id"]}
    if definition_hash:
        row = db.get_conn().execute(
            "SELECT version FROM agent_versions WHERE agent_id = ? AND hash = ? "
            "ORDER BY version DESC LIMIT 1", (agent_id, definition_hash)).fetchone()
        if row is not None:
            return {"version": int(row["version"]), "experiment_id": None}
    return {"version": None, "experiment_id": None}


def grade_run(rule: Dict[str, Any], loaded: Dict[str, Any]) -> Dict[str, Any]:
    """Run the rule's graders on one loaded run.

    Each grader gets a case built from the run: ``input`` is the message the
    run answered, ``expected`` and ``rubric`` come from the grader's own
    params when set, else from the rule. The score is the weighted mean and
    ``passed`` needs every grader to pass, as in ``evals.graders.grade_all``.
    """
    from evals.graders import grade
    from evals.models import Case, GraderSpec

    run_id = str(loaded["run"].get("run_id") or "")
    details = []
    weighted = 0.0
    total_weight = 0.0
    all_passed = True
    for raw in rule.get("graders") or []:
        spec = GraderSpec.from_dict(raw)
        params = dict(spec.params or {})
        case = Case(
            input=loaded["input"],
            expected=params.get("expected") if params.get("expected") not in (None, "") else rule.get("expected"),
            rubric=params.get("rubric") if params.get("rubric") not in (None, "") else rule.get("rubric"),
            source_run_id=run_id,
        )
        result = grade(loaded["output"], case, spec, run_id=run_id)
        weight = max(0.0, float(spec.weight or 0.0))
        weighted += result.score * weight
        total_weight += weight
        all_passed = all_passed and bool(result.passed)
        details.append({**result.to_dict(), "weight": weight})
    if not details:
        return {"score": 0.0, "passed": False, "details": []}
    if total_weight <= 0:
        score = sum(d["score"] for d in details) / len(details)
    else:
        score = weighted / total_weight
    return {"score": round(score, 4), "passed": all_passed, "details": details}


def _write_result(job: Dict[str, Any], rule: Dict[str, Any], run: Dict[str, Any],
                  graded: Dict[str, Any]) -> Dict[str, Any]:
    agent_id = str(run.get("agent_id") or job.get("agent_id") or "")
    definition_hash = run.get("definition_hash")
    version = _definition_version(agent_id, str(job["run_id"]), definition_hash)
    record = {
        "run_id": str(job["run_id"]),
        "rule_id": str(job["rule_id"]),
        "workspace": job.get("workspace"),
        "agent_id": agent_id,
        "definition_hash": definition_hash,
        "definition_version": version["version"],
        "experiment_id": version["experiment_id"],
        "score": float(graded["score"]),
        "passed": bool(graded["passed"]),
        "details": graded["details"],
        "graded_at": _now().isoformat(),
    }
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO online_eval_results "
            "(run_id, rule_id, workspace, agent_id, definition_hash, definition_version, "
            "experiment_id, score, passed, details, graded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (run_id, rule_id) DO UPDATE SET score = excluded.score, "
            "passed = excluded.passed, details = excluded.details, graded_at = excluded.graded_at",
            (record["run_id"], record["rule_id"], record["workspace"], record["agent_id"],
             record["definition_hash"], record["definition_version"], record["experiment_id"],
             record["score"], 1 if record["passed"] else 0, db.dumps(record["details"]),
             record["graded_at"]))
    return record


def _maybe_fire(job: Dict[str, Any], rule: Dict[str, Any], record: Dict[str, Any]) -> bool:
    min_score = float(rule.get("min_score") if rule.get("min_score") is not None else 0.5)
    if record["score"] >= min_score:
        return False
    from notify import rules as notify_rules

    failing = [d for d in record["details"] if not d.get("passed")]
    lines = [f"Run {record['run_id']} by '{record['agent_id'] or 'agent'}' scored "
             f"{record['score']:.2f}, below {min_score:.2f}."]
    if record.get("definition_version") is not None:
        lines.append(f"Definition version: v{record['definition_version']}.")
    for d in failing[:3]:
        lines.append(f"{d.get('kind')}: {str(d.get('detail') or '')[:200]}")
    notify_rules._fire(
        str(job.get("workspace") or "default"), rule,
        title=f"Online eval below {min_score:.2f}: {record['agent_id'] or 'agent'}",
        body="\n".join(lines),
        severity=str(rule.get("severity") or "warning"),
    )
    return True


def process_job(job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Grade one claimed job. Never raises: a failure is written on the job."""
    try:
        rule = db.loads(job.get("rule_json"), {}) or {}
        loaded = _load_run(str(job["run_id"]))
        graded = grade_run(rule, loaded)
        record = _write_result(job, rule, loaded["run"], graded)
        try:
            _maybe_fire(job, rule, record)
        except Exception:
            log.warning("online eval: could not raise the alert for run %s",
                        job.get("run_id"), exc_info=True)
        _finish(int(job["id"]), "done")
        return record
    except Exception as exc:  # noqa: BLE001 - recorded on the job, never retried forever
        log.warning("online eval: grading run %s failed: %s", job.get("run_id"), exc)
        try:
            _finish(int(job["id"]), "failed", f"{type(exc).__name__}: {exc}")
        except Exception:
            log.debug("online eval: could not record the failure", exc_info=True)
        return None


def process_pending(limit: int = BATCH) -> int:
    """Grade up to ``limit`` pending jobs. Returns how many were graded."""
    _requeue_stale()
    done = 0
    for job in _claim(limit):
        if process_job(job) is not None:
            done += 1
    return done


# ── Reads for the API ───────────────────────────────────────────────────────

def _row_to_result(row) -> Dict[str, Any]:
    rec = dict(row)
    rec["passed"] = bool(rec.get("passed"))
    rec["details"] = db.loads(rec.get("details"), []) or []
    return rec


def recent_results(agent_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    rows = db.get_conn().execute(
        "SELECT * FROM online_eval_results WHERE agent_id = ? "
        "ORDER BY graded_at DESC LIMIT ?", (str(agent_id), max(1, min(int(limit), 500)))
    ).fetchall()
    return [_row_to_result(r) for r in rows]


def summary(agent_id: str) -> Dict[str, Any]:
    """Count, mean score and pass rate overall, by definition version (or
    hash when the version is unknown) and by rule."""
    conn = db.get_conn()
    base = ("SELECT COUNT(*) AS n, AVG(score) AS mean_score, "
            f"{db.sum_if('passed = 1')} AS passed_n FROM online_eval_results WHERE agent_id = ?")
    overall = conn.execute(base, (str(agent_id),)).fetchone()

    def shape(row) -> Dict[str, Any]:
        n = int(row["n"] or 0)
        return {
            "count": n,
            "mean_score": round(float(row["mean_score"]), 4) if row["mean_score"] is not None else None,
            "pass_rate": round(int(row["passed_n"] or 0) / n, 4) if n else None,
        }

    by_version_rows = conn.execute(
        "SELECT definition_version, definition_hash, COUNT(*) AS n, AVG(score) AS mean_score, "
        f"{db.sum_if('passed = 1')} AS passed_n, MAX(graded_at) AS last_graded_at "
        "FROM online_eval_results WHERE agent_id = ? "
        "GROUP BY definition_version, definition_hash "
        "ORDER BY MAX(graded_at) DESC", (str(agent_id),)).fetchall()
    by_rule_rows = conn.execute(
        "SELECT rule_id, COUNT(*) AS n, AVG(score) AS mean_score, "
        f"{db.sum_if('passed = 1')} AS passed_n, MAX(graded_at) AS last_graded_at "
        "FROM online_eval_results WHERE agent_id = ? GROUP BY rule_id "
        "ORDER BY MAX(graded_at) DESC", (str(agent_id),)).fetchall()
    pending = conn.execute(
        "SELECT COUNT(*) FROM online_eval_jobs WHERE agent_id = ? AND status IN ('pending', 'running')",
        (str(agent_id),)).fetchone()[0]
    failed = conn.execute(
        "SELECT COUNT(*) FROM online_eval_jobs WHERE agent_id = ? AND status = 'failed'",
        (str(agent_id),)).fetchone()[0]
    return {
        "agent_id": agent_id,
        **shape(overall),
        "pending_jobs": int(pending or 0),
        "failed_jobs": int(failed or 0),
        "by_version": [
            {"definition_version": r["definition_version"],
             "definition_hash": r["definition_hash"],
             "last_graded_at": r["last_graded_at"], **shape(r)}
            for r in by_version_rows
        ],
        "by_rule": [
            {"rule_id": r["rule_id"], "last_graded_at": r["last_graded_at"], **shape(r)}
            for r in by_rule_rows
        ],
    }


# ── The background loop (a leased singleton) ────────────────────────────────

class _Service:
    """A thread that grades pending jobs every :data:`POLL_SECONDS`.

    Started and stopped by the singleton supervisor, which holds the
    ``online_evals`` lease: with several replicas exactly one grades.
    """

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    async def start(self) -> None:
        if self.is_running():
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="online-evals", daemon=True)
        self._thread.start()

    async def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            import asyncio
            await asyncio.to_thread(thread.join, 5.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                process_pending()
            except Exception:
                log.warning("online eval loop tick failed", exc_info=True)
            self._stop.wait(POLL_SECONDS)


service = _Service()


__all__ = [
    "RULE_KIND", "SEVERITIES", "MAX_ATTEMPTS", "normalize_rule_fields", "normalize_graders",
    "sample_point", "is_sampled", "maybe_enqueue", "list_jobs", "grade_run",
    "process_job", "process_pending", "recent_results", "summary", "service",
]
