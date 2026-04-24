from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, List, Dict  # noqa: F401 — List/Dict/Any used by SharedMemory
from uuid import UUID, uuid4
from pydantic import BaseModel, Field

class TaskStatus(str, Enum):
    todo = "todo"
    ready = "ready"
    pending = "pending"
    in_progress = "in_progress"
    blocked = "blocked"
    stopped = "stopped"
    resolved = "resolved"
    reviewing = "reviewing"
    reviewed = "reviewed"
    done = "done"

class CreatedBy(str, Enum):
    user = "user"
    orchestrator = "orchestrator"
    external = "external"

class AgentState(str, Enum):
    none = "none"
    assigned = "assigned"
    pending = "pending"
    pending_approval = "pending_approval"
    running = "running"
    stopped = "stopped"
    completed = "completed"
    failed = "failed"

class Task(BaseModel):
    id: UUID = Field(default_factory=uuid4, description="Unique task identifier")
    title: str
    description: str = ""

    status: TaskStatus = Field(default=TaskStatus.todo)
    created_by: CreatedBy = Field(default=CreatedBy.user)

    parent_id: Optional[UUID] = None
    sequence_id: Optional[str] = None
    order: Optional[int] = None
    blocked_reason: Optional[str] = None
    should_decompose: bool = Field(default=False, description="Whether to automatically decompose this task")

    priority: Optional[str] = Field(default=None, description="Task priority: low, medium, high, critical")

    # Project association
    project_id: Optional[str] = Field(
        default=None, description="ID of the project this task belongs to"
    )

    # Workspace path for this task (where swe-agent should work)
    workspace: Optional[str] = Field(
        default=None, description="Absolute or relative path to the workspace for this task"
    )

    # Project subfolder within the workspace (agents are sandboxed here)
    project: Optional[str] = Field(
        default=None, description="Project subfolder name within the workspace (agents read/write here)"
    )

    # Agent assignment and execution control
    assigned_agent_type: Optional[str] = Field(
        default=None, description="Type/name of the agent assigned to this task"
    )
    assigned_agent_params: Optional[dict[str, Any]] = Field(
        default=None, description="Arbitrary parameters for the assigned agent"
    )
    assigned_agent_run_id: Optional[str] = Field(
        default=None, description="External run identifier for the agent execution"
    )
    routing_reason: Optional[str] = Field(
        default=None, description="Orchestrator's reasoning for choosing this agent"
    )

    # Session context ID grouping all agent runs for this task
    session_id: Optional[str] = Field(default=None, description="Session context ID for all agent runs on this task")

    # Status before a pending agent assignment — used to restore on rejection
    pre_assignment_status: Optional[TaskStatus] = Field(
        default=None, description="Task status captured just before an agent was assigned (pending approval)"
    )

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        """Update the updated_at timestamp to now (UTC)."""
        try:
            object.__setattr__(self, "updated_at", datetime.now(timezone.utc))
        except Exception:
            setattr(self, "updated_at", datetime.now(timezone.utc))

    @property
    def agent_state(self) -> AgentState:
        """Backward-compatible runtime state derived from assignment/run, not persisted."""
        if self.assigned_agent_type and not self.assigned_agent_run_id:
            return AgentState.pending_approval
        if self.assigned_agent_run_id:
            try:
                from agents import run_manager
                run = run_manager.get_run_by_id(str(self.assigned_agent_run_id))
                if run:
                    st = str(run.get("status") or "")
                    if st in ("awaiting_approval",):
                        return AgentState.pending_approval
                    if st in ("assigned",):
                        return AgentState.assigned
                    if st in ("pending",):
                        return AgentState.pending
                    if st in ("running",):
                        return AgentState.running
                    if st in ("completed", "done", "finished"):
                        return AgentState.completed
                    if st in ("stopped", "stop"):
                        return AgentState.stopped
                    if st in ("failed", "error"):
                        return AgentState.failed
            except Exception:
                pass
        return AgentState.none

from memory.models import SharedMemory  # noqa: F401 — re-exported for backward compatibility
