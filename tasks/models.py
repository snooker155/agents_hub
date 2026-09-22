from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, List, Dict, FrozenSet  # noqa: F401 — List/Dict/Any used by SharedMemory
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

class TaskStatus(str, Enum):
    todo = "todo"
    ready = "ready"
    pending = "pending"
    in_progress = "in_progress"
    blocked = "blocked"
    awaiting_input = "awaiting_input"
    awaiting_approval = "awaiting_approval"
    stopped = "stopped"
    resolved = "resolved"
    reviewing = "reviewing"
    reviewed = "reviewed"
    done = "done"


class Actor(str, Enum):
    """Who is asking for a status transition.

    - ``user``: a person acting through the dashboard (routes pass this).
    - ``agent``: an assigned agent acting through the ``update_task`` tool
      (and the dedicated ``stop_task``/``block_task`` tools).
    - ``system``: the run manager, the finalizer, the watchdog, and every
      internal cascade in this module. Unrestricted within ``TRANSITIONS``.
    """
    user = "user"
    agent = "agent"
    system = "system"


# The closed transition table: every (from, to) pair a task is actually seen
# to move through in this codebase (service cascades, the run finalizer, the
# watchdog, container/dependency handling, escalation, manual assignment, and
# the routes). A ``system`` actor (the default — every internal caller not
# touched by this change) may perform any of these. A `user` or `agent` actor
# is additionally filtered against USER_TARGETS / AGENT_TRANSITIONS below.
# Nothing here is speculative: each edge traces back to a real call site or an
# existing test.
#
# One rule cuts across almost every state: manual assignment
# (``tasks.assign.assign_agent_to_task``, used by both the dashboard and the
# terminal client) refuses only a *stopped* task and otherwise re-assigns from
# wherever the task sits, landing it on ``ready`` (node mode), ``in_progress``
# (subprocess mode), or ``reviewing`` (assigning code_reviewer) — this is how
# a blocked, resolved, reviewed or even done task can be picked up again. That
# rule is spelled out once here and then folded into every state below.
_REASSIGNABLE_TARGETS = frozenset({TaskStatus.ready, TaskStatus.in_progress, TaskStatus.reviewing})

TRANSITIONS: Dict[TaskStatus, FrozenSet[TaskStatus]] = {
    # Not started. Reassignable; can be blocked by a dependency, aborted, or —
    # in the direct-assign path that skips the in_progress step — resolve, or
    # park on a question or a tool-call approval right away (the approval gate
    # does not require in_progress first).
    TaskStatus.todo: _REASSIGNABLE_TARGETS | frozenset({
        TaskStatus.blocked, TaskStatus.stopped, TaskStatus.done,
        TaskStatus.resolved, TaskStatus.awaiting_input,
        TaskStatus.awaiting_approval,
    }),
    # Queued for the orchestrator/worker poller. Reassignable; same other
    # moves as todo, plus the finalizer's "no verdict yet" fallback.
    TaskStatus.ready: _REASSIGNABLE_TARGETS | frozenset({
        TaskStatus.todo, TaskStatus.blocked, TaskStatus.stopped,
        TaskStatus.done, TaskStatus.resolved,
    }),
    # Assigned but waiting on a manual "start" approval (assignment_mode !=
    # live). Approval moves it on; reassignable, can be blocked, reset, or
    # stopped while it waits.
    TaskStatus.pending: _REASSIGNABLE_TARGETS | frozenset({
        TaskStatus.todo, TaskStatus.blocked, TaskStatus.stopped,
        TaskStatus.resolved,
    }),
    # An agent is running. Reassignable; may finish (resolved), park on a
    # question or an approval, fail (blocked), complete a container (done),
    # or be stopped.
    TaskStatus.in_progress: _REASSIGNABLE_TARGETS | frozenset({
        TaskStatus.resolved, TaskStatus.awaiting_input,
        TaskStatus.awaiting_approval, TaskStatus.blocked,
        TaskStatus.stopped, TaskStatus.done,
    }),
    # Work cannot proceed. Reassigning an agent is how a block gets fixed
    # (explicitly allowed even while blocked); unblocking also restores it to
    # todo/ready under a cascade, or it can be stopped.
    TaskStatus.blocked: _REASSIGNABLE_TARGETS | frozenset({
        TaskStatus.todo, TaskStatus.stopped,
    }),
    # Paused on a question to the user. Reassignable; answering (or an
    # auto-answer) resumes it; the user may also block or stop it instead of
    # answering, and the finalizer's fallback can resolve it directly if the
    # run behind it finished in the meantime.
    TaskStatus.awaiting_input: _REASSIGNABLE_TARGETS | frozenset({
        TaskStatus.blocked, TaskStatus.stopped, TaskStatus.resolved,
    }),
    # Paused on a tool call that needs a yes/no. Reassignable; approving or
    # denying resumes it either way; block/stop remain available, same
    # finalizer fallback as awaiting_input.
    TaskStatus.awaiting_approval: _REASSIGNABLE_TARGETS | frozenset({
        TaskStatus.blocked, TaskStatus.stopped, TaskStatus.resolved,
    }),
    # Aborted or paused by the user. Not reassignable (assign_agent_to_task
    # refuses a stopped task) — only resume brings it back: to todo (a
    # container's subtasks) or in_progress (the container itself).
    TaskStatus.stopped: frozenset({
        TaskStatus.todo, TaskStatus.ready, TaskStatus.in_progress,
    }),
    # The worker finished. Reassignable; otherwise handed to a reviewer,
    # failed post-hoc (blocked), completed without review (done), stopped, or
    # — the finalizer's own "not yet verdicted" fallback — pushed back to
    # in_progress by a later completed run on the same task.
    TaskStatus.resolved: _REASSIGNABLE_TARGETS | frozenset({
        TaskStatus.blocked, TaskStatus.stopped, TaskStatus.done,
    }),
    # A reviewer is running. Reassignable; records a verdict (reviewed),
    # falls back to resolved if it never records one, can be blocked/stopped,
    # or kept "ongoing" by a session continuation fired while it runs.
    TaskStatus.reviewing: _REASSIGNABLE_TARGETS | frozenset({
        TaskStatus.reviewed, TaskStatus.resolved, TaskStatus.blocked,
        TaskStatus.stopped,
    }),
    # Reviewed. Reassignable; passed (done), sent back for rework
    # (in_progress, already part of the reassignable set), or blocked/stopped.
    TaskStatus.reviewed: _REASSIGNABLE_TARGETS | frozenset({
        TaskStatus.done, TaskStatus.blocked, TaskStatus.stopped,
    }),
    # Finished. Reassignable (redoing a done task); not fully terminal
    # either way — an external issue sync can reopen it straight to todo.
    TaskStatus.done: _REASSIGNABLE_TARGETS | frozenset({TaskStatus.todo}),
}

# Statuses a `user` actor may set as the *target* of a transition (the
# dashboard's Kanban board already encodes exactly this: the "waiting" and
# "reviewing" columns are marked non-droppable). A user's move must also be a
# real edge in TRANSITIONS — this only narrows which destinations they reach.
USER_TARGETS: FrozenSet[TaskStatus] = frozenset({
    TaskStatus.todo, TaskStatus.ready, TaskStatus.in_progress,
    TaskStatus.blocked, TaskStatus.stopped, TaskStatus.done,
    TaskStatus.reviewed, TaskStatus.resolved,
})

# The only transitions an `agent` actor (the update_task/stop_task/block_task
# tools) may perform. Deliberately a short, explicit allowlist rather than a
# filter over TRANSITIONS: an agent must never set done, awaiting_*, stopped
# or pending, and reaches the review states only as the reviewer (below).
# todo/ready -> in_progress is the agent picking up work it was given (a
# normal, expected move, not a status the system alone should gate).
# The reviewer is the one exception on the verdict states: the code_reviewer
# agent's whole job is to move a resolved task to reviewing and then record its
# verdict (reviewed, or back to in_progress with findings), so those two rows
# exist for it. They still cannot be reached from any other state.
AGENT_TRANSITIONS: Dict[TaskStatus, FrozenSet[TaskStatus]] = {
    TaskStatus.todo: frozenset({TaskStatus.ready, TaskStatus.in_progress}),
    TaskStatus.ready: frozenset({TaskStatus.in_progress}),
    TaskStatus.in_progress: frozenset({TaskStatus.resolved, TaskStatus.blocked}),
    TaskStatus.blocked: frozenset({TaskStatus.in_progress}),
    TaskStatus.resolved: frozenset({TaskStatus.reviewing}),
    TaskStatus.reviewing: frozenset({TaskStatus.reviewed, TaskStatus.in_progress}),
    # The orchestrator closes a task once the reviewer approved it (its
    # instructions say so in Step 5): done is reachable for an agent from
    # reviewed and from nowhere else.
    TaskStatus.reviewed: frozenset({TaskStatus.done}),
}


class IllegalTransition(ValueError):
    """Raised when an actor asks for a status change the table forbids."""

    def __init__(self, from_status: "TaskStatus", to_status: "TaskStatus", actor: "Actor | str"):
        actor_name = actor.value if isinstance(actor, Actor) else str(actor)
        self.from_status = from_status
        self.to_status = to_status
        self.actor = actor_name
        super().__init__(
            f"Illegal transition from '{from_status.value}' to '{to_status.value}' "
            f"for actor '{actor_name}'"
        )


def check_transition(from_status: "TaskStatus", to_status: "TaskStatus", actor: "Actor | str" = Actor.system) -> None:
    """Raise IllegalTransition unless ``actor`` may move a task from-to.

    ``system`` may perform anything in TRANSITIONS (every internal caller not
    explicitly given a different actor keeps working exactly as before).
    ``user`` is further filtered to USER_TARGETS; ``agent`` is checked
    against the explicit AGENT_TRANSITIONS allowlist.
    """
    if not isinstance(actor, Actor):
        try:
            actor = Actor(str(actor))
        except ValueError:
            raise IllegalTransition(from_status, to_status, actor)

    allowed = to_status in TRANSITIONS.get(from_status, frozenset())
    if not allowed:
        raise IllegalTransition(from_status, to_status, actor)

    if actor is Actor.system:
        return
    if actor is Actor.user:
        if to_status in USER_TARGETS:
            return
        raise IllegalTransition(from_status, to_status, actor)
    if actor is Actor.agent:
        if to_status in AGENT_TRANSITIONS.get(from_status, frozenset()):
            return
        raise IllegalTransition(from_status, to_status, actor)
    raise IllegalTransition(from_status, to_status, actor)


# Statuses that stop a deadline from counting as overdue: the work is no
# longer moving, one way or another.
OVERDUE_EXCLUDED_STATUSES: FrozenSet[TaskStatus] = frozenset({
    TaskStatus.done, TaskStatus.reviewed, TaskStatus.resolved, TaskStatus.stopped,
})


def is_overdue(due_at: Optional[datetime], status: "TaskStatus") -> bool:
    """True when ``due_at`` is in the past and the task is still active."""
    if due_at is None or status in OVERDUE_EXCLUDED_STATUSES:
        return False
    d = due_at if due_at.tzinfo is not None else due_at.replace(tzinfo=timezone.utc)
    return d < datetime.now(timezone.utc)


class TaskPriority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


# Lower number sorts first — used to put higher priority ahead in dispatch order.
_PRIORITY_ORDER: Dict["TaskPriority", int] = {
    TaskPriority.critical: 0,
    TaskPriority.high: 1,
    TaskPriority.medium: 2,
    TaskPriority.low: 3,
}


def priority_sort_key(priority: "TaskPriority") -> int:
    """Numeric key for dispatch ordering: lower sorts first (higher priority)."""
    return _PRIORITY_ORDER.get(priority, _PRIORITY_ORDER[TaskPriority.medium])


def coerce_priority(value: Any) -> TaskPriority:
    """Coerce a raw priority value to TaskPriority, defaulting to medium.

    Accepts a TaskPriority, a matching string (any case), or None. Any other
    value logs a warning and falls back to medium rather than rejecting the
    write outright — priority is advisory, not a validation gate.
    """
    if value is None:
        return TaskPriority.medium
    if isinstance(value, TaskPriority):
        return value
    try:
        return TaskPriority(str(value).strip().lower())
    except ValueError:
        logger.warning("Unknown task priority %r; defaulting to medium", value)
        return TaskPriority.medium


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
    # Short human-readable key like "DEMO-12" (Jira-style: project prefix + number).
    # Assigned by the store on creation; the UUID stays the canonical id.
    key: Optional[str] = Field(default=None, description="Short Jira-style task key, e.g. DEMO-12")
    title: str
    description: str = ""

    # Tasks that must be completed before this one can be executed. A task with
    # unsatisfied dependencies is kept blocked and is released automatically
    # when the last dependency completes.
    depends: List[UUID] = Field(
        default_factory=list,
        description="IDs of tasks that must be completed before this task can run",
    )

    status: TaskStatus = Field(default=TaskStatus.todo)
    created_by: CreatedBy = Field(default=CreatedBy.user)
    # *Which* user filed it, as opposed to ``created_by``, which says what kind
    # of actor did (a person, the orchestrator, an external system). Stamped by
    # the store from the request in flight; ``local`` outside AUTH_MODE=multi,
    # where there is exactly one operator. See common/identity.py.
    created_by_user: str = Field(
        default="local",
        description="Id of the user who created the task ('local' in single-operator modes)",
    )

    parent_id: Optional[UUID] = None
    sequence_id: Optional[str] = None
    order: Optional[int] = None
    blocked_reason: Optional[str] = None
    # Set when the assigned agent paused the task to ask the user a question
    # (status == awaiting_input). Shape: {question, choices, agent_id, run_id,
    # asked_at}. Cleared when the user answers and the task resumes.
    pending_question: Optional[Dict[str, Any]] = None
    # Set when the assigned agent stopped on a tool call that needs a human's
    # approval (status == awaiting_approval). Shape: {tool, input, reason,
    # run_id, agent_id, hook, fingerprint, asked_at}. Cleared when the operator
    # approves or denies and the task resumes. See agents/hooks.py.
    pending_approval: Optional[Dict[str, Any]] = None
    # Tool calls the user approved but that have not been made yet: one entry
    # per approval, {tool, fingerprint, note, approved_at}. The resumed run
    # consumes the matching entry when it repeats the call, so an approval is
    # good for exactly that call, once, and not for the tool in general.
    approved_calls: List[Dict[str, Any]] = Field(default_factory=list)
    should_decompose: bool = Field(default=False, description="Whether to automatically decompose this task")

    # Number of automatic retries already spent on this task after run failures.
    # Compared against the workspace's orchestrator ``max_retries`` before
    # re-dispatching a failed run instead of blocking it (see run_manager).
    retry_count: int = Field(default=0, description="Automatic retries spent after failed runs")

    # Number of fix->review cycles already started for this task. Incremented
    # each time a review run starts and compared against
    # managers.runs.task_finalize.MAX_REVIEW_CYCLES; the task is blocked for
    # the user instead of starting another review once it is reached.
    review_cycles: int = Field(default=0, description="Review cycles already started for this task")

    priority: TaskPriority = Field(default=TaskPriority.medium, description="Task priority: low, medium, high, critical")

    # Optional deadline. Naive values are assumed UTC (see the validator below).
    due_at: Optional[datetime] = Field(default=None, description="Optional deadline for the task")

    @field_validator("priority", mode="before")
    @classmethod
    def _coerce_priority(cls, v):
        return coerce_priority(v)

    @field_validator("due_at")
    @classmethod
    def _assume_utc(cls, v):
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

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

    # Origin issue when imported from a git provider (GitHub/GitLab):
    # {provider, remote_id, number, url, state, labels, synced_at}
    external_source: Optional[Dict[str, Any]] = Field(
        default=None, description="Source issue metadata when imported from a git provider"
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
    def overdue(self) -> bool:
        """True when due_at is past and the task is still active (not persisted)."""
        return is_overdue(self.due_at, self.status)

    @property
    def agent_state(self) -> AgentState:
        """Backward-compatible runtime state derived from assignment/run, not persisted."""
        if self.assigned_agent_type and not self.assigned_agent_run_id:
            return AgentState.pending_approval
        if self.assigned_agent_run_id:
            try:
                from managers import run_manager
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
