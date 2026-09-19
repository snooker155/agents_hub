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
    status: JobStatus = JobStatus.scheduled

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
    # Delivery channels for notifications ("dashboard"; "telegram" later).
    channels: List[str] = Field(default_factory=lambda: ["dashboard"])

    last_fired_at: Optional[datetime] = None
    last_error: Optional[str] = None
    # Task IDs created by firings of this job (newest last).
    created_task_ids: List[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        try:
            object.__setattr__(self, "updated_at", datetime.now(timezone.utc))
        except Exception:
            setattr(self, "updated_at", datetime.now(timezone.utc))


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
    "Notification",
]
