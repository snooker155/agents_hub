from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, List, Dict
from uuid import UUID, uuid4
from pydantic import BaseModel, Field

class TaskStatus(str, Enum):
    todo = "todo"
    in_progress = "in_progress"
    blocked = "blocked"
    stopped = "stopped"
    done = "done"

class CreatedBy(str, Enum):
    user = "user"
    orchestrator = "orchestrator"

class AgentState(str, Enum):
    none = "none"
    assigned = "assigned"
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

    # Workspace path for this task (where swe-agent should work)
    workspace: Optional[str] = Field(
        default=None, description="Absolute or relative path to the workspace for this task"
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
    agent_state: AgentState = Field(
        default=AgentState.none,
        description="State of the agent execution lifecycle",
    )

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        """Update the updated_at timestamp to now (UTC)."""
        try:
            object.__setattr__(self, "updated_at", datetime.now(timezone.utc))
        except Exception:
            setattr(self, "updated_at", datetime.now(timezone.utc))

class SharedMemory(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    type: str = "text"
    description: str = ""
    workspace: Optional[str] = None
    files: List[Dict[str, Any]] = Field(default_factory=list)  # [{name, content, rag_status, ...}]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        """Update the updated_at timestamp to now (UTC)."""
        try:
            object.__setattr__(self, "updated_at", datetime.now(timezone.utc))
        except Exception:
            setattr(self, "updated_at", datetime.now(timezone.utc))
