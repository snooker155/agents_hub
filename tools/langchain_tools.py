"""
LangChain tools wrapping the orchestrator task service.

Provided tools (names):
- create_task
- add_subtask
- get_task
- list_tasks
- update_task
- stop_task
- block_task
- create_sequence

Agent management tools:
- list_agents_tool
- assign_and_start_agent_tool(task_id, agent_id, params_json)
- stop_agent_tool(task_id)
- get_agent_status_tool(task_id)

All tools validate inputs with Pydantic and return structured JSON strings.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, validator, model_validator
from langchain_core.tools import tool

# Local imports from the project
from common.tasks_service import (
    add_subtask as svc_add_subtask,
    block_task as svc_block_task,
    create_sequence as svc_create_sequence,
    create_task as svc_create_task,
    get_task as svc_get_task,
    list_tasks as svc_list_tasks,
    stop_task as svc_stop_task,
    update_task as svc_update_task,
    assign_agent as svc_assign_agent,
    set_agent_state as svc_set_agent_state,
)
from tasks import Task, TaskStatus, CreatedBy, AgentState
from common.workspace import create_workspace_folder as ws_create_workspace_folder
from agents.registry import (
    list_agents as reg_list_agents,
    get_agent as reg_get_agent,
)
from agents.run_manager import (
    start_run as rm_start_run,
    stop_run as rm_stop_run,
    get_status as rm_get_status,
)


# -------------------- helpers --------------------

def _uuid_from_str(value: Optional[str]) -> Optional[UUID]:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except Exception as e:
        raise ValueError(f"Invalid UUID: {value}") from e


def _task_to_dict(t: Task) -> Dict[str, Any]:
    # Compatible with pydantic v1/v2
    data = t.model_dump() if hasattr(t, "model_dump") else t.dict()
    # Normalize enums to their values
    if isinstance(data.get("status"), TaskStatus):
        data["status"] = data["status"].value
    if isinstance(data.get("created_by"), CreatedBy):
        data["created_by"] = data["created_by"].value
    # Normalize agent_state enum if present
    if "agent_state" in data and isinstance(data.get("agent_state"), AgentState):
        data["agent_state"] = data["agent_state"].value
    # UUID fields to str
    for key in ("id", "parent_id"):
        if data.get(key) is not None:
            data[key] = str(data[key])
    # Datetime to ISO strings
    for ts in ("created_at", "updated_at"):
        if data.get(ts) is not None:
            try:
                data[ts] = data[ts].isoformat()
            except Exception:
                pass
    return data


def _json_ok(payload: Dict[str, Any]) -> str:
    body = {"ok": True, **payload}
    return json.dumps(body, ensure_ascii=False, indent=2)


def _json_err(message: str, *, code: str = "bad_request", extra: Optional[Dict[str, Any]] = None) -> str:
    body: Dict[str, Any] = {"ok": False, "error": message, "code": code}
    if extra:
        body.update(extra)
    return json.dumps(body, ensure_ascii=False, indent=2)


# -------------------- input schemas --------------------

class CreateTaskInput(BaseModel):
    title: str = Field(..., min_length=1)
    description: str = ""
    created_by: CreatedBy = CreatedBy.user
    parent_id: Optional[str] = Field(None, description="UUID of parent task")
    status: TaskStatus = TaskStatus.todo
    workspace_name: Optional[str] = Field(
        None,
        description="Optional workspace NAME to create under global workspaces/ root.",
    )
    workspace: Optional[str] = Field(
        None,
        description="Optional path or name to an existing workspace directory (only NAME will be stored).",
    )

    @validator("parent_id")
    def _validate_parent(cls, v):
        if v is None or v == "":
            return None
        _uuid_from_str(v)  # will raise if invalid
        return v


@tool("create_task", args_schema=CreateTaskInput)
def create_task(
    title: str,
    description: str = "",
    created_by: CreatedBy = CreatedBy.user,
    parent_id: Optional[str] = None,
    status: TaskStatus = TaskStatus.todo,
    workspace_name: Optional[str] = None,
    workspace: Optional[str] = None,
) -> str:
    """Create a new task. Returns JSON with the created task."""
    try:
        # Resolve workspace and store only the NAME (not absolute path).
        ws_name: Optional[str] = None
        if workspace_name:
            try:
                p = ws_create_workspace_folder(workspace_name)
                ws_name = p.name
            except Exception as e:
                return _json_err(f"Failed to prepare workspace '{workspace_name}': {e}")
        elif workspace:
            try:
                # Accept path or name; keep only the terminal name and ensure exists
                from pathlib import Path as _Path
                name = _Path(workspace).name
                p = ws_create_workspace_folder(name)
                ws_name = p.name
            except Exception as e:
                return _json_err(f"Invalid workspace '{workspace}': {e}")
        else:
            # Auto-create a fresh workspace when not provided
            try:
                p = ws_create_workspace_folder()
                ws_name = p.name
            except Exception as e:
                return _json_err(f"Failed to auto-create workspace: {e}")

        task = svc_create_task(
            title=title,
            description=description,
            created_by=created_by,
            parent_id=_uuid_from_str(parent_id),
            status=status,
            workspace=ws_name,
        )
        return _json_ok({"task": _task_to_dict(task)})
    except Exception as e:
        return _json_err(f"Failed to create task: {e}")


class AddSubtaskInput(BaseModel):
    parent_id: str = Field(..., description="UUID of the parent task")
    title: str = Field(..., min_length=1)
    description: str = ""

    @validator("parent_id")
    def _valid_uuid(cls, v):
        _uuid_from_str(v)
        return v


@tool("add_subtask", args_schema=AddSubtaskInput)
def add_subtask(parent_id: str, title: str, description: str = "") -> str:
    """Create a subtask under the given parent. Returns JSON with the created task."""
    try:
        task = svc_add_subtask(
            parent_id=_uuid_from_str(parent_id),
            title=title,
            description=description,
        )
        return _json_ok({"task": _task_to_dict(task)})
    except Exception as e:
        return _json_err(f"Failed to add subtask: {e}")


class IdInput(BaseModel):
    id: str = Field(..., description="UUID of the task")

    @validator("id")
    def _valid_uuid(cls, v):
        _uuid_from_str(v)
        return v


@tool("get_task", args_schema=IdInput)
def get_task(id: str) -> str:
    """Get a task by id. Returns JSON with task or not_found."""
    try:
        tid = _uuid_from_str(id)
        task = svc_get_task(tid)
        if not task:
            return _json_err("Task not found", code="not_found", extra={"id": id})
        return _json_ok({"task": _task_to_dict(task)})
    except Exception as e:
        return _json_err(f"Failed to get task: {e}")


@tool("list_tasks")
def list_tasks() -> str:
    """List all tasks. Returns JSON with an array of tasks."""
    try:
        tasks = svc_list_tasks()
        return _json_ok({"tasks": [_task_to_dict(t) for t in tasks]})
    except Exception as e:
        return _json_err(f"Failed to list tasks: {e}")


class UpdateTaskInput(BaseModel):
    id: str = Field(..., description="UUID of the task to update")
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

    @validator("id")
    def _valid_id(cls, v):
        _uuid_from_str(v)
        return v

    @validator("parent_id")
    def _valid_parent(cls, v):
        if v is None:
            return v
        _uuid_from_str(v)
        return v

    @model_validator(mode="after")
    def _at_least_one_field(cls, values):
        fields = [
            values.get("title"),
            values.get("description"),
            values.get("status"),
            values.get("blocked_reason"),
            values.get("parent_id"),
            values.get("sequence_id"),
            values.get("order"),
            values.get("created_by"),
        ]
        if all(v is None for v in fields):
            raise ValueError("No fields to update provided")
        return values


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
) -> str:
    """Update task fields. Returns JSON with the updated task."""
    try:
        tid = _uuid_from_str(id)
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

        updated = svc_update_task(tid, **fields)
        if not updated:
            return _json_err("Task not found", code="not_found", extra={"id": id})
        return _json_ok({"task": _task_to_dict(updated)})
    except Exception as e:
        return _json_err(f"Failed to update task: {e}")


class StopTaskInput(BaseModel):
    id: str

    @validator("id")
    def _valid_id(cls, v):
        _uuid_from_str(v)
        return v


@tool("stop_task", args_schema=StopTaskInput)
def stop_task(id: str) -> str:
    """Set task status to stopped. Returns JSON with updated task."""
    try:
        tid = _uuid_from_str(id)
        updated = svc_stop_task(tid)
        if not updated:
            return _json_err("Task not found", code="not_found", extra={"id": id})
        return _json_ok({"task": _task_to_dict(updated)})
    except Exception as e:
        return _json_err(f"Failed to stop task: {e}")


class BlockTaskInput(BaseModel):
    id: str
    reason: str = Field(..., min_length=1)

    @validator("id")
    def _valid_id(cls, v):
        _uuid_from_str(v)
        return v


@tool("block_task", args_schema=BlockTaskInput)
def block_task(id: str, reason: str) -> str:
    """Block a task with a reason. Returns JSON with updated task."""
    try:
        tid = _uuid_from_str(id)
        updated = svc_block_task(tid, reason)
        if not updated:
            return _json_err("Task not found", code="not_found", extra={"id": id})
        return _json_ok({"task": _task_to_dict(updated)})
    except Exception as e:
        return _json_err(f"Failed to block task: {e}")


class CreateSequenceInput(BaseModel):
    task_ids: List[str] = Field(..., min_items=1, description="List of UUIDs")
    sequence_id: Optional[str] = None
    start_order: int = Field(1, ge=0)

    @validator("task_ids")
    def _validate_ids(cls, v):
        if not v:
            raise ValueError("task_ids must not be empty")
        for s in v:
            _uuid_from_str(s)
        return v


@tool("create_sequence", args_schema=CreateSequenceInput)
def create_sequence(task_ids: List[str], sequence_id: Optional[str] = None, start_order: int = 1) -> str:
    """Assign a common sequence_id and order to given task IDs. Returns JSON with sequence info and tasks."""
    try:
        uuids = [UUID(s) for s in task_ids]
        seq_id = svc_create_sequence(uuids, sequence_id=sequence_id, start_order=start_order)
        # Fetch updated tasks for response
        tasks = []
        for tid in uuids:
            t = svc_get_task(tid)
            if t:
                tasks.append(_task_to_dict(t))
        return _json_ok({"sequence_id": seq_id, "tasks": tasks})
    except ValueError as e:
        return _json_err(str(e), code="not_found")
    except Exception as e:
        return _json_err(f"Failed to create sequence: {e}")


# -------------------- Agent management tools --------------------

@tool("list_agents_tool")
def list_agents_tool() -> str:
    """List all available agents from the registry. Returns JSON with an array of agents."""
    try:
        specs = reg_list_agents()
        return _json_ok({"agents": [s.to_dict() for s in specs]})
    except Exception as e:
        return _json_err(f"Failed to list agents: {e}")


class AssignAndStartAgentInput(BaseModel):
    task_id: str = Field(..., description="UUID of the task")
    agent_id: str = Field(..., min_length=1, description="Agent identifier from the registry")
    params_json: Optional[str] = Field(
        None, description="Optional JSON object string with agent parameters"
    )

    @validator("task_id")
    def _valid_task_id(cls, v):
        _uuid_from_str(v)
        return v


@tool("assign_and_start_agent_tool", args_schema=AssignAndStartAgentInput)
def assign_and_start_agent_tool(task_id: str, agent_id: str, params_json: Optional[str] = None) -> str:
    """Assign an agent to a task and start its run.

    Returns JSON with the updated task, run status and agent info.
    """
    try:
        tid = _uuid_from_str(task_id)

        # Ensure task exists
        task = svc_get_task(tid)
        if not task:
            return _json_err("Task not found", code="not_found", extra={"task_id": task_id})

        # Ensure agent exists
        spec = reg_get_agent(agent_id)
        if not spec:
            return _json_err("Agent not found", code="not_found", extra={"agent_id": agent_id})

        # Policy: decomposer-capable agents may only be assigned to user-created tasks
        try:
            caps = list(getattr(spec, "capabilities", []) or [])
        except Exception:
            caps = []
        if "decompose" in caps:
            try:
                if getattr(task, "created_by", None) != CreatedBy.user:
                    return _json_err(
                        "Decomposer agent can only be assigned to user-created tasks",
                        code="invalid_task",
                    )
            except Exception:
                return _json_err(
                    "Decomposer agent can only be assigned to user-created tasks",
                    code="invalid_task",
                )

        # Parse params JSON if provided
        params: Optional[Dict[str, Any]] = None
        if params_json:
            try:
                obj = json.loads(params_json)
                if obj is not None and not isinstance(obj, dict):
                    return _json_err("params_json must be a JSON object", code="bad_params")
                params = obj
            except Exception as e:
                return _json_err(f"Invalid params_json: {e}", code="bad_params")

        # Start run first to obtain run_id
        run_id = rm_start_run(str(task.id), agent_id, params)

        # Record assignment and mark as running
        svc_assign_agent(task.id, agent_id, params, run_id=run_id)
        svc_set_agent_state(task.id, AgentState.running, run_id=run_id)

        updated = svc_get_task(task.id)
        status = rm_get_status(str(task.id))

        payload: Dict[str, Any] = {
            "message": "Agent assigned and started",
            "run_id": run_id,
            "status": status,
            "task": _task_to_dict(updated) if updated else None,
            "agent": spec.to_dict(),
        }
        return _json_ok(payload)
    except Exception as e:
        return _json_err(f"Failed to assign/start agent: {e}")


class TaskIdInput(BaseModel):
    task_id: str

    @validator("task_id")
    def _valid_task_id(cls, v):
        _uuid_from_str(v)
        return v


@tool("stop_agent_tool", args_schema=TaskIdInput)
def stop_agent_tool(task_id: str) -> str:
    """Attempt to stop the latest running agent process for the task. Also marks agent_state=stopped.

    Returns JSON with {stopped: bool, status: dict, task: Task}.
    """
    try:
        tid = _uuid_from_str(task_id)
        task = svc_get_task(tid)
        if not task:
            return _json_err("Task not found", code="not_found", extra={"task_id": task_id})

        stopped = rm_stop_run(str(task.id))
        # Reflect in task state (best-effort)
        svc_set_agent_state(task.id, AgentState.stopped)
        updated = svc_get_task(task.id)
        status = rm_get_status(str(task.id))
        return _json_ok({
            "stopped": bool(stopped),
            "status": status,
            "task": _task_to_dict(updated) if updated else None,
        })
    except Exception as e:
        return _json_err(f"Failed to stop agent: {e}")


@tool("get_agent_status_tool", args_schema=TaskIdInput)
def get_agent_status_tool(task_id: str) -> str:
    """Get the latest agent run status for a task along with task assignment info."""
    try:
        tid = _uuid_from_str(task_id)
        task = svc_get_task(tid)
        status = rm_get_status(str(task_id))
        return _json_ok({
            "task": _task_to_dict(task) if task else None,
            "status": status,
        })
    except Exception as e:
        return _json_err(f"Failed to get agent status: {e}")


__all__ = [
    "create_task",
    "add_subtask",
    "get_task",
    "list_tasks",
    "update_task",
    "stop_task",
    "block_task",
    "create_sequence",
    # New agent tools
    "list_agents_tool",
    "assign_and_start_agent_tool",
    "stop_agent_tool",
    "get_agent_status_tool",
]
