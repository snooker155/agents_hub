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
import os
import socket
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from croniter import croniter

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


def _default_owner() -> str:
    """Identify this process for lease ownership: hostname:pid."""
    return f"{socket.gethostname()}:{os.getpid()}"


def _resolve_tz(name: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        # Bad/legacy data should not crash a tick; fall back to UTC.
        return ZoneInfo("UTC")


def _validate_timezone(tz: Optional[str]) -> str:
    """Validate an IANA timezone name, defaulting to UTC. Raises ValueError."""
    name = (tz or "UTC").strip() or "UTC"
    try:
        ZoneInfo(name)
    except Exception as e:
        raise ValueError(f"Unknown timezone '{name}'. Use an IANA name, e.g. 'Europe/Berlin'.") from e
    return name


def _validate_cron(expr: Optional[str]) -> str:
    """Validate a cron expression with croniter. Raises ValueError."""
    text = (expr or "").strip()
    if not text:
        raise ValueError("Recurrence 'cron' requires a cron expression, e.g. '0 9 * * 1-5'.")
    try:
        croniter(text)
    except Exception as e:
        raise ValueError(f"Invalid cron expression '{text}': {e}") from e
    return text


# -------------------- Job CRUD --------------------

def create_job(
    *,
    kind: JobKind,
    title: str,
    message: str = "",
    run_at: datetime,
    recurrence: Recurrence = Recurrence.none,
    cron: Optional[str] = None,
    timezone: Optional[str] = None,
    catch_up: bool = False,
    workspace: Optional[str] = None,
    created_by: str = "user",
    agent_id: Optional[str] = None,
    flow_id: Optional[str] = None,
    seed: Optional[Dict[str, Any]] = None,
    max_concurrent: int = 1,
    channels: Optional[List[str]] = None,
) -> ScheduledJob:
    tz_name = _validate_timezone(timezone)
    cron_expr = _validate_cron(cron) if recurrence == Recurrence.cron else None
    job = ScheduledJob(
        kind=kind,
        title=title,
        message=message,
        run_at=_ensure_aware(run_at),
        recurrence=recurrence,
        cron=cron_expr,
        timezone=tz_name,
        catch_up=catch_up,
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
    if "timezone" in fields:
        fields["timezone"] = _validate_timezone(fields["timezone"])
    if "cron" in fields and fields["cron"] is not None:
        fields["cron"] = _validate_cron(fields["cron"])
    if fields.get("recurrence") == Recurrence.cron:
        existing = plan_store.get(job_id)
        cron_val = fields.get("cron") or (existing.cron if existing else None)
        fields["cron"] = _validate_cron(cron_val)
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

    The inbox (dashboard bell) is always the source of truth. ``channels``
    picks which best-effort side channels also get it: ``"telegram"`` reaches
    the Telegram chats bound in this workspace; ``"slack"`` and ``"webhook"``
    reach every enabled endpoint of that kind (see ``notify.store``) whose
    events include ``"notification"``. Omit all three and the notification
    stays inbox-only.
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
    if channels and "slack" in channels:
        _push_endpoints(workspace, "slack", n)
    if channels and "webhook" in channels:
        _push_endpoints(workspace, "webhook", n)
    return n


def _push_telegram(workspace: Optional[str], title: str, body: str) -> None:
    """Best-effort proactive Telegram delivery; never raises."""
    try:
        from connectors.telegram.notify import notify_workspace
        notify_workspace(workspace, title, body)
    except Exception:
        log.debug("telegram notification delivery failed", exc_info=True)


def _push_endpoints(workspace: Optional[str], kind: str, n: Notification) -> None:
    """Best-effort delivery to every enabled ``kind`` endpoint subscribed to
    the ``"notification"`` event; never raises. Sibling of :func:`_push_telegram`
    for the endpoints configured on the Connectors page's Webhooks tab."""
    try:
        from notify import outbound as notify_outbound
        from notify import store as notify_store

        ws = workspace or "default"
        event = {
            "id": str(uuid4()),
            "type": "notification",
            "workspace": ws,
            "created_at": _now().isoformat(),
            "data": {
                "title": n.title,
                "body": n.body,
                "severity": n.severity,
                "source": n.source,
            },
        }
        for endpoint in notify_store.endpoints_for_event(ws, "notification"):
            if endpoint.get("kind") != kind:
                continue
            notify_outbound.dispatch(endpoint, event)
    except Exception:
        log.debug("%s notification delivery failed", kind, exc_info=True)


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
        import os
        import requests
        from common.auth import auth_headers
        port = os.environ.get("DASHBOARD_PORT", "8000")
        requests.post(
            f"http://localhost:{port}/api/plan/notifications/publish",
            json=payload,
            headers=auth_headers(),
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
    data = n.model_dump()
    data["id"] = str(data["id"])
    if data.get("created_at") is not None:
        try:
            data["created_at"] = data["created_at"].isoformat()
        except Exception:
            pass
    return data


def job_to_dict(job: ScheduledJob) -> Dict[str, Any]:
    data = job.model_dump()
    data["id"] = str(data["id"])
    for key in ("kind", "status", "recurrence"):
        if hasattr(data.get(key), "value"):
            data[key] = data[key].value
    for ts in ("run_at", "last_fired_at", "created_at", "updated_at", "lease_until", "last_fired_slot"):
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


def _next_run(job: ScheduledJob, now: datetime) -> datetime:
    """Compute the next run_at for a recurring job, past ``now``.

    ``job.catch_up`` is the fork in this function: it only matters when the
    slot just fired is more than one occurrence behind ``now`` (the backend
    was down through several ticks).

    ``catch_up`` False (the default, and the only behaviour before this field
    existed): cron follows the spec literally, giving the next cron occurrence
    after ``now``; hourly/daily/weekly step forward by their interval until
    past ``now``. Either way every missed occurrence in between is silently
    dropped and the job re-arms on the next slot that is still in the future.
    A single ``fire_job`` call only ever runs the job once regardless (see its
    docstring), so this is about how far the schedule jumps, not about
    replaying missed ticks.

    ``catch_up`` True: the next occurrence after the slot that *just* fired,
    full stop, even when that is still in the past. Such a job is due again
    immediately, so the scheduler's ordinary next tick claims and fires it
    right away, so the backend catches up one missed occurrence per tick instead
    of jumping straight to "now" and losing the rest.

    hourly/daily/weekly arithmetic happens on the job's local wall-clock time
    (via zoneinfo) rather than on the UTC instant, so a daily job stays at the
    same local hour across a DST change: the elapsed real time shifts by an
    hour instead of the local clock time drifting.
    """
    now = _ensure_aware(now)
    tz = _resolve_tz(job.timezone)

    if job.recurrence == Recurrence.cron:
        if not job.cron:
            raise ValueError(f"job {job.id} has recurrence=cron but no cron expression")
        base = job.run_at if job.catch_up else now
        base_in_tz = _ensure_aware(base).astimezone(tz)
        nxt = croniter(job.cron, base_in_tz).get_next(datetime)
        return _ensure_aware(nxt).astimezone(timezone.utc)

    deltas = {
        Recurrence.hourly: timedelta(hours=1),
        Recurrence.daily: timedelta(days=1),
        Recurrence.weekly: timedelta(weeks=1),
    }
    delta = deltas[job.recurrence]
    local = _ensure_aware(job.run_at).astimezone(tz)
    nxt = local + delta
    if job.catch_up:
        return nxt.astimezone(timezone.utc)
    # Roll forward past missed occurrences (e.g. backend was down).
    while nxt <= now.astimezone(tz):
        nxt += delta
    return nxt.astimezone(timezone.utc)


def fire_job(job: ScheduledJob) -> Dict[str, Any]:
    """Execute one due job and update its record. Returns a summary dict.

    ``fire_job`` only runs on a claimed job: if ``job`` was returned by
    ``claim_due_jobs`` / ``claim_job_now`` it already carries a lease and is
    used as is. Called directly with an unclaimed job (a tool, a route, a
    test), it claims the job itself first, so a caller never has to remember
    the two-step dance and two schedulers still can't race the same fire —
    whichever call wins the claim proceeds, the other gets back a "locked"
    error instead of a lease-less race.

    The job's ``run_at`` at call time is the "slot" it is firing for; if a
    previous attempt already recorded that slot as fired (it crashed after
    the side effect but before this bookkeeping wrote), the side effect is
    skipped and only the bookkeeping below is completed — so a job fires at
    most once per slot even across a crash and a retried lease.
    """
    if not job.lease_owner:
        claimed = plan_store.force_claim_job(job.id, _default_owner())
        if not claimed:
            return {
                "job_id": str(job.id), "kind": job.kind.value, "ok": False,
                "error": "job is locked by another firing, or is not in a fireable status",
            }
        job = claimed

    now = _now()
    slot = _ensure_aware(job.run_at)
    already_fired = job.last_fired_slot is not None and _ensure_aware(job.last_fired_slot) == slot
    result: Dict[str, Any] = {"job_id": str(job.id), "kind": job.kind.value}
    error: Optional[str] = None

    if already_fired:
        result["skipped"] = "already_fired_this_slot"
    else:
        # Record the slot as fired *before* running the side effect. If the
        # process crashes between the side effect and the completion write
        # below, this mark survives, so a retry after the lease expires takes
        # the already_fired branch above instead of firing a second time.
        plan_store.update(job.id, last_fired_slot=slot)
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
                result["task_id"] = _fire_flow(job)
            else:
                result["task_id"] = _fire_agent_task(job)
        except Exception as e:
            error = str(e)

    fields: Dict[str, Any] = {
        "lease_until": None,
        "lease_owner": None,
        "fire_count": job.fire_count + 1,
        "last_fired_at": now,
        "last_error": error,
    }
    if job.kind in (JobKind.agent_task, JobKind.flow) and result.get("task_id"):
        fields["created_task_ids"] = [*job.created_task_ids, result["task_id"]]
    if job.recurrence == Recurrence.none:
        fields["status"] = JobStatus.failed if error else JobStatus.fired
    else:
        try:
            fields["run_at"] = _next_run(job, now)
        except Exception as e:
            # Bad cron/timezone data should not wedge the tick; keep the job
            # alive and try again shortly rather than crashing the scheduler.
            log.error("failed computing next run for job %s: %s", job.id, e)
            fields["run_at"] = now + timedelta(hours=1)
    plan_store.update(job.id, **fields)
    result["ok"] = error is None
    if error:
        result["error"] = error
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


def claim_due_jobs(
    now: Optional[datetime] = None,
    owner: Optional[str] = None,
    lease_seconds: float = 120.0,
) -> List[ScheduledJob]:
    """Atomically claim due jobs with no live lease (see PlanStore.claim_due_jobs).

    Two schedulers racing this on the same store — two backend replicas, or a
    tick that overlaps a slow previous fire — each get a disjoint set back,
    so a job is handed to at most one of them.
    """
    now = now or _now()
    owner = owner or _default_owner()
    return plan_store.claim_due_jobs(now, owner, lease_seconds=lease_seconds)


def claim_job_now(
    job_id: UUID | str,
    owner: Optional[str] = None,
    lease_seconds: float = 120.0,
) -> Optional[ScheduledJob]:
    """Claim one specific job for an immediate manual fire (the run-now action)."""
    owner = owner or _default_owner()
    return plan_store.force_claim_job(job_id, owner, lease_seconds=lease_seconds)


def run_due_jobs(owner: Optional[str] = None) -> List[Dict[str, Any]]:
    """Claim and fire every due job once. Called by the scheduler loop each tick."""
    results = []
    for job in claim_due_jobs(owner=owner):
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
    "claim_due_jobs",
    "claim_job_now",
    "fire_job",
    "run_due_jobs",
]
