"""
Plan API routes: scheduled jobs (future notifications / agent tasks) and the
user notification inbox.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from plans import JobKind, JobStatus, Recurrence
from plans import service as plan_service
from plans.service import (
    NOTIFICATIONS_CHANNEL,
    fire_to_dict,
    job_to_dict,
    notification_to_dict,
)

router = APIRouter(prefix="/api/plan", tags=["plan"])


# -------------------- request models --------------------

class JobCreate(BaseModel):
    kind: JobKind
    title: str = Field(..., min_length=1)
    message: str = ""
    # Either an ISO datetime (naive → UTC) or a relative delay.
    run_at: Optional[datetime] = None
    delay_minutes: Optional[int] = Field(None, ge=1)
    recurrence: Recurrence = Recurrence.none
    cron: Optional[str] = Field(None, description="Cron expression, required when recurrence='cron'")
    timezone: Optional[str] = Field(None, description="IANA timezone name, e.g. 'Europe/Berlin'. Defaults to UTC.")
    catch_up: bool = Field(
        False,
        description="When more than one occurrence was missed, advance one slot per "
                    "firing instead of jumping straight to the next future one.",
    )
    workspace: Optional[str] = None
    agent_id: Optional[str] = None
    # flow jobs: which flow to trigger, an optional JSON seed, and a concurrency cap.
    flow_id: Optional[str] = None
    seed: Optional[Dict[str, Any]] = None
    max_concurrent: int = 1
    channels: Optional[List[str]] = None
    # Environment (environments/) and per-task money cap copied onto every
    # task this job creates; auto_pause_after (0 = off) is the run of
    # consecutive firing failures that pauses a recurring job on its own.
    environment_id: Optional[str] = None
    budget_usd: Optional[float] = None
    # Agent version pin (agents/versions.py) copied onto every task this job
    # creates; only meaningful together with agent_id (validated as such by
    # plans.service.create_job).
    agent_version: Optional[int] = None
    auto_pause_after: int = 3
    # agent_task only: the deployment's resources (plans.service._validate_resources).
    project_id: Optional[str] = None
    file_ids: Optional[List[str]] = None
    secrets: Optional[List[str]] = None
    memory_pool_ids: Optional[List[str]] = None
    memory_access: Optional[str] = None
    # memory_consolidate only: the pool to consolidate and how many recent
    # sessions to fold in (plans.service._validate_consolidate).
    consolidate_pool_id: Optional[str] = None
    consolidate_session_limit: Optional[int] = None

    @model_validator(mode="after")
    def _check_when(self):
        if self.run_at is None and self.delay_minutes is None:
            raise ValueError("Provide run_at or delay_minutes")
        if self.kind == JobKind.flow and not self.flow_id:
            raise ValueError("flow jobs require flow_id")
        if self.kind == JobKind.memory_consolidate and not self.consolidate_pool_id:
            raise ValueError("memory_consolidate jobs require consolidate_pool_id")
        return self

    def resolved_run_at(self) -> datetime:
        if self.run_at is not None:
            return self.run_at
        return datetime.now(timezone.utc) + timedelta(minutes=int(self.delay_minutes))


class JobUpdate(BaseModel):
    title: Optional[str] = None
    message: Optional[str] = None
    run_at: Optional[datetime] = None
    recurrence: Optional[Recurrence] = None
    cron: Optional[str] = None
    timezone: Optional[str] = None
    catch_up: Optional[bool] = None
    agent_id: Optional[str] = None
    channels: Optional[List[str]] = None
    environment_id: Optional[str] = None
    budget_usd: Optional[float] = None
    agent_version: Optional[int] = None
    auto_pause_after: Optional[int] = None
    project_id: Optional[str] = None
    file_ids: Optional[List[str]] = None
    secrets: Optional[List[str]] = None
    memory_pool_ids: Optional[List[str]] = None
    memory_access: Optional[str] = None
    consolidate_pool_id: Optional[str] = None
    consolidate_session_limit: Optional[int] = None


# -------------------- jobs --------------------

@router.get("/jobs")
async def list_jobs(
    workspace: Optional[str] = None,
    status: Optional[str] = None,
    kinds: Optional[str] = None,
):
    """``kinds`` is a comma list (e.g. ``agent_task,flow,loop``) so the
    Deployments page can list only the job kinds it treats as deployments,
    excluding plain ``notification`` reminders."""
    jobs = plan_service.list_jobs(workspace=workspace)
    if status:
        jobs = [j for j in jobs if j.status.value == status]
    if kinds:
        wanted = {k.strip() for k in kinds.split(",") if k.strip()}
        jobs = [j for j in jobs if j.kind.value in wanted]
    return [job_to_dict(j) for j in jobs]


@router.post("/jobs")
async def create_job(payload: JobCreate):
    try:
        job = plan_service.create_job(
            kind=payload.kind,
            title=payload.title,
            message=payload.message,
            run_at=payload.resolved_run_at(),
            recurrence=payload.recurrence,
            cron=payload.cron,
            timezone=payload.timezone,
            catch_up=payload.catch_up,
            workspace=payload.workspace,
            created_by="user",
            agent_id=payload.agent_id,
            flow_id=payload.flow_id,
            seed=payload.seed,
            max_concurrent=payload.max_concurrent,
            channels=payload.channels,
            environment_id=payload.environment_id,
            budget_usd=payload.budget_usd,
            agent_version=payload.agent_version,
            auto_pause_after=payload.auto_pause_after,
            project_id=payload.project_id,
            file_ids=payload.file_ids,
            secrets=payload.secrets,
            memory_pool_ids=payload.memory_pool_ids,
            memory_access=payload.memory_access,
            consolidate_pool_id=payload.consolidate_pool_id,
            consolidate_session_limit=payload.consolidate_session_limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return job_to_dict(job)


@router.get("/jobs/{job_id}")
async def get_job(job_id: UUID):
    job = plan_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_to_dict(job)


@router.patch("/jobs/{job_id}")
async def update_job(job_id: UUID, payload: JobUpdate):
    job = plan_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (JobStatus.scheduled, JobStatus.paused):
        raise HTTPException(status_code=400, detail=f"Cannot edit a job in status '{job.status.value}'")
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        updated = plan_service.update_job(job_id, **fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return job_to_dict(updated)


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: UUID):
    deleted = plan_service.delete_job(job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": str(job_id), "deleted": deleted}


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: UUID):
    job = plan_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    updated = plan_service.pause_job(job_id)
    if not updated:
        raise HTTPException(status_code=400, detail="Only scheduled jobs can be paused")
    return job_to_dict(updated)


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: UUID):
    job = plan_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    updated = plan_service.resume_job(job_id)
    if not updated:
        raise HTTPException(status_code=400, detail="Only paused jobs can be resumed")
    return job_to_dict(updated)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: UUID):
    job = plan_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (JobStatus.scheduled, JobStatus.paused):
        raise HTTPException(status_code=400, detail=f"Cannot cancel a job in status '{job.status.value}'")
    return job_to_dict(plan_service.cancel_job(job_id))


@router.post("/jobs/{job_id}/run-now")
async def run_job_now(job_id: UUID):
    """Fire a scheduled/paused job immediately, bypassing its run_at.

    Claims the job first (same lease as the automatic tick) so a manual fire
    can never race an in-flight automatic one on the same job. Allowed while
    paused (manually or auto-paused after errors), so an operator can retest
    a fix without resuming the job first; the resulting journal row is
    recorded with ``trigger="manual"``.
    """
    job = plan_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (JobStatus.scheduled, JobStatus.paused):
        raise HTTPException(status_code=400, detail=f"Cannot run a job in status '{job.status.value}'")
    claimed = plan_service.claim_job_now(job_id)
    if not claimed:
        raise HTTPException(status_code=409, detail="Job is currently being fired elsewhere; try again shortly")
    result = plan_service.fire_job(claimed, trigger="manual")
    updated = plan_service.get_job(job_id)
    return {"result": result, "job": job_to_dict(updated) if updated else None}


@router.get("/jobs/{job_id}/fires")
async def list_job_fires(job_id: UUID, limit: int = 50, only_errors: bool = False):
    """The firing journal for one job, newest first."""
    job = plan_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    items = plan_service.list_job_fires(job_id, only_errors=only_errors, limit=limit)
    return [fire_to_dict(f) for f in items]


@router.get("/fires")
async def list_fires(workspace: Optional[str] = None, only_errors: bool = False, limit: int = 100):
    """The firing journal across every job, newest first."""
    items = plan_service.list_fires(workspace=workspace, only_errors=only_errors, limit=limit)
    return [fire_to_dict(f) for f in items]


# -------------------- cron preview --------------------

@router.get("/cron/preview")
async def cron_preview(
    cron: str = "",
    timezone: Optional[str] = None,
    count: int = 5,
    start: Optional[datetime] = None,
    recurrence: Recurrence = Recurrence.cron,
):
    """Live preview for a schedule field: the next ``count`` fire times,
    computed with the exact same logic the scheduler itself uses to fire a job
    (``plans.service.upcoming_runs``, which wraps ``_next_run``), so this can
    never predict a time the scheduler would not actually fire at. ``cron``
    is read for ``recurrence=cron``; hourly, daily and weekly step from
    ``start``, the job's first run.

    Always 200, even for a bad expression: the caller is typing into a form
    and wants feedback as it goes, not an exception on every keystroke.
    """
    count = max(1, min(count, 20))
    if recurrence == Recurrence.cron and not cron.strip():
        return {"valid": False, "error": "Cron expression required.", "upcoming_runs_at": [], "description": None}
    try:
        runs = plan_service.upcoming_runs(cron, timezone, count, start=start, recurrence=recurrence)
    except ValueError as e:
        return {"valid": False, "error": str(e), "upcoming_runs_at": [], "description": None}
    return {
        "valid": True,
        "error": None,
        "upcoming_runs_at": runs,
        "description": plan_service.describe_cron(cron) if recurrence == Recurrence.cron else None,
    }


# -------------------- notifications --------------------

@router.get("/notifications")
async def list_notifications(
    workspace: Optional[str] = None,
    unread_only: bool = False,
    limit: int = 100,
):
    items = plan_service.list_notifications(workspace=workspace, unread_only=unread_only, limit=limit)
    return [notification_to_dict(n) for n in items]


@router.get("/notifications/unread-count")
async def notifications_unread_count(workspace: Optional[str] = None):
    return {"unread": plan_service.unread_count(workspace=workspace)}


# Live notification push now rides the single multiplexed `/api/stream`
# connection (channel `__notifications__`); the per-stream endpoint was removed.


@router.post("/notifications/publish")
async def publish_notification_event(payload: dict):
    """Internal relay: fan out a notification (already persisted by an agent
    subprocess) to live SSE subscribers. Does not write to the inbox."""
    from common.session_broker import broker
    await broker.apublish(NOTIFICATIONS_CHANNEL, {"type": "notification", "notification": payload})
    return {"published": True}


@router.post("/notifications/read-all")
async def mark_all_notifications_read(workspace: Optional[str] = None):
    return {"marked_read": plan_service.mark_all_notifications_read(workspace=workspace)}


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: UUID, read: bool = True):
    n = plan_service.mark_notification_read(notification_id, read=read)
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    return notification_to_dict(n)


@router.delete("/notifications/{notification_id}")
async def delete_notification(notification_id: UUID):
    deleted = plan_service.delete_notification(notification_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"notification_id": str(notification_id), "deleted": deleted}
