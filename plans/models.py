from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class JobKind(str, Enum):
    notification = "notification"
    agent_task = "agent_task"
    flow = "flow"
    # Starts a run of a loop (loops/launcher.py). Used by the system
    # workspace's maintenance loop (common/system_workspace.py).
    loop = "loop"
    # One tick of a proactive agent (proactive/service.py, docs/proactive.md):
    # the scheduler fires it like an agent_task, but the firing first checks
    # the agent's quiet hours, daily budget, tick limit and whether the
    # previous tick is still running, and the task's answer is a structured
    # outcome written back onto the journal row. One such job per agent,
    # owned by the agent's proactive profile, never created by hand.
    heartbeat = "heartbeat"


class JobStatus(str, Enum):
    scheduled = "scheduled"
    paused = "paused"
    fired = "fired"
    cancelled = "cancelled"
    failed = "failed"


class Recurrence(str, Enum):
    none = "none"
    hourly = "hourly"
    daily = "daily"
    weekly = "weekly"
    cron = "cron"


class ScheduledJob(BaseModel):
    """A future action: either a user notification or an agent task to start.

    Jobs are NOT tasks — a Task record is only materialized when an
    agent_task job fires, so node pollers never see future work early.
    """

    id: UUID = Field(default_factory=uuid4)
    kind: JobKind
    title: str
    # Notification body, or the description of the Task created on firing.
    message: str = ""

    run_at: datetime
    recurrence: Recurrence = Recurrence.none
    # recurrence == cron only: the cron expression driving the next run_at.
    cron: Optional[str] = None
    # IANA timezone name (e.g. "Europe/Berlin"). None means UTC. Used for
    # cron evaluation and to keep hourly/daily/weekly firings at the same
    # local wall-clock time across a DST change.
    timezone: Optional[str] = None
    # Opt-in: when the scheduler finds this job more than one slot behind (the
    # backend was down through several occurrences), advance one occurrence
    # per firing instead of jumping straight to the next slot that is still in
    # the future. False (default) keeps today's behaviour: every missed
    # occurrence in between is silently dropped. See plans.service._next_run.
    catch_up: bool = False
    status: JobStatus = JobStatus.scheduled

    # -------------------- lease + idempotency --------------------
    # Held while a scheduler tick is firing this job, so a second tick (a
    # second replica, or an overlapping slow fire) skips it instead of
    # firing it again. Cleared once the firing completes.
    lease_until: Optional[datetime] = None
    lease_owner: Optional[str] = None
    # Total number of times this job has actually fired.
    fire_count: int = 0
    # run_at of the slot most recently fired. Firing checks this before
    # doing anything visible, so a retry after a crash between the side
    # effect and updating the record does not fire the same slot twice.
    last_fired_slot: Optional[datetime] = None

    workspace: Optional[str] = None
    created_by: str = "user"  # "user" | "agent"

    # agent_task only: preassigned agent. None → orchestrator routes the task.
    agent_id: Optional[str] = None
    # flow only: the flow to trigger, an optional JSON seed merged into its
    # initial state, and a concurrency cap (0 = unlimited) so a recurring flow
    # job can't stack up runaway instances.
    flow_id: Optional[str] = None
    seed: Optional[Dict[str, Any]] = None
    max_concurrent: int = 1
    # loop only: the loop to start. A firing is skipped, with an error on the
    # job, while a run of the same loop is still active.
    loop_id: Optional[str] = None
    # Delivery channels for notifications ("dashboard"; "telegram" later).
    channels: List[str] = Field(default_factory=lambda: ["dashboard"])

    # agent_task/flow/loop: the environment (environments/), money cap
    # (common/run_budget.py) and agent version pin (agents/versions.py)
    # copied onto every task this job creates. For a flow or loop firing the
    # task is created by that launcher, not here, so plans.service applies
    # these after the fact by updating the task it returns; None means "the
    # task's own default" for all three.
    environment_id: Optional[str] = None
    budget_usd: Optional[float] = None
    # Only meaningful for an agent_task job with a preassigned agent_id — a
    # flow/loop job's task has no single agent to pin at fire time. Validated
    # against agent_id at create/update time (plans.service).
    agent_version: Optional[int] = None

    # agent_task only: the resources of a deployment, copied onto every task
    # it creates (docs/deployments.md, "Resources"). The project the task
    # belongs to, the workspace files it gets, secret names handed to its
    # runs on top of the agent's own allowlist, and memory pools bound for
    # its runs only: the agent record keeps its own binding, so no chat or
    # other task of the agent sees these pools.
    project_id: Optional[str] = None
    file_ids: List[str] = Field(default_factory=list)
    secrets: List[str] = Field(default_factory=list)
    memory_pool_ids: List[str] = Field(default_factory=list)
    # How the runs may use those pools: "read" (the default for a deployment's
    # pools, reference material) builds the run without the memory write
    # tools; "write" lets it remember, forget and link like the agent's own
    # binding would.
    memory_access: str = "read"

    last_fired_at: Optional[datetime] = None
    last_error: Optional[str] = None
    # Task IDs created by firings of this job (newest last).
    created_task_ids: List[str] = Field(default_factory=list)
    # heartbeat only: events that woke the agent and have not been handed to
    # a tick yet (proactive.service.wake_agent). A wake pulls ``run_at`` to
    # within the batching window; the tick that then starts takes every
    # pending event into its prompt and clears the list. Capped at
    # proactive.service.MAX_PENDING_EVENTS, oldest dropped first.
    pending_events: List[Dict[str, Any]] = Field(default_factory=list)

    # -------------------- auto pause on repeated failure --------------------
    # A recurring job that keeps failing pauses itself rather than firing
    # forever into the void. 0 turns this off (it still fires and records the
    # journal, it just never auto-pauses).
    auto_pause_after: int = 3
    # Firing failures in a row, reset to 0 on the next success. Never consulted
    # for a one-off job (recurrence == none): it is already terminal (failed)
    # after its single firing.
    consecutive_errors: int = 0
    # Why a paused job is paused: "manual" (pause_job / the pause route),
    # "errors" (auto_pause_after consecutive failures), "target_missing" (the
    # agent/flow/loop this job points at no longer exists — that never fixes
    # itself, so it pauses on the first such failure regardless of the
    # counter). None for a job that was never auto/manually paused.
    paused_reason: Optional[str] = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        try:
            object.__setattr__(self, "updated_at", datetime.now(timezone.utc))
        except Exception:
            setattr(self, "updated_at", datetime.now(timezone.utc))


class FireRecord(BaseModel):
    """One attempt to fire a :class:`ScheduledJob`: what happened and how long
    it took.

    Kept in its own collection (``plans.storage.FireStore``, over
    ``DocStore("plan_fires")``) rather than folded into the job record, so the
    journal can grow without bound across every firing while the job itself
    stays small and cheap to read on every list. Retention is a daily prune in
    ``common.maintenance.run_maintenance`` (``AGENTS_HUB_PLAN_FIRES_RETENTION_DAYS``),
    not a cap on this model.

    One record is written per :func:`plans.service.fire_job` call, including
    the ``already_fired_this_slot`` skip (``ok=True``,
    ``error_type="skipped_slot"``) so the journal shows every attempt, not
    just the ones that did something.
    """

    id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    workspace: Optional[str] = None
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # The job's run_at this attempt was firing for (fire_job's docstring on
    # last_fired_slot explains why a slot, not a wall-clock moment, is what
    # idempotency is keyed on).
    slot: Optional[datetime] = None
    # "schedule": the automatic tick (plans.scheduler / run_due_jobs), the
    # default for a bare fire_job() call. "manual": the run-now route.
    trigger: str = "schedule"
    ok: bool = True
    # One of plans.service.classify_fire_error's buckets, or "skipped_slot"
    # for the already-fired-this-slot case. None when ok is True and nothing
    # was skipped.
    error_type: Optional[str] = None
    error: Optional[str] = None
    task_id: Optional[str] = None
    notification_id: Optional[str] = None
    loop_run_id: Optional[str] = None
    duration_ms: Optional[int] = None

    # -------------------- heartbeat ticks (proactive/service.py) --------------------
    # What the tick came to. Set at fire time for a tick that never started a
    # task (``quiet_hours``, ``budget``, ``rate``, ``busy``, ``disabled``), and
    # when the task's run finishes for one that did: ``acted``, ``quiet`` or
    # ``blocked`` from the agent's structured answer, ``error`` for a failed
    # run. None until then (a tick still running) and for every other kind.
    outcome: Optional[str] = None
    # The agent's own one-line account of the tick, and what it asked itself
    # to check next time (the next tick's prompt carries it).
    summary: Optional[str] = None
    next_check: Optional[str] = None
    # Estimated USD the tick's run(s) cost (common.pricing.run_cost_usd), so
    # the daily budget is a sum over this journal rather than over every run.
    cost_usd: Optional[float] = None
    # How many ticks this row stands for. 1 for a row as written; the journal
    # compaction (proactive.service.compact_journal) folds a day's quiet and
    # skipped ticks into one row with their count, keeping acted, blocked and
    # error rows as they are.
    count: int = 1


class Notification(BaseModel):
    """An inbox entry shown to the user (bell icon / notification list)."""

    id: UUID = Field(default_factory=uuid4)
    title: str
    body: str = ""
    severity: str = "info"  # info | success | warning | error
    # Origin pointers, e.g. {"job_id": "...", "task_id": "..."}
    source: Dict[str, Any] = Field(default_factory=dict)
    workspace: Optional[str] = None
    read: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


__all__ = [
    "JobKind",
    "JobStatus",
    "Recurrence",
    "ScheduledJob",
    "FireRecord",
    "Notification",
]
