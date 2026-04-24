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
- Agent management: assign_agent, clear_agent

All functions use the module-level default_store (shared tasks/storage), but accept an optional
store argument for injection/testing.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID, uuid4

from tasks import (
    CreatedBy,
    Task,
    TaskStatus,
)
from tasks.storage import (
    TaskStore,
    get_activity_log as _get_activity_log,
    append_activity_log as _append_activity_log,
    delete_activity_log as _delete_activity_log,
    get_task_execution_log as _get_task_execution_log,
    upsert_task_execution_log_entry as _upsert_task_execution_log_entry,
    delete_task_execution_log as _delete_task_execution_log,
    get_task_results as _get_task_results,
    get_task_result as _get_task_result,
    get_task_result_files as _get_task_result_files,
    set_task_result as _set_task_result,
    delete_task_result as _delete_task_result,
    get_routing_log as _get_routing_log,
    append_routing_log as _append_routing_log,
)
from common.config import settings
from datetime import datetime, timezone
from common.paths import TASKS_FILE as DEFAULT_TASKS_FILE

# Default store points to the shared tasks file under .agents_hub, unless overridden via settings/.env.
TASKS_FILE = getattr(settings, "tasks_file", str(DEFAULT_TASKS_FILE)) or str(DEFAULT_TASKS_FILE)

default_store = TaskStore(TASKS_FILE)


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
    project: Optional[str] = None,
    should_decompose: bool = False,
    store: TaskStore = default_store,
) -> Task:
    """Create a new task and persist it in the store."""
    task = store.create(
        title=title,
        description=description,
        created_by=created_by,
        parent_id=parent_id,
        sequence_id=sequence_id,
        order=order,
        blocked_reason=blocked_reason,
        status=status,
        workspace=workspace,
        project=project,
        should_decompose=should_decompose,
    )
    append_task_activity_log(task.id, "created", "Task created")
    return task


def get_task(task_id: UUID, *, store: TaskStore = default_store) -> Optional[Task]:
    """Return a task by id or None if not found."""
    return store.get(task_id)


def list_tasks(*, store: TaskStore = default_store) -> List[Task]:
    """Return all tasks from the store."""
    return store.list()


def update_task(task_id: UUID, *, store: TaskStore = default_store, **fields) -> Optional[Task]:
    """Update fields of a given task. Returns updated task or None if not found."""
    fields.pop("id", None)

    if "status" in fields:
        new_status = fields["status"]
        if not isinstance(new_status, TaskStatus):
            new_status = TaskStatus(new_status)
            fields["status"] = new_status

        task = store.get(task_id)
        if task and task.status != new_status:
            # Clear blocked_reason when leaving blocked
            if new_status != TaskStatus.blocked and "blocked_reason" not in fields:
                fields["blocked_reason"] = None

            # Only fully reset when moving back to todo (not resolved, done, ready, etc.)
            _RESET_STATUSES = {TaskStatus.todo}

            # Clear agent assignment fields only when resetting to todo
            _CLEAR_AGENT_STATUSES = {TaskStatus.todo}

            # Clear agent assignment when resetting to todo (preserve routing history for reviewed/done)
            if new_status in _CLEAR_AGENT_STATUSES:
                for key in ("assigned_agent_type", "assigned_agent_params", "assigned_agent_run_id", "pre_assignment_status"):
                    if key not in fields:
                        fields[key] = None
            # Only clear session on full todo reset (keep session history for reviewed/done)
            if new_status in _RESET_STATUSES:
                if "session_id" not in fields:
                    fields["session_id"] = None

            # Clear results and execution log only when explicitly moving to todo
            if new_status in _RESET_STATUSES:
                try:
                    delete_task_result_file(task_id)
                except Exception:
                    pass
                try:
                    delete_task_execution_log(task_id)
                except Exception:
                    pass

    # Load current task to detect changes for activity log
    current = store.get(task_id)
    updated = store.update(task_id, **fields)
    if updated and current:
        if "status" in fields and str(fields["status"]) != str(current.status):
            append_task_activity_log(
                task_id,
                "status_change",
                f"Status: {current.status} → {fields['status']}",
                **{"from": str(current.status), "to": str(fields["status"])},
            )
        new_agent = fields.get("assigned_agent_type")
        old_agent = current.assigned_agent_type
        if new_agent and new_agent != old_agent:
            append_task_activity_log(task_id, "agent_assigned", f"Agent assigned: {new_agent}", agent=new_agent)
        elif old_agent and new_agent is None and "assigned_agent_type" in fields:
            append_task_activity_log(task_id, "agent_cleared", "Agent assignment cleared")
    return updated


def delete_task(task_id: UUID, *, cascade: bool = False, store: TaskStore = default_store) -> int:
    """Delete a task by id. Returns the number of deleted tasks."""
    if cascade:
        # Collect all descendant IDs before deletion to clean up sidecars
        tasks = store.list()
        to_delete = {str(task_id)}
        stack = [str(task_id)]
        while stack:
            parent = stack.pop()
            for t in tasks:
                if t.parent_id and str(t.parent_id) == parent:
                    child_id = str(t.id)
                    if child_id not in to_delete:
                        to_delete.add(child_id)
                        stack.append(child_id)
        deleted = store.delete(task_id, cascade=True)
        for tid in to_delete:
            delete_task_activity_log(_uuid_from_str(tid))
            delete_task_result_file(_uuid_from_str(tid))
            delete_task_execution_log(_uuid_from_str(tid))
        return deleted
    deleted = store.delete(task_id, cascade=False)
    if deleted:
        delete_task_activity_log(task_id)
        delete_task_result_file(task_id)
        delete_task_execution_log(task_id)
    return deleted


def _uuid_from_str(v) -> UUID:
    import os as _os
    from uuid import UUID as _UUID
    if isinstance(v, _UUID):
        return v
    s = str(v).strip()
    try:
        return _UUID(s)
    except ValueError:
        # Try to recover: strip all hyphens and reformat as 8-4-4-4-12
        hex_only = s.replace("-", "").replace(" ", "")
        if len(hex_only) == 32 and all(c in "0123456789abcdefABCDEF" for c in hex_only):
            return _UUID(f"{hex_only[:8]}-{hex_only[8:12]}-{hex_only[12:16]}-{hex_only[16:20]}-{hex_only[20:]}")
        # Fall back to the env-injected task ID (set by node_runner for orchestrator runs).
        # Small models sometimes truncate UUIDs; this lets the tools recover silently.
        env_id = _os.environ.get("AGENT_TASK_ID", "").strip()
        if env_id:
            try:
                return _UUID(env_id)
            except ValueError:
                pass
        raise ValueError(
            f"Invalid task ID '{s}'. Use the exact Task ID from your context without modification."
        )


# -------------------- Utilities --------------------

def add_subtask(
    parent_id: UUID,
    title: str,
    description: str = "",
    *,
    store: TaskStore = default_store,
) -> Task:
    """Create a subtask under the given parent with created_by=orchestrator.

    The subtask inherits workspace, project, and project_id from its parent task.
    """
    parent = store.get(parent_id)
    workspace = parent.workspace if parent else None
    project = parent.project if parent else None
    project_id = parent.project_id if parent else None

    return store.create(
        title=title,
        description=description,
        created_by=CreatedBy.orchestrator,
        parent_id=parent_id,
        workspace=workspace,
        project=project,
        project_id=project_id,
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


# -------------------- Activity log --------------------

def _tasks_path():
    from pathlib import Path
    return Path(TASKS_FILE)


def get_task_activity_log(task_id: UUID) -> list:
    """Return activity log entries for a task."""
    return _get_activity_log(_tasks_path(), str(task_id))


def append_task_activity_log(task_id: UUID, entry_type: str, message: str, **extra) -> None:
    """Append an activity log entry for a task."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": entry_type,
        "message": message,
        **extra,
    }
    _append_activity_log(_tasks_path(), str(task_id), entry)


def delete_task_activity_log(task_id: UUID) -> None:
    """Delete the activity log for a task."""
    _delete_activity_log(_tasks_path(), str(task_id))


# -------------------- Routing log --------------------

def get_routing_log(workspace: Optional[str] = None) -> list:
    """Return all orchestrator routing decisions, newest-first."""
    return _get_routing_log(workspace=workspace)


def append_routing_log_entry(
    task_id: UUID,
    task_title: str,
    agent_id: str,
    reason: Optional[str],
    workspace: Optional[str],
) -> None:
    """Record a single orchestrator routing decision."""
    from uuid import uuid4
    entry = {
        "id": str(uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task_id": str(task_id),
        "task_title": task_title,
        "agent_id": agent_id,
        "reason": reason or "",
        "workspace": workspace or "",
    }
    _append_routing_log(entry)


# -------------------- Execution log --------------------

def get_task_execution_log(task_id: UUID) -> list:
    """Return execution log entries for a task."""
    return _get_task_execution_log(_tasks_path(), str(task_id))


def upsert_task_execution_log_entry(task_id: UUID, run_id: str, **fields) -> None:
    """Insert or update a task execution log entry for a specific run."""
    _upsert_task_execution_log_entry(_tasks_path(), str(task_id), run_id, **fields)


def delete_task_execution_log(task_id: UUID) -> None:
    """Delete the execution log for a task."""
    _delete_task_execution_log(_tasks_path(), str(task_id))


# -------------------- Result --------------------

def get_task_results(task_id: UUID) -> list:
    """Return all result entries for a task (one per agent run)."""
    return _get_task_results(_tasks_path(), str(task_id))


def get_task_result(task_id: UUID) -> Optional[str]:
    """Return the latest result text for a task (backward-compat)."""
    return _get_task_result(_tasks_path(), str(task_id))


def get_task_result_files(task_id: UUID) -> list:
    """Return the files from the latest result entry."""
    return _get_task_result_files(_tasks_path(), str(task_id))


def set_task_result(
    task_id: UUID,
    result: str,
    files: Optional[list] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> None:
    """Upsert a result entry for a task, keyed by run_id."""
    _set_task_result(_tasks_path(), str(task_id), result, files=files, run_id=run_id, agent_id=agent_id)


def delete_task_result_file(task_id: UUID) -> None:
    """Delete the result file for a task."""
    _delete_task_result(_tasks_path(), str(task_id))


# -------------------- Agent management --------------------

def assign_agent(
    task_id: UUID,
    agent_type: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    store: TaskStore = default_store,
    run_id: Optional[str] = None,
) -> Optional[Task]:
    """Assign an agent to the task.

    - agent_type: string identifier of the agent
    - params: arbitrary dict with agent configuration
    - run_id: optional external run identifier
    """
    fields: Dict[str, Any] = {
        "assigned_agent_type": agent_type,
        "assigned_agent_params": params if params is not None else None,
        "assigned_agent_run_id": run_id,
    }
    # Capture the current status so rejection can restore it
    current = store.get(task_id)
    if current and current.pre_assignment_status is None:
        fields["pre_assignment_status"] = current.status
    return store.update(task_id, **fields)


def clear_agent(
    task_id: UUID,
    *,
    store: TaskStore = default_store,
) -> Optional[Task]:
    """Clear agent assignment."""
    fields: Dict[str, Any] = {
        "assigned_agent_type": None,
        "assigned_agent_params": None,
        "assigned_agent_run_id": None,
        "pre_assignment_status": None,
    }
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
    "delete_task",
    "add_subtask",
    "stop_task",
    "block_task",
    "create_sequence",
    "assign_agent",
    "clear_agent",
    "get_task_activity_log",
    "append_task_activity_log",
    "delete_task_activity_log",
    "get_routing_log",
    "append_routing_log_entry",
    "get_task_execution_log",
    "upsert_task_execution_log_entry",
    "delete_task_execution_log",
    "get_task_results",
    "get_task_result",
    "get_task_result_files",
    "set_task_result",
    "delete_task_result_file",
]
