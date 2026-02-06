"""
Task service for swe_agent built on top of the shared file-based TaskStore.

Provides high-level CRUD and utility operations mirroring orchestrator.tasks_service.
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

# Use a single, centralized tasks file
default_store = TaskStore("tasks/tasks.json")


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
    store: TaskStore = default_store,
) -> Task:
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
    )


def get_task(task_id: UUID, *, store: TaskStore = default_store) -> Optional[Task]:
    return store.get(task_id)


def list_tasks(*, store: TaskStore = default_store) -> List[Task]:
    return store.list()


def update_task(task_id: UUID, *, store: TaskStore = default_store, **fields) -> Optional[Task]:
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
    return store.update(task_id, status=TaskStatus.stopped)


def block_task(task_id: UUID, reason: str, *, store: TaskStore = default_store) -> Optional[Task]:
    return store.update(task_id, status=TaskStatus.blocked, blocked_reason=reason)


def create_sequence(
    task_ids: Sequence[UUID],
    *,
    store: TaskStore = default_store,
    sequence_id: Optional[str] = None,
    start_order: int = 1,
) -> str:
    seq_id = sequence_id or str(uuid4())

    tasks = store.load()
    index = {t.id: t for t in tasks}

    missing = [tid for tid in task_ids if tid not in index]
    if missing:
        raise ValueError(f"Tasks not found for sequence: {', '.join(str(x) for x in missing)}")

    for order_value, tid in enumerate(task_ids, start=start_order):
        t = index[tid]
        t.sequence_id = seq_id
        t.order = int(order_value)
        t.touch()

    store.save(tasks)
    return seq_id


# -------------------- Agent assignment helpers --------------------

def assign_agent(
    task_id: UUID,
    agent_type: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    store: TaskStore = default_store,
    run_id: Optional[str] = None,
) -> Optional[Task]:
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
