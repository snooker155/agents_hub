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
from plans.service import NOTIFICATIONS_CHANNEL, job_to_dict, notification_to_dict

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

    @model_validator(mode="after")
    def _check_when(self):
        if self.run_at is None and self.delay_minutes is None:
            raise ValueError("Provide run_at or delay_minutes")
        if self.kind == JobKind.flow and not self.flow_id:
            raise ValueError("flow jobs require flow_id")
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


# -------------------- jobs --------------------

@router.get("/jobs")
async def list_jobs(workspace: Optional[str] = None, status: Optional[str] = None):
    jobs = plan_service.list_jobs(workspace=workspace)
    if status:
        jobs = [j for j in jobs if j.status.value == status]
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
    can never race an in-flight automatic one on the same job.
    """
    job = plan_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (JobStatus.scheduled, JobStatus.paused):
        raise HTTPException(status_code=400, detail=f"Cannot run a job in status '{job.status.value}'")
    claimed = plan_service.claim_job_now(job_id)
    if not claimed:
        raise HTTPException(status_code=409, detail="Job is currently being fired elsewhere; try again shortly")
    result = plan_service.fire_job(claimed)
    updated = plan_service.get_job(job_id)
    return {"result": result, "job": job_to_dict(updated) if updated else None}


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
