"""
Plan service: CRUD for scheduled jobs, the notification inbox, and the
fire logic executed when a job comes due.

Firing an agent_task job materializes a regular Task and starts it through
the same machinery the manual assign endpoint uses, so execution honors the
workspace's execution_mode (subprocess vs node) and stays observable via the
normal run/session records.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from .models import JobKind, JobStatus, Notification, Recurrence, ScheduledJob
from .storage import NotificationStore, PlanStore

log = logging.getLogger("plans.service")

plan_store = PlanStore()
notification_store = NotificationStore()

# Reserved session-broker channel for real-time notification push (SSE).
NOTIFICATIONS_CHANNEL = "__notifications__"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# -------------------- Job CRUD --------------------

def create_job(
    *,
    kind: JobKind,
    title: str,
    message: str = "",
    run_at: datetime,
    recurrence: Recurrence = Recurrence.none,
    workspace: Optional[str] = None,
    created_by: str = "user",
    agent_id: Optional[str] = None,
    flow_id: Optional[str] = None,
    seed: Optional[Dict[str, Any]] = None,
    max_concurrent: int = 1,
    channels: Optional[List[str]] = None,
) -> ScheduledJob:
    job = ScheduledJob(
        kind=kind,
        title=title,
        message=message,
        run_at=_ensure_aware(run_at),
        recurrence=recurrence,
        workspace=workspace,
        created_by=created_by,
        agent_id=agent_id,
        flow_id=flow_id,
        seed=seed,
        max_concurrent=max_concurrent,
        channels=channels or ["dashboard"],
    )
    saved = plan_store.add(job)
    _notify_plan_changed()
    return saved


def get_job(job_id: UUID | str) -> Optional[ScheduledJob]:
    return plan_store.get(job_id)


def list_jobs(workspace: Optional[str] = None) -> List[ScheduledJob]:
    jobs = plan_store.list()
    if workspace:
        jobs = [j for j in jobs if (j.workspace or "").strip() == workspace]
    return sorted(jobs, key=lambda j: _ensure_aware(j.run_at))


def _notify_plan_changed() -> None:
    try:
        from common.session_broker import notify_change
        notify_change("plan")
    except Exception:
        pass


def update_job(job_id: UUID | str, **fields) -> Optional[ScheduledJob]:
    if "run_at" in fields and isinstance(fields["run_at"], datetime):
        fields["run_at"] = _ensure_aware(fields["run_at"])
    updated = plan_store.update(job_id, **fields)
    if updated:
        _notify_plan_changed()
    return updated


def delete_job(job_id: UUID | str) -> int:
    deleted = plan_store.delete(job_id)
    if deleted:
        _notify_plan_changed()
    return deleted


def cancel_job(job_id: UUID | str) -> Optional[ScheduledJob]:
    updated = plan_store.update(job_id, status=JobStatus.cancelled)
    if updated:
        _notify_plan_changed()
    return updated


def pause_job(job_id: UUID | str) -> Optional[ScheduledJob]:
    job = plan_store.get(job_id)
    if job and job.status != JobStatus.scheduled:
        return None
    updated = plan_store.update(job_id, status=JobStatus.paused)
    if updated:
        _notify_plan_changed()
    return updated


def resume_job(job_id: UUID | str) -> Optional[ScheduledJob]:
    job = plan_store.get(job_id)
    if job and job.status != JobStatus.paused:
        return None
    updated = plan_store.update(job_id, status=JobStatus.scheduled)
    if updated:
        _notify_plan_changed()
    return updated


# -------------------- Notifications --------------------

def create_notification(
    *,
    title: str,
    body: str = "",
    severity: str = "info",
    source: Optional[Dict[str, Any]] = None,
    workspace: Optional[str] = None,
    channels: Optional[List[str]] = None,
) -> Notification:
    """Persist an inbox entry and push it to live SSE subscribers.

    The inbox (dashboard bell) is always the source of truth. When ``channels``
    includes ``"telegram"``, the same notification is *also* pushed to the
    Telegram chats bound in this workspace (best-effort). Omit telegram and the
    notification stays inbox-only.
    """
    n = Notification(
        title=title,
        body=body,
        severity=severity,
        source=source or {},
        workspace=workspace,
    )
    notification_store.add(n)
    _publish_notification(n)
    if channels and "telegram" in channels:
        _push_telegram(workspace, title, body)
    return n


def _push_telegram(workspace: Optional[str], title: str, body: str) -> None:
    """Best-effort proactive Telegram delivery; never raises."""
    try:
        from connectors.telegram.notify import notify_workspace
        notify_workspace(workspace, title, body)
    except Exception:
        log.debug("telegram notification delivery failed", exc_info=True)


def _publish_notification(n: Notification) -> None:
    """Best-effort real-time push; inbox persistence is the source of truth.

    In the backend process the broker has a running event loop, so we publish
    directly. In agent subprocesses there is no loop — relay the event to the
    backend over HTTP instead (same pattern as SessionPublishCallback).
    """
    payload = notification_to_dict(n)
    try:
        from common.session_broker import broker
        loop = getattr(broker, "_loop", None)
        if loop is not None and loop.is_running():
            broker.publish_threadsafe(
                NOTIFICATIONS_CHANNEL, {"type": "notification", "notification": payload}
            )
            return
    except Exception:
        pass
    try:
        import requests
        requests.post(
            "http://localhost:8000/api/plan/notifications/publish",
            json=payload,
            timeout=2,
        )
    except Exception:
        pass


def list_notifications(
    workspace: Optional[str] = None,
    unread_only: bool = False,
    limit: int = 100,
) -> List[Notification]:
    items = notification_store.list()
    if workspace:
        items = [n for n in items if (n.workspace or "").strip() == workspace]
    if unread_only:
        items = [n for n in items if not n.read]
    items.sort(key=lambda n: _ensure_aware(n.created_at), reverse=True)
    return items[: max(0, limit)]


def unread_count(workspace: Optional[str] = None) -> int:
    return len(list_notifications(workspace=workspace, unread_only=True, limit=notification_store.MAX_ENTRIES))


def mark_notification_read(notification_id: UUID | str, read: bool = True) -> Optional[Notification]:
    return notification_store.update(notification_id, read=read)


def mark_all_notifications_read(workspace: Optional[str] = None) -> int:
    return notification_store.mark_all_read(workspace=workspace)


def delete_notification(notification_id: UUID | str) -> int:
    return notification_store.delete(notification_id)


def notification_to_dict(n: Notification) -> Dict[str, Any]:
    data = n.model_dump() if hasattr(n, "model_dump") else n.dict()
    data["id"] = str(data["id"])
    if data.get("created_at") is not None:
        try:
            data["created_at"] = data["created_at"].isoformat()
        except Exception:
            pass
    return data


def job_to_dict(job: ScheduledJob) -> Dict[str, Any]:
    data = job.model_dump() if hasattr(job, "model_dump") else job.dict()
    data["id"] = str(data["id"])
    for key in ("kind", "status", "recurrence"):
        if hasattr(data.get(key), "value"):
            data[key] = data[key].value
    for ts in ("run_at", "last_fired_at", "created_at", "updated_at"):
        if data.get(ts) is not None:
            try:
                data[ts] = data[ts].isoformat()
            except Exception:
                pass
    return data


# -------------------- Firing --------------------

def due_jobs(now: Optional[datetime] = None) -> List[ScheduledJob]:
    now = now or _now()
    return [
        j for j in plan_store.list()
        if j.status == JobStatus.scheduled and _ensure_aware(j.run_at) <= now
    ]


def _next_run(run_at: datetime, recurrence: Recurrence, now: datetime) -> datetime:
    deltas = {
        Recurrence.hourly: timedelta(hours=1),
        Recurrence.daily: timedelta(days=1),
        Recurrence.weekly: timedelta(weeks=1),
    }
    delta = deltas[recurrence]
    nxt = _ensure_aware(run_at) + delta
    # Roll forward past missed occurrences (e.g. backend was down).
    while nxt <= now:
        nxt += delta
    return nxt


def fire_job(job: ScheduledJob) -> Dict[str, Any]:
    """Execute one due job and update its record. Returns a summary dict."""
    now = _now()
    result: Dict[str, Any] = {"job_id": str(job.id), "kind": job.kind.value}
    try:
        if job.kind == JobKind.notification:
            n = create_notification(
                title=job.title,
                body=job.message,
                source={"job_id": str(job.id)},
                workspace=job.workspace,
                channels=job.channels,
            )
            result["notification_id"] = str(n.id)
        elif job.kind == JobKind.flow:
            task_id = _fire_flow(job)
            result["task_id"] = task_id
        else:
            task_id = _fire_agent_task(job)
            result["task_id"] = task_id

        fields: Dict[str, Any] = {"last_fired_at": now, "last_error": None}
        if job.kind in (JobKind.agent_task, JobKind.flow) and result.get("task_id"):
            fields["created_task_ids"] = [*job.created_task_ids, result["task_id"]]
        if job.recurrence == Recurrence.none:
            fields["status"] = JobStatus.fired
        else:
            fields["run_at"] = _next_run(job.run_at, job.recurrence, now)
        plan_store.update(job.id, **fields)
        result["ok"] = True
    except Exception as e:
        # Recurring jobs keep going next period; one-shots are marked failed.
        fields = {"last_error": str(e), "last_fired_at": now}
        if job.recurrence == Recurrence.none:
            fields["status"] = JobStatus.failed
        else:
            fields["run_at"] = _next_run(job.run_at, job.recurrence, now)
        plan_store.update(job.id, **fields)
        result["ok"] = False
        result["error"] = str(e)
    return result


def _fire_agent_task(job: ScheduledJob) -> str:
    """Materialize a Task for the job and start it. Returns the task id.

    Mirrors the manual assign flow in dashboard/backend/routes/tasks.py:
    - preassigned agent + node mode: write the "assigned" run record while the
      task is still `todo`, then flip to `ready` — the orchestrator poller only
      grabs `ready` tasks with no agent, so it never sees this one unassigned.
    - preassigned agent + subprocess mode: launch the run directly.
    - no agent: set the task `ready` and let the orchestrator node route it.
    """
    from tasks import service as tasks_service
    from tasks.models import CreatedBy, TaskStatus
    from workspace import create_workspace_folder, get_workspace_metadata

    ws_name = None
    if job.workspace:
        ws_name = create_workspace_folder(job.workspace).name

    task = tasks_service.create_task(
        title=job.title,
        description=job.message or job.title,
        created_by=CreatedBy.user,
        status=TaskStatus.todo,
        workspace=ws_name,
    )
    tasks_service.append_task_activity_log(
        task.id, "scheduled_fire", f"Created by scheduled job {job.id}", job_id=str(job.id)
    )

    if job.agent_id:
        execution_mode = (
            get_workspace_metadata(ws_name or "default")
            .get("orchestrator", {})
            .get("execution_mode", "subprocess")
        )
        if execution_mode == "node":
            from managers import run_manager
            from common.session_service import get_or_create_task_session

            session_id = None
            try:
                session_id = get_or_create_task_session(
                    title=task.title, workspace=ws_name, task_id=str(task.id)
                )
            except Exception:
                pass
            run_id = str(uuid4())
            run_manager.upsert_run({
                "run_id": run_id,
                "task_id": str(task.id),
                "agent_id": job.agent_id,
                "status": "assigned",
                "session_type": "task",
                "session_id": session_id,
                "created_at": run_manager.utc_now_iso(),
                "started_at": None,
                "finished_at": None,
                "pid": None,
                "exit_code": None,
                "error": None,
            })
            tasks_service.assign_agent(task.id, job.agent_id, None, run_id=run_id)
            tasks_service.update_task(task.id, status=TaskStatus.ready, session_id=session_id)
        else:
            from agents import agent_launcher

            run_id, session_id = agent_launcher.start_run(str(task.id), job.agent_id, None)
            tasks_service.assign_agent(task.id, job.agent_id, None, run_id=run_id)
            tasks_service.update_task(task.id, status=TaskStatus.in_progress, session_id=session_id)
    else:
        # Unassigned: orchestrator nodes pick up ready user tasks with no agent.
        tasks_service.update_task(task.id, status=TaskStatus.ready)

    # Surface the firing in the inbox so the user sees work has started.
    create_notification(
        title=f"Scheduled task started: {job.title}",
        body=(
            f"Agent '{job.agent_id}' was started on the task."
            if job.agent_id
            else "The task was handed to the orchestrator for routing."
        ),
        source={"job_id": str(job.id), "task_id": str(task.id)},
        workspace=job.workspace,
        channels=job.channels,
    )
    return str(task.id)


def _fire_flow(job: ScheduledJob) -> str:
    """Trigger a flow run for a scheduled ``flow`` job. Returns the task id.

    Reuses ``flow.launcher.trigger_flow`` so scheduled and webhook triggers share
    concurrency + authorization semantics. A concurrency-capped skip raises, which
    ``fire_job`` records as ``last_error`` (the recurring job simply tries again
    next period) rather than crashing the scheduler.
    """
    if not job.flow_id:
        raise ValueError("flow job has no flow_id")

    from flow import launcher as flow_launcher

    result = flow_launcher.trigger_flow(
        job.flow_id,
        workspace=job.workspace,
        description=job.message or job.title,
        seed=job.seed,
        max_concurrent=job.max_concurrent,
        created_by="schedule",
    )
    create_notification(
        title=f"Scheduled flow started: {job.title}",
        body=f"Flow '{job.flow_id}' was triggered by scheduled job {job.id}.",
        source={"job_id": str(job.id), "task_id": result["task_id"], "flow_id": job.flow_id},
        workspace=job.workspace,
        channels=job.channels,
    )
    return result["task_id"]


def run_due_jobs() -> List[Dict[str, Any]]:
    """Fire every due job once. Called by the scheduler loop each tick."""
    results = []
    for job in due_jobs():
        results.append(fire_job(job))
    return results


__all__ = [
    "NOTIFICATIONS_CHANNEL",
    "plan_store",
    "notification_store",
    "create_job",
    "get_job",
    "list_jobs",
    "update_job",
    "delete_job",
    "cancel_job",
    "pause_job",
    "resume_job",
    "create_notification",
    "list_notifications",
    "unread_count",
    "mark_notification_read",
    "mark_all_notifications_read",
    "delete_notification",
    "notification_to_dict",
    "job_to_dict",
    "due_jobs",
    "fire_job",
    "run_due_jobs",
]
