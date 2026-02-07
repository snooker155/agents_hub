"""
Task service module built on top of the file-based TaskStore.

Provides high-level CRUD and utility operations:
- create_task
- get_task
- list_tasks
- update_task
- add_subtask (sets parent_id and created_by=orchestrator)
- stop_task (status=stopped)
- block_task (status=blocked with a reason)
- create_sequence (assign common sequence_id and incremental order for given task IDs)
- Agent management: assign_agent, clear_agent, set_agent_state

All functions use the module-level default_store (shared tasks/storage), but accept an optional
store argument for injection/testing.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID, uuid4

from domains.swe.orchestrator.tasks import (
    CreatedBy,
    AgentState,
    Task,
    TaskStatus,
)
from domains.swe.orchestrator.tasks.storage import TaskStore
from .config import get_settings

# Default store points to a single shared tasks file under the top-level tasks/ folder
default_store = TaskStore(get_settings().tasks_file or "tasks/tasks.json")


# -------------------- CRUD --------------------

def create_task(
    title: str,
    description: str = "",
    *,
    created_by: CreatedBy = CreatedBy.user,
    parent_id: Optional[UUID] = None,
    sequence_id: Optional[str] = None,
    order: Optional[int] = None,
    blocked_reason: Optional[str] = None,
    status: TaskStatus = TaskStatus.todo,
    workspace: Optional[str] = None,
    should_decompose: bool = False,
    store: TaskStore = default_store,
) -> Task:
    """Create a new task and persist it in the store."""
    return store.create(
        title=title,
        description=description,
        created_by=created_by,
        parent_id=parent_id,
        sequence_id=sequence_id,
        order=order,
        blocked_reason=blocked_reason,
        status=status,
        workspace=workspace,
        should_decompose=should_decompose,
    )


def get_task(task_id: UUID, *, store: TaskStore = default_store) -> Optional[Task]:
    """Return a task by id or None if not found."""
    return store.get(task_id)


def list_tasks(*, store: TaskStore = default_store) -> List[Task]:
    """Return all tasks from the store."""
    return store.list()


def update_task(task_id: UUID, *, store: TaskStore = default_store, **fields) -> Optional[Task]:
    """Update fields of a given task. Returns updated task or None if not found."""
    # Guard against attempts to overwrite immutable fields like id
    fields.pop("id", None)
    return store.update(task_id, **fields)


# -------------------- Utilities --------------------

def add_subtask(
    parent_id: UUID,
    title: str,
    description: str = "",
    *,
    store: TaskStore = default_store,
) -> Task:
    """Create a subtask under the given parent with created_by=orchestrator.
    
    The subtask inherits the workspace from its parent task.
    """
    parent = store.get(parent_id)
    workspace = parent.workspace if parent else None
    
    return store.create(
        title=title,
        description=description,
        created_by=CreatedBy.orchestrator,
        parent_id=parent_id,
        workspace=workspace,
    )


def stop_task(task_id: UUID, *, store: TaskStore = default_store) -> Optional[Task]:
    """Mark the task as stopped. Returns updated task or None if not found."""
    return store.update(task_id, status=TaskStatus.stopped)


def block_task(task_id: UUID, reason: str, *, store: TaskStore = default_store) -> Optional[Task]:
    """Mark the task as blocked with a given reason. Returns updated task or None."""
    return store.update(task_id, status=TaskStatus.blocked, blocked_reason=reason)


def create_sequence(
    task_ids: Sequence[UUID],
    *,
    store: TaskStore = default_store,
    sequence_id: Optional[str] = None,
    start_order: int = 1,
) -> str:
    """Assign a common sequence_id and increasing order to the provided task IDs.

    - If sequence_id is not provided, a new UUID string is generated.
    - Order starts from `start_order` (default 1) and increases by 1 following the
      order of task_ids.
    - Raises ValueError if any task is not found.

    Returns the sequence_id used.
    """
    seq_id = sequence_id or str(uuid4())

    # Load all tasks, verify existence and update in-memory; then save once.
    tasks = store.load()
    index = {t.id: t for t in tasks}

    missing = [tid for tid in task_ids if tid not in index]
    if missing:
        raise ValueError(f"Tasks not found for sequence: {', '.join(str(x) for x in missing)}")

    for order_value, tid in enumerate(task_ids, start=start_order):
        t = index[tid]
        # mutate fields
        t.sequence_id = seq_id
        t.order = int(order_value)
        t.touch()

    # Persist all tasks in one save call
    store.save(tasks)
    return seq_id


# -------------------- Agent management --------------------

def assign_agent(
    task_id: UUID,
    agent_type: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    store: TaskStore = default_store,
    run_id: Optional[str] = None,
) -> Optional[Task]:
    """Assign an agent to the task and set agent_state=assigned.

    - agent_type: string identifier of the agent
    - params: arbitrary dict with agent configuration
    - run_id: optional external run identifier
    """
    fields: Dict[str, Any] = {
        "assigned_agent_type": agent_type,
        "assigned_agent_params": params if params is not None else None,
        "assigned_agent_run_id": run_id,
        "agent_state": AgentState.assigned,
    }
    return store.update(task_id, **fields)


def clear_agent(
    task_id: UUID,
    *,
    store: TaskStore = default_store,
) -> Optional[Task]:
    """Clear agent assignment and set agent_state=none."""
    fields: Dict[str, Any] = {
        "assigned_agent_type": None,
        "assigned_agent_params": None,
        "assigned_agent_run_id": None,
        "agent_state": AgentState.none,
    }
    return store.update(task_id, **fields)


def set_agent_state(
    task_id: UUID,
    state: AgentState,
    *,
    store: TaskStore = default_store,
    run_id: Optional[str] = None,
) -> Optional[Task]:
    """Update only the agent_state (and optionally run_id)."""
    fields: Dict[str, Any] = {"agent_state": state}
    if run_id is not None:
        fields["assigned_agent_run_id"] = run_id
    return store.update(task_id, **fields)


__all__ = [
    "Task",
    "TaskStatus",
    "CreatedBy",
    "TaskStore",
    "create_task",
    "get_task",
    "list_tasks",
    "update_task",
    "add_subtask",
    "stop_task",
    "block_task",
    "create_sequence",
    "assign_agent",
    "clear_agent",
    "set_agent_state",
]
