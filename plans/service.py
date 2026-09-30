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
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from croniter import croniter

from .models import FireRecord, JobKind, JobStatus, Notification, Recurrence, ScheduledJob
from .storage import FireStore, NotificationStore, PlanStore

log = logging.getLogger("plans.service")

plan_store = PlanStore()
fire_store = FireStore()
notification_store = NotificationStore()

# Firing failure types that never self-heal: the target this job points at
# is simply gone, so a recurring job pauses on the first one instead of
# waiting out its normal auto_pause_after counter.
_TARGET_MISSING_ERROR_TYPES = frozenset({"agent_missing", "flow_missing", "loop_missing"})

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


def _validate_agent_version(agent_version: Optional[int], agent_id: Optional[str]) -> None:
    """When set, ``agent_version`` must be a real stored version of
    ``agent_id`` (agents/versions.py). Raises ``ValueError`` (a 400 at the
    route) rather than accepting a pin that would fall back to the live
    definition, silently, on every firing.
    """
    if agent_version is None:
        return
    if not agent_id:
        raise ValueError("agent_version requires an agent_id (a flow/loop job has no single agent to pin)")
    from tasks.service import validate_agent_version
    validate_agent_version(agent_id, agent_version)


def _validate_environment_id(environment_id: Optional[str], workspace: Optional[str]) -> None:
    """When set, the environment must exist, be usable from ``workspace`` (its
    own or global) and not be archived — the same check
    ``environments.launch.launch_fields`` applies at fire time. Raises
    ``ValueError`` (a 400 at the route) rather than silently accepting an id
    that would resolve to nothing once the job actually fires.

    Guarded by ``ImportError`` so a checkout without the ``environments``
    package (part C) still runs plans unchanged; an id set in that case is
    simply not checked, same as before this validation existed.
    """
    if not environment_id:
        return
    try:
        from environments.service import EnvironmentServiceError, resolve_for
    except ImportError:
        return
    try:
        resolve_for(workspace, environment_id)
    except EnvironmentServiceError as e:
        raise ValueError(str(e)) from e


# -------------------- Job CRUD --------------------

_RESOURCE_FIELDS = ("project_id", "file_ids", "secrets", "memory_pool_ids", "memory_access")


def _validate_resources(kind: JobKind, workspace: Optional[str], agent_id: Optional[str], *,
                        project_id: Optional[str] = None, file_ids: Optional[List[str]] = None,
                        secrets: Optional[List[str]] = None,
                        memory_pool_ids: Optional[List[str]] = None,
                        memory_access: Optional[str] = None) -> Dict[str, Any]:
    """The deployment's resources, cleaned, or ``ValueError``.

    Only an ``agent_task`` job creates a task of its own, so only it can carry
    resources. Each id must exist in the job's workspace: a project of that
    workspace, a live workspace file, a shared memory pool of that workspace
    (or a global one). Secret names are checked for shape; whether a value
    exists is decided at run time, like the agent's own allowlist. Extra
    secrets widen what the agent can read, so the capability guard is asked
    the same question the agent editor asks: with these names on top of its
    tools, does the agent form a blocked combination.
    """
    out: Dict[str, Any] = {
        "project_id": (str(project_id).strip() or None) if project_id else None,
        "file_ids": [str(f).strip() for f in (file_ids or []) if str(f or "").strip()],
        "secrets": [],
        "memory_pool_ids": [str(m).strip() for m in (memory_pool_ids or []) if str(m or "").strip()],
        "memory_access": (str(memory_access or "read").strip().lower() or "read"),
    }
    from memory.binding import MEMORY_ACCESS_MODES
    if out["memory_access"] not in MEMORY_ACCESS_MODES:
        raise ValueError(f"memory_access must be one of {', '.join(MEMORY_ACCESS_MODES)}")
    for name in (secrets or []):
        n = str(name or "").strip()
        if not n:
            continue
        from common.secrets import validate_name
        validate_name(n)
        if n not in out["secrets"]:
            out["secrets"].append(n)
    out["file_ids"] = list(dict.fromkeys(out["file_ids"]))
    out["memory_pool_ids"] = list(dict.fromkeys(out["memory_pool_ids"]))
    if not any([out["project_id"], out["file_ids"], out["secrets"], out["memory_pool_ids"]]):
        return out
    if kind != JobKind.agent_task:
        raise ValueError("resources (project, files, secrets, memory pools) apply to agent task jobs only")
    ws = (workspace or "").strip() or None
    if out["project_id"]:
        from projects.storage import ProjectStore
        from common.paths import PROJECTS_FILE
        project = ProjectStore(path=PROJECTS_FILE).get(out["project_id"])
        if project is None or (ws and (project.workspace or "") != ws):
            raise ValueError(f"project '{out['project_id']}' does not exist in workspace '{ws or 'default'}'")
    if out["file_ids"]:
        from files.service import get_files
        found = {str(f.get("id")): f for f in get_files(out["file_ids"])}
        for fid in out["file_ids"]:
            rec = found.get(fid)
            if rec is None or (ws and (rec.get("workspace") or "") != ws):
                raise ValueError(f"file '{fid}' does not exist in workspace '{ws or 'default'}'")
    if out["memory_pool_ids"]:
        from memory.store import MemoryStore
        pools = {str(m.id): m for m in MemoryStore().load()}
        for pid in out["memory_pool_ids"]:
            pool = pools.get(pid)
            if pool is None or (pool.workspace and ws and pool.workspace != ws):
                raise ValueError(f"memory pool '{pid}' does not exist in workspace '{ws or 'default'}'")
    if out["secrets"] and agent_id:
        from agents.registry import get_agent
        from agents.capability_guard import check_agent_tools, guard_mode
        from tools.capabilities import secret_grant_ids
        spec = get_agent(agent_id)
        if spec is not None:
            names = list(spec.secrets or []) + [n for n in out["secrets"] if n not in (spec.secrets or [])]
            violation = check_agent_tools(
                agent_id, list(spec.tools or []) + secret_grant_ids(names),
                previous_tools=list(spec.tools or []) + secret_grant_ids(spec.secrets or []),
                override=bool(spec.capability_override), delegates=list(spec.delegates or []),
                workspace=getattr(spec, "owner_workspace", None),
            )
            if violation is not None and violation.blocking and guard_mode() == "block":
                raise ValueError(f"secrets refused by the capability guard: {violation.message}")
    return out


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
    environment_id: Optional[str] = None,
    budget_usd: Optional[float] = None,
    agent_version: Optional[int] = None,
    auto_pause_after: int = 3,
    project_id: Optional[str] = None,
    file_ids: Optional[List[str]] = None,
    secrets: Optional[List[str]] = None,
    memory_pool_ids: Optional[List[str]] = None,
    memory_access: Optional[str] = None,
) -> ScheduledJob:
    tz_name = _validate_timezone(timezone)
    cron_expr = _validate_cron(cron) if recurrence == Recurrence.cron else None
    _validate_environment_id(environment_id, workspace)
    _validate_agent_version(agent_version, agent_id)
    resources = _validate_resources(kind, workspace, agent_id, project_id=project_id, file_ids=file_ids,
                                    secrets=secrets, memory_pool_ids=memory_pool_ids,
                                    memory_access=memory_access)
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
        environment_id=environment_id,
        budget_usd=budget_usd,
        agent_version=agent_version,
        auto_pause_after=auto_pause_after,
        **resources,
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
    if "environment_id" in fields:
        existing = plan_store.get(job_id)
        ws = existing.workspace if existing else None
        _validate_environment_id(fields["environment_id"], ws)
    if "agent_version" in fields or "agent_id" in fields:
        existing = plan_store.get(job_id)
        agent_id = fields.get("agent_id", existing.agent_id if existing else None)
        agent_version = fields.get("agent_version", existing.agent_version if existing else None)
        _validate_agent_version(agent_version, agent_id)
    if any(k in fields for k in _RESOURCE_FIELDS) or "agent_id" in fields:
        existing = plan_store.get(job_id)
        if existing is not None:
            merged = {k: fields.get(k, getattr(existing, k)) for k in _RESOURCE_FIELDS}
            agent_id = fields.get("agent_id", existing.agent_id)
            fields.update(_validate_resources(existing.kind, existing.workspace, agent_id, **merged))
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
    updated = plan_store.update(job_id, status=JobStatus.paused, paused_reason="manual")
    if updated:
        _notify_plan_changed()
    return updated


def resume_job(job_id: UUID | str) -> Optional[ScheduledJob]:
    job = plan_store.get(job_id)
    if job and job.status != JobStatus.paused:
        return None
    updated = plan_store.update(
        job_id, status=JobStatus.scheduled, paused_reason=None, consecutive_errors=0,
    )
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


def _upcoming_runs_at(job: ScheduledJob, count: int = 3) -> List[str]:
    """The next ``count`` occurrences of a recurring, scheduled job, as ISO
    strings. ``[]`` for a one-off job or one that will not fire again on its
    own (paused, cancelled, failed, fired).

    Reuses ``_next_run`` with a forced ``catch_up=True`` copy of the job: for
    both the cron and the hourly/daily/weekly branches, that mode computes the
    occurrence strictly after ``job.run_at`` regardless of the ``now`` it is
    given (see ``_next_run``'s docstring), which is exactly "the slot after
    this one" — independent of wall-clock time and of the job's own
    catch_up setting, which only matters when it actually fires.
    """
    if job.recurrence == Recurrence.none or job.status != JobStatus.scheduled:
        return []
    try:
        out: List[str] = []
        current = _ensure_aware(job.run_at)
        out.append(current.isoformat())
        probe = job.model_copy(update={"catch_up": True})
        for _ in range(max(0, count - 1)):
            probe = probe.model_copy(update={"run_at": current})
            current = _next_run(probe, current)
            out.append(current.isoformat())
        return out
    except Exception:
        log.debug("upcoming_runs_at failed for job %s", job.id, exc_info=True)
        return []


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
    data["upcoming_runs_at"] = _upcoming_runs_at(job)
    try:
        last = fire_store.last_for_job(job.id)
    except Exception:
        log.debug("last_fire lookup failed for job %s", job.id, exc_info=True)
        last = None
    data["last_fire"] = fire_to_dict(last) if last else None
    return data


def fire_to_dict(f: FireRecord) -> Dict[str, Any]:
    data = f.model_dump()
    data["id"] = str(data["id"])
    data["job_id"] = str(data["job_id"])
    for ts in ("at", "slot"):
        if data.get(ts) is not None:
            try:
                data[ts] = data[ts].isoformat()
            except Exception:
                pass
    return data


def list_job_fires(
    job_id: UUID | str, *, only_errors: bool = False, limit: int = 50,
) -> List[FireRecord]:
    return fire_store.list_for_job(job_id, only_errors=only_errors, limit=limit)


def list_fires(
    *, workspace: Optional[str] = None, only_errors: bool = False, limit: int = 100,
) -> List[FireRecord]:
    return fire_store.list_all(workspace=workspace, only_errors=only_errors, limit=limit)


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


def classify_fire_error(exc_or_message: Any) -> str:
    """Bucket a firing failure for the journal and the auto-pause decision.

    String based and deliberately conservative: an error that does not match
    a known pattern lands in ``"other"`` rather than a guess, since a wrong
    bucket could pause a job that would have recovered on its own (or fail to
    pause one that never will). Accepts either the exception itself (checked
    by type first, for ``BudgetExceededError``) or a plain message string, so
    a caller that only has ``last_error`` text (e.g. re-classifying an old
    journal row) can use it too.
    """
    try:
        from common.budget import BudgetExceededError
        if isinstance(exc_or_message, BudgetExceededError):
            return "budget_exceeded"
    except Exception:
        pass

    text = str(exc_or_message) if exc_or_message is not None else ""
    low = text.lower()
    if not low:
        return "other"
    if "unknown agent_id" in low or "agent not found" in low:
        return "agent_missing"
    if "flow not found" in low:
        return "flow_missing"
    if "loop not found" in low:
        return "loop_missing"
    if "loop" in low and "is still" in low:
        return "loop_busy"
    if "budget" in low and ("cap" in low or "exceeded" in low):
        return "budget_exceeded"
    if "locked by another firing" in low or "capacity" in low:
        return "capacity"
    if "workspace" in low and ("does not exist" in low or "invalid workspace name" in low):
        return "workspace_missing"
    return "other"


def _notify_job_paused(job: ScheduledJob, error: Optional[str], consecutive_errors: int) -> None:
    """Best-effort inbox alert when a job auto-pauses (errors or
    target_missing); never raises, so a notification failure can never wedge
    a firing tick."""
    try:
        create_notification(
            title=f"Scheduled job paused after {consecutive_errors} failures",
            body=f"'{job.title}' was paused after repeated failures: {error or 'unknown error'}",
            severity="warning",
            source={"job_id": str(job.id)},
            workspace=job.workspace,
        )
    except Exception:
        log.debug("auto-pause notification failed for job %s", job.id, exc_info=True)


def _record_fire(
    job: ScheduledJob, *, trigger: str, ok: bool, error_type: Optional[str],
    error: Optional[str], task_id: Optional[str], notification_id: Optional[str],
    loop_run_id: Optional[str], slot: datetime, duration_ms: int,
) -> None:
    """Append one journal row; never raises (a journal write must not turn a
    successful, or already-failed, firing into a harder failure)."""
    try:
        fire_store.add(FireRecord(
            job_id=job.id,
            workspace=job.workspace,
            slot=slot,
            trigger=trigger,
            ok=ok,
            error_type=error_type,
            error=error,
            task_id=task_id,
            notification_id=notification_id,
            loop_run_id=loop_run_id,
            duration_ms=duration_ms,
        ))
    except Exception:
        log.debug("failed writing fire journal for job %s", job.id, exc_info=True)


def fire_job(job: ScheduledJob, trigger: str = "schedule") -> Dict[str, Any]:
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

    ``trigger`` is recorded on the journal row this call writes (see
    ``FireRecord`` / ``FireStore``) and nowhere else: it does not change what
    firing does. It defaults to ``"schedule"`` (the automatic tick, via
    ``run_due_jobs``); the run-now route passes ``"manual"`` explicitly.

    Every call, including the ``already_fired_this_slot`` skip, appends one
    row to the firing journal (``plans.storage.FireStore``) and, on a
    recurring job, tracks ``consecutive_errors``: a run of failures reaching
    ``auto_pause_after`` (or a single failure classified as the job's agent,
    flow or loop no longer existing) pauses the job and raises a
    notification, since a target that is gone will not come back on its own.
    A success resets the counter. Journal writes and the auto-pause
    notification are both best-effort and never raise past this function.
    """
    if not job.lease_owner:
        claimed = plan_store.force_claim_job(job.id, _default_owner())
        if not claimed:
            return {
                "job_id": str(job.id), "kind": job.kind.value, "ok": False,
                "error": "job is locked by another firing, or is not in a fireable status",
            }
        job = claimed

    started = time.monotonic()
    now = _now()
    slot = _ensure_aware(job.run_at)
    already_fired = job.last_fired_slot is not None and _ensure_aware(job.last_fired_slot) == slot
    result: Dict[str, Any] = {"job_id": str(job.id), "kind": job.kind.value}
    error: Optional[str] = None
    error_type: Optional[str] = None

    if already_fired:
        result["skipped"] = "already_fired_this_slot"
        error_type = "skipped_slot"
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
            elif job.kind == JobKind.loop:
                fired = _fire_loop(job)
                result["task_id"] = fired.get("task_id")
                result["loop_run_id"] = fired.get("loop_run_id")
            else:
                result["task_id"] = _fire_agent_task(job)
        except Exception as e:
            error = str(e)
            error_type = classify_fire_error(e)

    fields: Dict[str, Any] = {
        "lease_until": None,
        "lease_owner": None,
        "fire_count": job.fire_count + 1,
        "last_fired_at": now,
        "last_error": error,
    }
    if job.kind in (JobKind.agent_task, JobKind.flow, JobKind.loop) and result.get("task_id"):
        fields["created_task_ids"] = [*job.created_task_ids, result["task_id"]]

    # -------------------- consecutive errors + auto pause --------------------
    # A one-off job (recurrence == none) is already terminal below (failed on
    # error), so the counter and auto-pause only matter for a recurring job:
    # pausing a job that will never fire again would just be confusing status.
    recurring = job.recurrence != Recurrence.none
    if already_fired:
        pass  # a skipped duplicate slot is not a failure of the job itself.
    elif error is not None:
        fields["consecutive_errors"] = job.consecutive_errors + 1
        if recurring:
            target_missing = error_type in _TARGET_MISSING_ERROR_TYPES
            threshold_hit = job.auto_pause_after > 0 and fields["consecutive_errors"] >= job.auto_pause_after
            if target_missing or threshold_hit:
                fields["status"] = JobStatus.paused
                fields["paused_reason"] = "target_missing" if target_missing else "errors"
                _notify_job_paused(job, error, fields["consecutive_errors"])
    else:
        fields["consecutive_errors"] = 0

    if job.recurrence == Recurrence.none:
        fields["status"] = JobStatus.failed if error else JobStatus.fired
    else:
        # Advance run_at regardless of whether this firing auto-paused the
        # job: a later resume then waits for the next natural slot instead of
        # finding a stale, already-past run_at and firing again immediately.
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
        result["error_type"] = error_type

    duration_ms = int((time.monotonic() - started) * 1000)
    _record_fire(
        job, trigger=trigger, ok=error is None, error_type=error_type, error=error,
        task_id=result.get("task_id"), notification_id=result.get("notification_id"),
        loop_run_id=result.get("loop_run_id"), slot=slot, duration_ms=duration_ms,
    )
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
        budget_usd=job.budget_usd,
        environment_id=job.environment_id,
        # The deployment's resources (docs/deployments.md, "Resources"): the
        # project and files become the task's, the extra secrets and the
        # pools reach only this task's runs.
        project_id=job.project_id or None,
        file_ids=list(job.file_ids or []),
        secrets=list(job.secrets or []),
        memory_pool_ids=list(job.memory_pool_ids or []),
        memory_access=(job.memory_access or "read") if job.memory_pool_ids else "write",
        # Only meaningful once job.agent_id names the agent it pins a version
        # of (validated together at create/update time); a task this job
        # creates unassigned carries the pin with no agent yet to check it
        # against, same as a task the operator pins before assigning one.
        agent_version=job.agent_version if job.agent_id else None,
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


def _apply_job_fields_to_task(task_id: str, job: ScheduledJob) -> None:
    """Copy the job's ``budget_usd``/``environment_id`` onto a task a flow or
    loop launcher created, after the fact.

    ``_fire_agent_task`` passes both straight into ``tasks_service.create_task``
    because it creates the task itself; a flow or loop firing does not — the
    task is created inside ``flow.launcher.trigger_flow`` / ``loops.launcher.
    start_loop_run``, outside this module's files, so this function is the
    equivalent for those two paths, applied to the task id they hand back.
    A no-op when the job sets neither (the common case), and best-effort
    otherwise: a task already exists and is running by the time this runs, so
    a failure here must not turn a successful firing into a failed one.
    """
    if job.budget_usd is None and job.environment_id is None:
        return
    try:
        from tasks import service as tasks_service
        tasks_service.update_task(
            UUID(str(task_id)), budget_usd=job.budget_usd, environment_id=job.environment_id,
        )
    except Exception:
        log.debug(
            "failed applying budget/environment to task %s for job %s", task_id, job.id,
            exc_info=True,
        )


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
    _apply_job_fields_to_task(result["task_id"], job)
    create_notification(
        title=f"Scheduled flow started: {job.title}",
        body=f"Flow '{job.flow_id}' was triggered by scheduled job {job.id}.",
        source={"job_id": str(job.id), "task_id": result["task_id"], "flow_id": job.flow_id},
        workspace=job.workspace,
        channels=job.channels,
    )
    return result["task_id"]


def _fire_loop(job: ScheduledJob) -> Dict[str, Any]:
    """Start a run of the job's loop. Returns ``{"loop_run_id", "task_id"}``.

    Goes through ``loops.launcher.start_loop_run``, the same door the loop
    route uses (dashboard/backend/routes/loops.py), so a scheduled run is a
    process of its own, queued for a worker in the ``api`` role. A run of the
    same loop that is still active makes this firing raise, which
    ``fire_job`` records as ``last_error``: two maintenance passes over the
    same repository copy at once would only trip over each other.
    """
    if not job.loop_id:
        raise ValueError("loop job has no loop_id")

    from common.run_status import ACTIVE_STATUSES
    from loops import store as loop_store
    from loops.launcher import start_loop_run

    loop = loop_store.get_loop(job.loop_id)
    if loop is None:
        raise ValueError(f"Loop not found: {job.loop_id}")
    active = [r for r in loop_store.list_runs(job.loop_id, limit=20)
              if r.status in ACTIVE_STATUSES]
    if active:
        raise RuntimeError(
            f"skipped: run {active[0].loop_run_id} of loop '{job.loop_id}' is still "
            f"{active[0].status}")

    run = start_loop_run(
        job.loop_id, job.message or "", workspace=job.workspace or loop.workspace,
        seed=dict(job.seed or {}),
    )
    _apply_job_fields_to_task(run.task_id, job)
    create_notification(
        title=f"Scheduled loop started: {job.title}",
        body=f"Loop '{job.loop_id}' was started by scheduled job {job.id}.",
        source={"job_id": str(job.id), "task_id": run.task_id, "loop_id": job.loop_id,
                "loop_run_id": run.loop_run_id},
        workspace=job.workspace,
        channels=job.channels,
    )
    return {"loop_run_id": run.loop_run_id, "task_id": run.task_id}


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


def run_due_jobs(owner: Optional[str] = None,
                 fence_token: Optional[int] = None) -> List[Dict[str, Any]]:
    """Claim and fire every due job once. Called by the scheduler loop each tick.

    ``fence_token`` is the ``scheduler`` service lease version the tick holds
    (``common.leases.fencing_token``). The per-job claim already keeps two
    replicas off the same job; the token is what stops a leader that stalled
    past its TTL and was superseded from firing anything at all. It is
    re-checked before each job, and the tick stops at the first failed check.
    Jobs it claimed but did not fire keep their short claim until it lapses,
    then the new leader takes them.
    """
    from common import leases
    from plans.scheduler import PlanScheduler

    results = []
    for job in claim_due_jobs(owner=owner):
        if fence_token is not None and not leases.verify(PlanScheduler.LEASE_ROLE, fence_token):
            log.warning("scheduler lease lost mid-tick, not firing job %s or the rest", job.id)
            break
        results.append(fire_job(job))
    return results


__all__ = [
    "NOTIFICATIONS_CHANNEL",
    "plan_store",
    "fire_store",
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
    "fire_to_dict",
    "list_job_fires",
    "list_fires",
    "classify_fire_error",
    "due_jobs",
    "claim_due_jobs",
    "claim_job_now",
    "fire_job",
    "run_due_jobs",
]
