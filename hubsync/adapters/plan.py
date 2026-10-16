"""
Scheduled jobs for the sync engine: reminders, recurring agent tasks, flow and
loop triggers.

Two hubs firing the same job would send the reminder twice and start the task
twice. So a job arrives **paused** (reason ``synced``) on the hub that did not
create it, and the person resumes it where it should run. ``status`` is local
for the same reason: it is left out of the document and an existing job keeps
its own, so pausing on one hub is not undone by the next sync.

Also local, because they describe what happened on this hub: leases, fire
counts and the last slot, last error, the ids of tasks the firings created,
queued wake events and the auto-pause counters. The journal of fires
(``plan_fires``) and notifications are not jobs and are not synced.

``run_at`` is content for a one-off job. For a recurring one it is the next
slot, which moves on every firing, so it travels only as ``next_run_hint``
(volatile) and a hub that already has the job keeps its own schedule unless
the recurrence, cron or timezone changed. Jobs a proactive profile owns
(kind ``heartbeat``, or created by ``system``) are derived from the agent
record and are skipped: the agent carries the profile.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from hubsync.adapters.base import Adapter, AdapterError, register

_RUN_TIME_KEYS = (
    "id", "status", "workspace", "lease_until", "lease_owner", "fire_count",
    "last_fired_slot", "last_fired_at", "last_error", "created_task_ids",
    "created_consolidation_ids", "pending_events", "consecutive_errors",
    "paused_reason", "run_at",
)


def _owned_by_agent(job: Any) -> bool:
    return job.kind.value == "heartbeat" or job.created_by == "system"


class PlanAdapter(Adapter):
    kind = "plan"
    order = 47
    label = "Scheduled jobs"
    volatile = ("updated_at", "next_run_hint")

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        from plans import service
        return sorted(str(j.id) for j in service.list_jobs(workspace) if not _owned_by_agent(j))

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        from plans import service
        job = service.get_job(record_id)
        if job is None or _owned_by_agent(job):
            return None
        data = job.model_dump(mode="json")
        doc = {k: v for k, v in data.items() if k not in _RUN_TIME_KEYS}
        if job.recurrence.value == "none":
            doc["run_at"] = data["run_at"]
        else:
            doc["next_run_hint"] = data["run_at"]
        return doc

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        from plans import service
        from plans.models import JobStatus, ScheduledJob
        prev = service.get_job(record_id)
        if prev is not None and (prev.workspace or "") != workspace:
            raise AdapterError(f"job {record_id} lives in another workspace here")
        if prev is not None and _owned_by_agent(prev):
            raise AdapterError(f"job {record_id} is owned by an agent profile here")
        data = {k: v for k, v in doc.items() if k != "next_run_hint"}
        hint = doc.get("next_run_hint")
        data["id"] = record_id
        data["workspace"] = workspace
        try:
            service._validate_timezone(data.get("timezone"))
            if data.get("recurrence") == "cron":
                service._validate_cron(data.get("cron"))
        except Exception as exc:  # noqa: BLE001 - the validators raise ValueError or an HTTP-style error
            raise AdapterError(f"invalid schedule: {exc}") from exc
        if prev is not None:
            local = prev.model_dump(mode="json")
            for key in _RUN_TIME_KEYS:
                if key in local and key not in ("id", "workspace"):
                    data[key] = local[key]
            same = all(local.get(k) == data.get(k) for k in ("recurrence", "cron", "timezone"))
            if data.get("recurrence") != "none" and same:
                data["run_at"] = local["run_at"]
        else:
            data["status"] = JobStatus.paused.value
            data["paused_reason"] = "synced"
        if "run_at" not in data:
            data["run_at"] = hint or datetime.now(timezone.utc).isoformat()
        try:
            job = ScheduledJob.model_validate(data)
        except ValueError as exc:
            raise AdapterError(f"unreadable job: {exc}") from exc
        if prev is not None and data.get("recurrence") != "none" and "run_at" in data \
                and not same:
            job.run_at = service._next_run(job, datetime.now(timezone.utc))
        service.plan_store.add(job)
        service._notify_plan_changed()
        return record_id

    def delete(self, workspace: str, record_id: str) -> None:
        from plans import service
        service.delete_job(record_id)


register(PlanAdapter())
