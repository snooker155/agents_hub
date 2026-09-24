"""
Routes: a task's outcome (tasks/outcome.py).

``GET|PUT|DELETE /api/tasks/{task_id}/outcome``      the rubric and grading settings
``GET  /api/tasks/{task_id}/outcome/evaluations``    every grading, oldest first
``POST /api/tasks/{task_id}/outcome/grade``          grade the latest completed run now

The same visibility rule as the task routes applies (the task's workspace
must be one the caller can see), and every write lands in the audit log.
Grading is a model call, so the grade route is a plain ``def`` and runs on
the thread pool rather than holding the event loop.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Union
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common import access, audit, identity

router = APIRouter(tags=["outcomes"])


class OutcomeIn(BaseModel):
    rubric: str = ""
    max_iterations: Optional[int] = None
    grader: Optional[Union[str, Dict[str, Any]]] = None
    threshold: Optional[float] = None


def _task_or_404(task_id: str, request: Request):
    from tasks import service as tasks_service

    try:
        tid = UUID(str(task_id))
    except ValueError:
        task = tasks_service.find_task_by_key(str(task_id).strip())
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
    else:
        task = tasks_service.get_task(tid)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
    access.require_visible(identity.request_principal(request), task.workspace)
    return task


def _view(task) -> Dict[str, Any]:
    from tasks.outcome import DEFAULT_MAX_ITERATIONS, MAX_ITERATIONS_LIMIT, parse_rubric

    outcome = getattr(task, "outcome", None)
    evaluations = list(getattr(task, "outcome_evaluations", None) or [])
    attempts = sum(1 for e in evaluations if (e.get("trigger") or "run") == "run")
    return {
        "task_id": str(task.id),
        "outcome": outcome,
        "criteria": parse_rubric(outcome.get("rubric", "")) if outcome else [],
        "evaluations": evaluations,
        "attempts": attempts,
        "latest": evaluations[-1] if evaluations else None,
        "defaults": {"max_iterations": DEFAULT_MAX_ITERATIONS,
                     "max_iterations_limit": MAX_ITERATIONS_LIMIT},
    }


def _audit(request: Request, action: str, task, details: Dict[str, Any]) -> None:
    audit.record(
        action, principal=identity.request_principal(request), object_type="task",
        object_id=str(task.id), workspace=getattr(task, "workspace", None),
        ip=identity.client_ip(request), details=details,
    )


@router.get("/api/tasks/{task_id}/outcome")
async def get_outcome(task_id: str, request: Request):
    """The task's outcome (null when it has none), its parsed criteria and gradings."""
    return _view(_task_or_404(task_id, request))


@router.put("/api/tasks/{task_id}/outcome")
async def put_outcome(task_id: str, body: OutcomeIn, request: Request):
    """Set or replace the outcome. Past gradings are kept."""
    from tasks import service as tasks_service
    from tasks.outcome import OutcomeError, normalize_outcome

    task = _task_or_404(task_id, request)
    try:
        outcome = normalize_outcome(body.model_dump())
    except OutcomeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    tasks_service.update_task(task.id, outcome=outcome)
    _audit(request, "task.outcome.set", task, {
        "max_iterations": outcome["max_iterations"], "grader": outcome["grader"],
        "threshold": outcome["threshold"], "rubric_chars": len(outcome["rubric"]),
        "replaced": bool(getattr(task, "outcome", None)),
    })
    return _view(tasks_service.get_task(task.id))


@router.delete("/api/tasks/{task_id}/outcome")
async def delete_outcome(task_id: str, request: Request):
    """Remove the outcome and its gradings: the task resolves on a finished run again."""
    from tasks import service as tasks_service

    task = _task_or_404(task_id, request)
    had = bool(getattr(task, "outcome", None))
    tasks_service.update_task(task.id, outcome=None, outcome_evaluations=[])
    if had:
        _audit(request, "task.outcome.delete", task, {
            "evaluations": len(getattr(task, "outcome_evaluations", None) or []),
        })
    return _view(tasks_service.get_task(task.id))


@router.get("/api/tasks/{task_id}/outcome/evaluations")
async def list_evaluations(task_id: str, request: Request):
    task = _task_or_404(task_id, request)
    return {"task_id": str(task.id),
            "evaluations": list(getattr(task, "outcome_evaluations", None) or [])}


@router.post("/api/tasks/{task_id}/outcome/grade")
def grade_outcome_now(task_id: str, request: Request):
    """Grade the task's latest completed run against its outcome, now.

    Records the grading (``trigger: manual``, not counted as an attempt) and
    changes nothing else: no relaunch, no status change.
    """
    from tasks import service as tasks_service
    from tasks.outcome import OutcomeError, grade_now

    task = _task_or_404(task_id, request)
    try:
        evaluation = grade_now(task.id)
    except OutcomeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _audit(request, "task.outcome.grade", task, {
        "run_id": evaluation.get("run_id"), "passed": evaluation.get("passed"),
        "score": evaluation.get("score"), "cost_usd": evaluation.get("cost_usd"),
    })
    return {"evaluation": evaluation, **_view(tasks_service.get_task(task.id))}
