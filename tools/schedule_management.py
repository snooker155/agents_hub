"""
Schedule management tools.

Let agents schedule future user notifications and future agent tasks (Plan
page). Jobs are written to the shared plans store; the backend scheduler
fires them when due.

Named schedule_* (not plan_*) to avoid colliding with the reasoning layer's
auto-injected plan-store tools (save_plan / get_plan / list_plans / ...).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator

from common.entity_sink import record_entity
from common.workspace_context import resolve_active_workspace
from plans.models import JobKind, JobStatus, Recurrence
from plans import service as plan_service
from plans.service import job_to_dict
from tools._json import json_err as _json_err, json_ok as _json_ok


# -------------------- helpers --------------------

def _record_job(job, action: str) -> None:
    """Report a scheduled job this run touched, so the reply can link to it."""
    if job is None:
        return
    record_entity("job", str(job.id), action, (job.title or "").strip())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid_from_str(value: str) -> UUID:
    s = str(value).strip()
    try:
        return UUID(s)
    except Exception:
        hex_only = s.replace("-", "").replace(" ", "")
        if len(hex_only) == 32 and all(c in "0123456789abcdefABCDEF" for c in hex_only):
            return UUID(f"{hex_only[:8]}-{hex_only[8:12]}-{hex_only[12:16]}-{hex_only[16:20]}-{hex_only[20:]}")
        raise ValueError(
            f"Invalid job ID '{s}'. Use the exact job ID from list_scheduled without modification."
        )


def _resolve_run_at(
    run_at: Optional[str],
    delay_minutes: Optional[int],
) -> datetime:
    """Resolve the firing time from an ISO string or a relative delay."""
    if run_at:
        dt = datetime.fromisoformat(run_at.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    if delay_minutes is not None:
        return _now() + timedelta(minutes=int(delay_minutes))
    raise ValueError("Provide run_at (ISO datetime) or delay_minutes")


_TIME_HELP = (
    "Timing: pass either `run_at` (ISO 8601, e.g. '2026-06-13T09:00:00+00:00'; "
    "naive values are treated as UTC) or `delay_minutes` (relative to now). "
    "Prefer delay_minutes for relative requests like 'in 2 hours' (=120). "
    "The response includes `current_time` and the resolved `run_at` — verify it "
    "matches the user's intent. If you need the current date/time first, call "
    "list_scheduled: its response also includes `current_time`."
)


# -------------------- Tool schemas --------------------

class ScheduleNotificationInput(BaseModel):
    title: str = Field(..., min_length=1, description="Short notification title shown to the user")
    message: str = Field("", description="Notification body text")
    run_at: Optional[str] = Field(None, description="ISO 8601 datetime when to notify (UTC if no offset)")
    delay_minutes: Optional[int] = Field(None, ge=1, description="Alternative to run_at: minutes from now")
    recurrence: Recurrence = Field(Recurrence.none, description="none, hourly, daily, or weekly")
    telegram: bool = Field(
        False,
        description="When it fires, also deliver to the user's bound Telegram chat(s). Default false = inbox/bell only.",
    )

    @model_validator(mode="after")
    def _check_when(self):
        if not self.run_at and self.delay_minutes is None:
            raise ValueError("Provide run_at or delay_minutes")
        return self


class ScheduleTaskInput(BaseModel):
    title: str = Field(..., min_length=1, description="Title of the task to create when the time comes")
    description: str = Field("", description="Detailed description of the work the agent should do")
    run_at: Optional[str] = Field(None, description="ISO 8601 datetime when to start the task (UTC if no offset)")
    delay_minutes: Optional[int] = Field(None, ge=1, description="Alternative to run_at: minutes from now")
    recurrence: Recurrence = Field(Recurrence.none, description="none, hourly, daily, weekly, or cron")
    cron: Optional[str] = Field(
        None,
        description="Cron expression, required when recurrence='cron' (e.g. '0 9 * * 1-5' for weekdays at 9am)",
    )
    timezone: Optional[str] = Field(
        None,
        description="IANA timezone name for recurrence timing, e.g. 'Europe/Berlin'. Defaults to UTC.",
    )
    agent_id: Optional[str] = Field(
        None,
        description="Optional agent to run the task. Omit to let the orchestrator pick one at fire time.",
    )
    telegram: bool = Field(
        False,
        description="Also send the 'task started' notification to the user's bound Telegram chat(s). Default false = inbox/bell only.",
    )

    @model_validator(mode="after")
    def _check_when(self):
        if not self.run_at and self.delay_minutes is None:
            raise ValueError("Provide run_at or delay_minutes")
        return self


class NotifyUserInput(BaseModel):
    title: str = Field(..., min_length=1, description="Short notification title shown to the user")
    message: str = Field("", description="Notification body text")
    severity: str = Field("info", description="One of: info, success, warning, error")
    telegram: bool = Field(
        False,
        description="Also deliver to the user's bound Telegram chat(s). Default false = inbox/bell only.",
    )

    @model_validator(mode="after")
    def _check_severity(self):
        if self.severity not in ("info", "success", "warning", "error"):
            raise ValueError("severity must be one of: info, success, warning, error")
        return self


class ListScheduledInput(BaseModel):
    include_finished: bool = Field(
        False, description="Also include fired/cancelled/failed jobs (default: only scheduled and paused)"
    )


class JobIdInput(BaseModel):
    id: str = Field(..., description="UUID of the scheduled job")


class UpdateScheduledInput(BaseModel):
    id: str = Field(..., description="UUID of the scheduled job to update")
    title: Optional[str] = None
    message: Optional[str] = Field(None, description="New notification body / task description")
    run_at: Optional[str] = Field(None, description="New ISO 8601 firing time")
    delay_minutes: Optional[int] = Field(None, ge=1, description="Alternative to run_at: minutes from now")
    recurrence: Optional[Recurrence] = None
    cron: Optional[str] = Field(None, description="New cron expression, required when recurrence='cron'")
    timezone: Optional[str] = Field(None, description="New IANA timezone name, e.g. 'Europe/Berlin'")
    agent_id: Optional[str] = Field(None, description="New agent for agent_task jobs; empty string clears it")


# -------------------- Tools --------------------

@tool("schedule_notification", args_schema=ScheduleNotificationInput)
def schedule_notification(
    title: str,
    message: str = "",
    run_at: Optional[str] = None,
    delay_minutes: Optional[int] = None,
    recurrence: Recurrence = Recurrence.none,
    telegram: bool = False,
) -> str:
    """Schedule a future notification (reminder) for the user.

    At the scheduled time the user gets an entry in their notification inbox
    (and the dashboard bell). Use for reminders like "remind me tomorrow to
    review the report". Set telegram=true to also deliver to the user's bound
    Telegram chat(s) when it fires. Returns JSON with the created job.
    """
    try:
        when = _resolve_run_at(run_at, delay_minutes)
        job = plan_service.create_job(
            kind=JobKind.notification,
            title=title,
            message=message,
            run_at=when,
            recurrence=recurrence,
            workspace=resolve_active_workspace(),
            created_by="agent",
            channels=["dashboard", "telegram"] if telegram else ["dashboard"],
        )
        _record_job(job, "scheduled")
        return _json_ok({
            "message": "Notification scheduled. Confirm the time to the user.",
            "current_time": _now().isoformat(),
            "job": job_to_dict(job),
        })
    except Exception as e:
        return _json_err(f"Failed to schedule notification: {e}")


schedule_notification.description += "\n\n" + _TIME_HELP


@tool("schedule_task", args_schema=ScheduleTaskInput)
def schedule_task(
    title: str,
    description: str = "",
    run_at: Optional[str] = None,
    delay_minutes: Optional[int] = None,
    recurrence: Recurrence = Recurrence.none,
    cron: Optional[str] = None,
    timezone: Optional[str] = None,
    agent_id: Optional[str] = None,
    telegram: bool = False,
) -> str:
    """Schedule agent work for the future.

    At the scheduled time a regular task is created and started: if agent_id
    is set, that agent runs it; otherwise the orchestrator routes it. The user
    is notified when the work starts. Use for requests like "every morning
    generate a summary" or "in 3 hours run the tests again". For recurrence
    'cron', pass a cron expression; timezone (IANA name, default UTC) applies
    to cron and keeps hourly/daily/weekly at the same local time across a DST
    change. Set telegram=true to also send that start notification to the
    user's bound Telegram chat(s). Returns JSON with the created job.
    """
    try:
        if agent_id:
            from agents.registry import get_agent as reg_get_agent
            if not reg_get_agent(agent_id):
                return _json_err(f"Agent '{agent_id}' not found", code="not_found")
        when = _resolve_run_at(run_at, delay_minutes)
        job = plan_service.create_job(
            kind=JobKind.agent_task,
            title=title,
            message=description,
            run_at=when,
            recurrence=recurrence,
            cron=cron,
            timezone=timezone,
            workspace=resolve_active_workspace(),
            created_by="agent",
            agent_id=agent_id or None,
            channels=["dashboard", "telegram"] if telegram else ["dashboard"],
        )
        _record_job(job, "scheduled")
        return _json_ok({
            "message": "Task scheduled. Confirm the time and agent to the user.",
            "current_time": _now().isoformat(),
            "job": job_to_dict(job),
        })
    except Exception as e:
        return _json_err(f"Failed to schedule task: {e}")


schedule_task.description += "\n\n" + _TIME_HELP


@tool("notify_user", args_schema=NotifyUserInput)
def notify_user(title: str, message: str = "", severity: str = "info", telegram: bool = False) -> str:
    """Send the user an immediate notification (inbox + dashboard bell), right now.

    Use this when something happens that the user should see even when they are
    not watching this conversation — e.g. you discovered a critical problem,
    finished a long piece of work with a noteworthy outcome, or are blocked and
    need their input. Do NOT use it for routine progress updates: run results
    are already reported to the user automatically.

    Set telegram=true to also push the message to the user's bound Telegram
    chat(s) for this workspace; otherwise it is inbox/bell only.
    """
    try:
        channels = ["dashboard", "telegram"] if telegram else ["dashboard"]
        n = plan_service.create_notification(
            title=title,
            body=message,
            severity=severity,
            source={"origin": "agent"},
            workspace=resolve_active_workspace(),
            channels=channels,
        )
        return _json_ok({
            "message": "Notification delivered to the user.",
            "notification_id": str(n.id),
            "channels": channels,
        })
    except Exception as e:
        return _json_err(f"Failed to send notification: {e}")


@tool("list_scheduled", args_schema=ListScheduledInput)
def list_scheduled(include_finished: bool = False) -> str:
    """List scheduled jobs (future notifications and agent tasks) in the active workspace.

    By default returns only pending jobs (scheduled/paused). The response
    includes `current_time` (UTC) — also call this when you need the current
    date/time to compute a run_at for scheduling.
    """
    try:
        ws = resolve_active_workspace()
        jobs = plan_service.list_jobs(workspace=ws)
        if not include_finished:
            jobs = [j for j in jobs if j.status in (JobStatus.scheduled, JobStatus.paused)]
        return _json_ok({
            "current_time": _now().isoformat(),
            "workspace": ws or "default",
            "count": len(jobs),
            "jobs": [job_to_dict(j) for j in jobs],
        })
    except Exception as e:
        return _json_err(f"Failed to list scheduled jobs: {e}")


@tool("cancel_scheduled", args_schema=JobIdInput)
def cancel_scheduled(id: str) -> str:
    """Cancel a scheduled job so it never fires. Returns JSON with the cancelled job."""
    try:
        jid = _uuid_from_str(id)
        job = plan_service.get_job(jid)
        if not job:
            return _json_err("Job not found", code="not_found", extra={"id": id})
        ws = resolve_active_workspace()
        if ws and (job.workspace or "").strip() not in ("", ws):
            return _json_err(f"Job '{id}' is outside the active workspace '{ws}'", code="forbidden")
        if job.status not in (JobStatus.scheduled, JobStatus.paused):
            return _json_err(f"Cannot cancel a job in status '{job.status.value}'", code="invalid_state")
        updated = plan_service.cancel_job(jid)
        _record_job(updated, "cancelled")
        return _json_ok({"job": job_to_dict(updated)})
    except Exception as e:
        return _json_err(f"Failed to cancel job: {e}")


@tool("update_scheduled", args_schema=UpdateScheduledInput)
def update_scheduled(
    id: str,
    title: Optional[str] = None,
    message: Optional[str] = None,
    run_at: Optional[str] = None,
    delay_minutes: Optional[int] = None,
    recurrence: Optional[Recurrence] = None,
    cron: Optional[str] = None,
    timezone: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> str:
    """Update a pending scheduled job (time, title, message, recurrence, cron,
    timezone, or agent).

    Only scheduled/paused jobs can be edited. Returns JSON with the updated job.
    """
    try:
        jid = _uuid_from_str(id)
        job = plan_service.get_job(jid)
        if not job:
            return _json_err("Job not found", code="not_found", extra={"id": id})
        ws = resolve_active_workspace()
        if ws and (job.workspace or "").strip() not in ("", ws):
            return _json_err(f"Job '{id}' is outside the active workspace '{ws}'", code="forbidden")
        if job.status not in (JobStatus.scheduled, JobStatus.paused):
            return _json_err(f"Cannot edit a job in status '{job.status.value}'", code="invalid_state")

        fields: Dict[str, Any] = {}
        if title is not None:
            fields["title"] = title
        if message is not None:
            fields["message"] = message
        if run_at or delay_minutes is not None:
            fields["run_at"] = _resolve_run_at(run_at, delay_minutes)
        if recurrence is not None:
            fields["recurrence"] = recurrence
        if cron is not None:
            fields["cron"] = cron
        if timezone is not None:
            fields["timezone"] = timezone
        if agent_id is not None:
            if agent_id.strip():
                from agents.registry import get_agent as reg_get_agent
                if not reg_get_agent(agent_id.strip()):
                    return _json_err(f"Agent '{agent_id}' not found", code="not_found")
                fields["agent_id"] = agent_id.strip()
            else:
                fields["agent_id"] = None
        if not fields:
            return _json_err("No fields to update provided")

        updated = plan_service.update_job(jid, **fields)
        _record_job(updated, "updated")
        return _json_ok({
            "current_time": _now().isoformat(),
            "job": job_to_dict(updated),
        })
    except Exception as e:
        return _json_err(f"Failed to update job: {e}")


update_scheduled.description += "\n\n" + _TIME_HELP


__all__ = [
    "schedule_notification",
    "schedule_task",
    "notify_user",
    "list_scheduled",
    "cancel_scheduled",
    "update_scheduled",
]
