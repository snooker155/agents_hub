"""
Task management tools.

Provides tools for creating, updating, and managing tasks in the system.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator
from langchain_core.tools import tool
from common.entity_sink import record_entity
from common.workspace_context import (
    resolve_active_workspace,
    resolve_active_project,
    task_in_workspace,
    filter_tasks_for_workspace,
    filter_tasks_for_project,
)

from tasks.models import Actor, is_overdue
from tasks.service import (
    CreatedBy,
    IllegalTransition,
    Task,
    TaskStatus,
    create_task as svc_create_task,
    add_subtask as svc_add_subtask,
    get_task as svc_get_task,
    list_tasks as svc_list_tasks,
    update_task as svc_update_task,
    stop_task as svc_stop_task,
    block_task as svc_block_task,
    set_dependencies as svc_set_dependencies,
    find_task_by_key as svc_find_task_by_key,
    create_sequence as svc_create_sequence,
    get_task_result as svc_get_task_result,
)
from tasks.keys import looks_like_key
from workspace import create_workspace_folder as ws_create_workspace_folder

# -------------------- helpers --------------------

def _uuid_from_str(value: Optional[str]) -> Optional[UUID]:
    """Convert a task reference (UUID or Jira-style key) to the task's UUID.

    Accepts either a raw UUID or a short task key like "DEMO-12". Attempts to
    auto-correct UUIDs with misplaced hyphens (e.g. LLM output where a hyphen
    was dropped) by stripping all hyphens and reformatting as the standard
    8-4-4-4-12 layout when the hex content is exactly 32 chars.
    """
    if value is None:
        return None
    s = str(value).strip()
    try:
        return UUID(s)
    except Exception:
        if looks_like_key(s):
            t = svc_find_task_by_key(s)
            if t is not None:
                return t.id
            raise ValueError(f"No task found with key '{s}'.")
        hex_only = s.replace("-", "").replace(" ", "")
        if len(hex_only) == 32 and all(c in "0123456789abcdefABCDEF" for c in hex_only):
            return UUID(f"{hex_only[:8]}-{hex_only[8:12]}-{hex_only[12:16]}-{hex_only[16:20]}-{hex_only[20:]}")
        raise ValueError(
            f"Invalid task ID '{s}'. Use the exact Task ID or task key (e.g. DEMO-12) from your context."
        )


def _task_to_dict(t: Task) -> Dict[str, Any]:
    """Convert Task to dict with normalized enums and UUIDs."""
    data = t.model_dump()

    # Overdue is derived from due_at + status, so compute it before either is
    # touched below (still real datetime/TaskStatus objects at this point).
    data["overdue"] = is_overdue(t.due_at, t.status)

    # Normalize enums
    if isinstance(data.get("status"), TaskStatus):
        data["status"] = data["status"].value
    if isinstance(data.get("created_by"), CreatedBy):
        data["created_by"] = data["created_by"].value
    if hasattr(data.get("priority"), "value"):
        data["priority"] = data["priority"].value

    # UUID to str
    for key in ("id", "parent_id"):
        if data.get(key) is not None:
            data[key] = str(data[key])
    if data.get("depends"):
        data["depends"] = [str(d) for d in data["depends"]]

    # Datetime to ISO
    for ts in ("created_at", "updated_at", "due_at"):
        if data.get(ts) is not None:
            try:
                data[ts] = data[ts].isoformat()
            except Exception:
                pass

    return data


def _record_task(task: Optional[Task], action: str) -> None:
    """Report a task this run touched, so the chat reply can link to its page.

    No-ops outside a chat run (see ``common.entity_sink``); ``task`` is allowed
    to be None so callers can pass a store lookup result straight through.
    """
    if task is None:
        return
    record_entity("task", str(task.id), action, (task.title or "").strip())


def _json_ok(payload: Dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2)


def _json_err(message: str, *, code: str = "bad_request", extra: Optional[Dict[str, Any]] = None) -> str:
    body: Dict[str, Any] = {"ok": False, "error": message, "code": code}
    if extra:
        body.update(extra)
    return json.dumps(body, ensure_ascii=False, indent=2)


def _active_workspace(explicit: Optional[str] = None) -> Optional[str]:
    return resolve_active_workspace(explicit)


def _inherit_project(parent_id: Optional[UUID]) -> tuple[Optional[str], Optional[str]]:
    """Resolve the (project, project_id) a newly created task should inherit.

    A task created while another task is being processed must belong to the same
    project as that task. Source priority:
      1. the explicit parent task — a child belongs to its parent's project;
      2. the task this agent is currently processing (``current_task_id``);
      3. the active project scope (chat with a project selected).
    Returns (project_name, project_id); either may be None when no project applies.
    """
    # 1. Explicit parent task wins.
    if parent_id is not None:
        parent = svc_get_task(parent_id)
        if parent is not None:
            return getattr(parent, "project", None), getattr(parent, "project_id", None)
    # 2. The task currently being processed by this agent.
    from common.agent_context import current_task_id
    active_tid = current_task_id.get()
    if active_tid:
        try:
            current = svc_get_task(_uuid_from_str(active_tid))
        except Exception:
            current = None
        if current is not None:
            return getattr(current, "project", None), getattr(current, "project_id", None)
    # 3. Active project scope (no controlling task in context).
    return None, resolve_active_project()



# -------------------- Tool Schemas --------------------

class CreateTaskInput(BaseModel):
    title: str = Field(..., min_length=1)
    description: str = ""
    created_by: CreatedBy = CreatedBy.user
    parent_id: Optional[str] = Field(None, description="UUID or key of parent task")
    status: TaskStatus = TaskStatus.todo
    workspace: Optional[str] = None
    depends: Optional[List[str]] = Field(
        None,
        description="Task IDs or keys (e.g. DEMO-12) that must be completed before this task can run",
    )
    due_at: Optional[datetime] = Field(
        None, description="Optional deadline, ISO 8601 (e.g. 2026-01-31T17:00:00Z). Assumed UTC if no timezone."
    )

    @field_validator("parent_id")
    @classmethod
    def _validate_parent(cls, v):
        if v is None or v == "":
            return None
        return str(_uuid_from_str(v))

    @field_validator("depends")
    @classmethod
    def _validate_depends(cls, v):
        if v is None:
            return None
        return [str(_uuid_from_str(s)) for s in v]


@tool("create_task", args_schema=CreateTaskInput)
def create_task(
    title: str,
    description: str = "",
    created_by: CreatedBy = CreatedBy.user,
    parent_id: Optional[str] = None,
    status: TaskStatus = TaskStatus.todo,
    workspace: Optional[str] = None,
    depends: Optional[List[str]] = None,
    due_at: Optional[datetime] = None,
) -> str:
    """Create a new task in the shared task tracker. Returns JSON with the created task.

    Use this whenever the user wants to create, add, or track a task — including
    turning a note, idea, or message into a task. Do NOT represent tasks as memory
    slots or notes; the tracker is for actionable work, memory is for facts to recall.

    Pass `depends` (task IDs or keys) when this task must wait for other tasks:
    it is created blocked and released back to todo once they are all done.
    Pass `due_at` to set a deadline.
    """
    try:
        ws_name = workspace
        if workspace:
            ws_name = ws_create_workspace_folder(workspace).name
        else:
            active_ws = _active_workspace()
            if active_ws:
                ws_name = ws_create_workspace_folder(active_ws).name
        pid = _uuid_from_str(parent_id)
        # Inherit the project of the controlling task so tasks created while
        # processing a project's task stay related to that project.
        proj_name, proj_id = _inherit_project(pid)
        task = svc_create_task(
            title=title,
            description=description,
            created_by=created_by,
            parent_id=pid,
            status=status,
            workspace=ws_name,
            project=proj_name,
            project_id=proj_id,
            depends=[_uuid_from_str(d) for d in depends] if depends else None,
            due_at=due_at,
        )
        _record_task(task, "created")
        return _json_ok({"task": _task_to_dict(task)})
    except Exception as e:
        return _json_err(f"Failed to create task: {e}")


class AddSubtaskInput(BaseModel):
    parent_id: str = Field(..., description="UUID or key of the parent task")
    title: str = Field(..., min_length=1)
    description: str = ""
    depends: Optional[List[str]] = Field(
        None,
        description="Task IDs or keys of subtasks that must be completed before this one",
    )

    @field_validator("parent_id")
    @classmethod
    def _valid_uuid(cls, v):
        return str(_uuid_from_str(v))

    @field_validator("depends")
    @classmethod
    def _validate_depends(cls, v):
        if v is None:
            return None
        return [str(_uuid_from_str(s)) for s in v]


@tool("add_subtask", args_schema=AddSubtaskInput)
def add_subtask(parent_id: str, title: str, description: str = "", depends: Optional[List[str]] = None) -> str:
    """Create a subtask under the given parent in the task tracker. Returns JSON with the created task.

    Use this to break a task into actionable steps. Do NOT store subtasks in memory.
    Pass `depends` (task IDs or keys) to enforce execution order between subtasks:
    a subtask with unfinished dependencies stays blocked and is released back to
    todo when they are all done.
    """
    try:
        task = svc_add_subtask(
            parent_id=_uuid_from_str(parent_id),
            title=title,
            description=description,
            depends=[_uuid_from_str(d) for d in depends] if depends else None,
        )
        _record_task(task, "created")
        return _json_ok({"task": _task_to_dict(task)})
    except Exception as e:
        return _json_err(f"Failed to add subtask: {e}")


class IdInput(BaseModel):
    id: str = Field(..., description="UUID or key (e.g. DEMO-12) of the task")

    @field_validator("id")
    @classmethod
    def _valid_uuid(cls, v):
        return str(_uuid_from_str(v))


@tool("get_task", args_schema=IdInput)
def get_task(id: str) -> str:
    """Get a task by id. Returns JSON with task or not_found."""
    try:
        tid = _uuid_from_str(id)
        task = svc_get_task(tid)
        if not task:
            return _json_err("Task not found", code="not_found", extra={"id": id})
        ws = _active_workspace()
        if ws and not task_in_workspace(task, ws):
            return _json_err(
                f"Task '{id}' is outside the active workspace '{ws}'",
                code="forbidden",
            )
        _record_task(task, "viewed")
        return _json_ok({"task": _task_to_dict(task)})
    except Exception as e:
        return _json_err(f"Failed to get task: {e}")


@tool("list_tasks")
def list_tasks() -> str:
    """List tasks in compact form (bounded output) to avoid oversized tool payloads."""
    try:
        ws = _active_workspace()
        tasks = filter_tasks_for_workspace(svc_list_tasks(), ws)
        # When a project is selected (chat scope), narrow within the workspace to
        # that project's tasks so the agent doesn't see the whole workspace.
        project_id = resolve_active_project()
        if project_id:
            tasks = filter_tasks_for_project(tasks, project_id)

        # Deterministic order: most recently updated first.
        def _sort_key(t: Task):
            upd = getattr(t, "updated_at", None)
            cre = getattr(t, "created_at", None)
            return ((upd.isoformat() if upd else ""), (cre.isoformat() if cre else ""), str(getattr(t, "id", "")))

        tasks_sorted = sorted(tasks, key=_sort_key, reverse=True)
        total_count = len(tasks_sorted)
        max_items = 40
        selected = tasks_sorted[:max_items]
        # print(f"Total tasks: {total_count}, returning {len(selected)} (max {max_items}) for workspace '{ws}'")

        compact = []
        for t in selected:
            d = _task_to_dict(t)
            compact.append({
                "id": d.get("id"),
                "key": d.get("key"),
                "title": d.get("title"),
                "status": d.get("status"),
                "workspace": d.get("workspace"),
                "parent_id": d.get("parent_id"),
                "depends": d.get("depends") or [],
                "blocked_reason": d.get("blocked_reason"),
                "assigned_agent_type": d.get("assigned_agent_type"),
                "agent_state": d.get("agent_state"),
                "updated_at": d.get("updated_at"),
            })

        by_status = {}
        for t in tasks_sorted:
            s = str(getattr(t, "status", ""))
            by_status[s] = by_status.get(s, 0) + 1

        return _json_ok({
            "workspace": ws or "default",
            "project_id": project_id,
            "total_count": total_count,
            "returned_count": len(compact),
            "truncated": total_count > len(compact),
            "by_status": by_status,
            "tasks": compact,
        })
    except Exception as e:
        return _json_err(f"Failed to list tasks: {e}")

class UpdateTaskInput(BaseModel):
    id: str = Field(..., description="UUID or key of the task to update")
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    blocked_reason: Optional[str] = None
    parent_id: Optional[str] = None
    sequence_id: Optional[str] = None
    order: Optional[int] = Field(None, ge=0)
    created_by: Optional[CreatedBy] = None
    workspace: Optional[str] = Field(
        default=None,
        description="Optional new workspace (path or name); only NAME will be stored",
    )
    depends: Optional[List[str]] = Field(
        None,
        description="Replace the task's dependency list with these task IDs or keys (empty list clears it)",
    )
    due_at: Optional[datetime] = Field(
        None, description="Deadline, ISO 8601 (e.g. 2026-01-31T17:00:00Z). Assumed UTC if no timezone."
    )

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v):
        return str(_uuid_from_str(v))

    @field_validator("parent_id")
    @classmethod
    def _valid_parent(cls, v):
        if v is None:
            return v
        return str(_uuid_from_str(v))

    @field_validator("depends")
    @classmethod
    def _valid_depends(cls, v):
        if v is None:
            return None
        return [str(_uuid_from_str(s)) for s in v]

    @model_validator(mode="after")
    def _at_least_one_field(self):
        fields = [
            self.title,
            self.description,
            self.status,
            self.blocked_reason,
            self.parent_id,
            self.sequence_id,
            self.order,
            self.created_by,
            self.workspace,
            self.depends,
            self.due_at,
        ]
        if all(v is None for v in fields):
            raise ValueError("No fields to update provided")
        return self


@tool("update_task", args_schema=UpdateTaskInput)
def update_task(
    id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    status: Optional[TaskStatus] = None,
    blocked_reason: Optional[str] = None,
    parent_id: Optional[str] = None,
    sequence_id: Optional[str] = None,
    order: Optional[int] = None,
    created_by: Optional[CreatedBy] = None,
    workspace: Optional[str] = None,
    depends: Optional[List[str]] = None,
    due_at: Optional[datetime] = None,
) -> str:
    """Update task fields. Returns JSON with the updated task.

    Setting `depends` replaces the task's dependency list; the task is blocked
    while any dependency is not yet done and released back to todo when all
    dependencies are done.

    An agent may only move a task: todo -> ready, todo/ready -> in_progress
    (picking up its own work), in_progress -> resolved, in_progress -> blocked
    (give a reason), and blocked -> in_progress. Other statuses (done,
    reviewed, reviewing, awaiting_input, awaiting_approval, stopped, pending)
    are set by the system, not by an agent's update_task call.
    """
    try:
        tid = _uuid_from_str(id)
        existing = svc_get_task(tid)
        if not existing:
            return _json_err("Task not found", code="not_found", extra={"id": id})
        ws = _active_workspace()
        if ws and not task_in_workspace(existing, ws):
            return _json_err(
                f"Task '{id}' is outside the active workspace '{ws}'",
                code="forbidden",
            )
        fields: Dict[str, Any] = {}
        if title is not None:
            fields["title"] = title
        if description is not None:
            fields["description"] = description
        if status is not None:
            fields["status"] = status
        if blocked_reason is not None:
            fields["blocked_reason"] = blocked_reason
        if parent_id is not None:
            fields["parent_id"] = _uuid_from_str(parent_id)
        if sequence_id is not None:
            fields["sequence_id"] = sequence_id
        if order is not None:
            fields["order"] = int(order)
        if created_by is not None:
            fields["created_by"] = created_by
        if workspace is not None:
            try:
                # Accept path or name; keep only the name and ensure it exists
                from pathlib import Path as _Path
                name = _Path(workspace).name
                p = ws_create_workspace_folder(name)
                fields["workspace"] = p.name
            except Exception as e:
                return _json_err(f"Failed to set workspace '{workspace}': {e}")
        if depends is not None:
            fields["depends"] = [_uuid_from_str(d) for d in depends]
        if due_at is not None:
            fields["due_at"] = due_at

        updated = svc_update_task(tid, actor=Actor.agent, **fields)
        if not updated:
            return _json_err("Task not found", code="not_found", extra={"id": id})
        _record_task(updated, "updated")
        return _json_ok({"task": _task_to_dict(updated)})
    except IllegalTransition as e:
        return _json_err(str(e), code="illegal_transition")
    except Exception as e:
        return _json_err(f"Failed to update task: {e}")


class StopTaskInput(BaseModel):
    id: str

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v):
        return str(_uuid_from_str(v))


@tool("stop_task", args_schema=StopTaskInput)
def stop_task(id: str) -> str:
    """Set task status to stopped. Returns JSON with updated task."""
    try:
        tid = _uuid_from_str(id)
        existing = svc_get_task(tid)
        if not existing:
            return _json_err("Task not found", code="not_found", extra={"id": id})
        ws = _active_workspace()
        if ws and not task_in_workspace(existing, ws):
            return _json_err(
                f"Task '{id}' is outside the active workspace '{ws}'",
                code="forbidden",
            )
        updated = svc_stop_task(tid)
        if not updated:
            return _json_err("Task not found", code="not_found", extra={"id": id})
        _record_task(updated, "stopped")
        return _json_ok({"task": _task_to_dict(updated)})
    except Exception as e:
        return _json_err(f"Failed to stop task: {e}")


class BlockTaskInput(BaseModel):
    id: str
    reason: str = Field(..., min_length=1)

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v):
        return str(_uuid_from_str(v))


@tool("block_task", args_schema=BlockTaskInput)
def block_task(id: str, reason: str) -> str:
    """Block a task with a reason. Returns JSON with updated task."""
    try:
        tid = _uuid_from_str(id)
        existing = svc_get_task(tid)
        if not existing:
            return _json_err("Task not found", code="not_found", extra={"id": id})
        ws = _active_workspace()
        if ws and not task_in_workspace(existing, ws):
            return _json_err(
                f"Task '{id}' is outside the active workspace '{ws}'",
                code="forbidden",
            )
        updated = svc_block_task(tid, reason)
        if not updated:
            return _json_err("Task not found", code="not_found", extra={"id": id})
        _record_task(updated, "blocked")
        return _json_ok({"task": _task_to_dict(updated)})
    except Exception as e:
        return _json_err(f"Failed to block task: {e}")


class SetDependenciesInput(BaseModel):
    id: str = Field(..., description="UUID or key of the task")
    depends: List[str] = Field(
        ..., description="Task IDs or keys this task must wait for (empty list clears all dependencies)"
    )

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v):
        return str(_uuid_from_str(v))

    @field_validator("depends")
    @classmethod
    def _valid_depends(cls, v):
        return [str(_uuid_from_str(s)) for s in v]


@tool("set_task_dependencies", args_schema=SetDependenciesInput)
def set_task_dependencies(id: str, depends: List[str]) -> str:
    """Set which tasks must be done before this task can run.

    Replaces the task's dependency list. While any dependency is not yet done
    the task stays blocked; when the last one reaches done, the task is
    automatically released back to `todo`. Pass an empty list to clear all
    dependencies (unblocking the task if it was dependency-blocked).
    """
    try:
        tid = _uuid_from_str(id)
        existing = svc_get_task(tid)
        if not existing:
            return _json_err("Task not found", code="not_found", extra={"id": id})
        ws = _active_workspace()
        if ws and not task_in_workspace(existing, ws):
            return _json_err(
                f"Task '{id}' is outside the active workspace '{ws}'",
                code="forbidden",
            )
        updated = svc_set_dependencies(tid, [_uuid_from_str(d) for d in depends])
        if not updated:
            return _json_err("Task not found", code="not_found", extra={"id": id})
        fresh = svc_get_task(tid) or updated
        _record_task(fresh, "updated")
        return _json_ok({"task": _task_to_dict(fresh)})
    except Exception as e:
        return _json_err(f"Failed to set dependencies: {e}")


class CreateSequenceInput(BaseModel):
    task_ids: List[str] = Field(..., min_length=1, description="List of UUIDs")
    sequence_id: Optional[str] = None
    start_order: int = Field(1, ge=0)

    @field_validator("task_ids")
    @classmethod
    def _validate_ids(cls, v):
        if not v:
            raise ValueError("task_ids must not be empty")
        return [str(_uuid_from_str(s)) for s in v]


@tool("create_sequence", args_schema=CreateSequenceInput)
def create_sequence(task_ids: List[str], sequence_id: Optional[str] = None, start_order: int = 1) -> str:
    """Assign a common sequence_id and order to given task IDs. Returns JSON with sequence info and tasks."""
    try:
        uuids = [UUID(s) for s in task_ids]
        ws = _active_workspace()
        if ws:
            for tid in uuids:
                t = svc_get_task(tid)
                if t is None:
                    return _json_err(str(tid), code="not_found")
                if not task_in_workspace(t, ws):
                    return _json_err(
                        f"Task '{tid}' is outside the active workspace '{ws}'",
                        code="forbidden",
                    )
        seq_id = svc_create_sequence(uuids, sequence_id=sequence_id, start_order=start_order)
        # Fetch updated tasks for response
        tasks = []
        for tid in uuids:
            t = svc_get_task(tid)
            if t:
                _record_task(t, "updated")
                tasks.append(_task_to_dict(t))
        return _json_ok({"sequence_id": seq_id, "tasks": tasks})
    except ValueError as e:
        return _json_err(str(e), code="not_found")
    except Exception as e:
        return _json_err(f"Failed to create sequence: {e}")


class GetTaskResultInput(BaseModel):
    task_id: str = Field(..., description="UUID of the task whose result to retrieve")


@tool("get_task_result", args_schema=GetTaskResultInput)
def get_task_result(task_id: str) -> str:
    """Get the output produced by the agent that last ran on a task.

    Use this to read the result of a completed task before assigning the next
    agent in a chain, so the next agent receives the previous agent's output
    as part of its description.

    Returns JSON with the result text, or an error if no result exists yet.
    """
    try:
        tid = _uuid_from_str(task_id)
        if tid is None:
            return _json_err("Invalid task_id", code="bad_request")
        result = svc_get_task_result(tid)
        if result is None:
            return _json_err("No result found for this task", code="not_found")
        _record_task(svc_get_task(tid), "viewed")
        return _json_ok({"task_id": task_id, "result": result})
    except Exception as e:
        return _json_err(f"Failed to get task result: {e}")


__all__ = [
    "create_task",
    "add_subtask",
    "get_task",
    "list_tasks",
    "update_task",
    "stop_task",
    "block_task",
    "set_task_dependencies",
    "create_sequence",
    "get_task_result",
]
