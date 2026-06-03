"""
Compatibility wrapper for LangChain tools.

The task and coordination tools are implemented in `tools.task_management`.
This module re-exports them for backward compatibility and keeps only the
agent-factory-specific tools local.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
from uuid import uuid4

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

from agents import run_manager
from agents.registry import (
    AgentSpec,
    add_agent as reg_add_agent,
    get_agent as reg_get_agent,
    list_agents as reg_list_agents,
    remove_agent as reg_remove_agent,
)
from agents.run_manager import (
    stop_run as rm_stop_run,
)
from agents.worker_runner import (
    start_run as rm_start_run,
    preregister_run as rm_preregister_run
)
from common.orchestrator_context import (
    filter_agents_for_workspace,
    task_in_workspace,
)
from common.session_service import get_or_create_task_session, add_run_to_session
from common.tasks_service import (
    CreatedBy,
    TaskStatus,
    assign_agent as svc_assign_agent,
    clear_agent as svc_clear_agent,
    get_task as svc_get_task,
    update_task as svc_update_task,
    append_routing_log_entry as svc_append_routing_log,
)
from tools.task_management import (
    add_subtask,
    block_task,
    create_sequence,
    create_task,
    get_task,
    list_tasks,
    stop_task,
    update_task,
    _uuid_from_str,
    _task_to_dict,
    _active_workspace,
)


def _json_ok(payload: Dict[str, object]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2)


def _json_err(message: str, *, code: str = "bad_request", extra: Optional[Dict[str, Any]] = None) -> str:
    body: Dict[str, Any] = {"ok": False, "error": message, "code": code}
    if extra:
        body.update(extra)
    return json.dumps(body, ensure_ascii=False, indent=2)



# -------------------- Agent coordination tools --------------------

@tool("list_agents_tool")
def list_agents_tool() -> str:
    """List all available agents from the registry. Returns JSON with an array of agents."""
    try:
        ws = _active_workspace()
        specs = filter_agents_for_workspace(reg_list_agents(), ws)
        agents = [{"id": s.id, "name": s.name, "description": s.description} for s in specs]
        return _json_ok({"agents": agents})
    except Exception as e:
        return _json_err(f"Failed to list agents: {e}")


class AssignAgentInput(BaseModel):
    task_id: str = Field(..., description="UUID of the task")
    agent_id: str = Field(..., min_length=1, description="Agent identifier from the registry")
    params_json: Optional[str] = Field(
        None, description="Optional JSON object string with agent parameters"
    )
    reason: Optional[str] = Field(
        None, description="Brief explanation of why this agent was chosen for this task"
    )

    @field_validator("params_json", mode="before")
    @classmethod
    def coerce_params_json(cls, v):
        if isinstance(v, (dict, list)):
            return json.dumps(v)
        return v


@tool("assign_agent_tool", args_schema=AssignAgentInput)
def assign_agent_tool(task_id: str, agent_id: str, params_json: Optional[str] = None, reason: Optional[str] = None) -> str:
    """Assign an agent to a task without starting it.

    Always creates a pending run and returns assignment_mode so the caller
    knows whether to ask for user approval before calling start_agent_tool.
    Returns JSON with the updated task, agent info, assignment_mode, and run_id.
    """
    try:
        tid = _uuid_from_str(task_id)
        task = svc_get_task(tid)
        if not task:
            return _json_err("Task not found", code="not_found", extra={"task_id": task_id})
        ws = _active_workspace()
        if ws and not task_in_workspace(task, ws):
            return _json_err(
                f"Task '{task_id}' is outside the active workspace '{ws}'",
                code="forbidden",
            )
        if getattr(task, "status", None) == TaskStatus.stopped:
            return _json_err("Task is stopped and cannot be assigned", code="invalid_task")
        existing_agent = getattr(task, "assigned_agent_type", None)
        existing_run_id = getattr(task, "assigned_agent_run_id", None)
        if existing_run_id:
            run = run_manager.get_run_by_id(str(existing_run_id))
            run_status = str((run or {}).get("status") or "")
            if run_status == "awaiting_approval" and existing_agent:
                spec = reg_get_agent(existing_agent)
                return _json_ok({
                    "message": "Task already has a pending assignment.",
                    "existing_assignment": True,
                    "run_id": str(existing_run_id),
                    "assigned_agent_id": existing_agent,
                    "assigned_agent_name": spec.name if spec else existing_agent,
                    "task": _task_to_dict(task),
                })
            if run_status in {"running", "stop"} and existing_agent != "orchestrator":
                return _json_ok({
                    "message": "Task already has an active run; stop it before reassigning",
                    "run_id": str(existing_run_id),
                    "existing_assignment": True,
                    "status": run,
                    "task": _task_to_dict(task),
                })
        spec = reg_get_agent(agent_id)
        if not spec:
            return _json_err("Agent not found", code="not_found", extra={"agent_id": agent_id})
        if ws and not filter_agents_for_workspace([spec], ws):
            return _json_err(
                f"Agent '{agent_id}' is not available in workspace '{ws}'",
                code="forbidden",
            )
        if agent_id == "decomposer":
            if getattr(task, "created_by", None) != CreatedBy.user:
                return _json_err(
                    "Decomposer agent can only be assigned to user-created tasks",
                    code="invalid_task",
                )
        params: Optional[Dict[str, Any]] = None
        if params_json:
            try:
                obj = json.loads(params_json)
                if obj is not None and not isinstance(obj, dict):
                    return _json_err("params_json must be a JSON object", code="bad_params")
                params = obj
            except Exception as e:
                return _json_err(f"Invalid params_json: {e}", code="bad_params")
        if reason:
            svc_update_task(task.id, routing_reason=reason.strip())

        ws_name = task.workspace or "default"
        try:
            from workspace import get_workspace_metadata
            orch_settings = get_workspace_metadata(ws_name).get("orchestrator", {})
            assignment_mode = orch_settings.get("assignment_mode", "manual")
        except Exception:
            assignment_mode = "manual"

        session_id = getattr(task, "session_id", None)
        if not session_id:
            try:
                session_id = get_or_create_task_session(
                    title=task.title,
                    workspace=task.workspace,
                )
                svc_update_task(task.id, session_id=session_id)
            except Exception:
                session_id = None

        # Live mode: pre-register with "pending" so the task shows as pending (not awaiting_approval).
        # Manual mode: pre-register with "awaiting_approval" for the approval gate.
        run_id = rm_preregister_run(str(task.id), agent_id, session_id=session_id)
        if assignment_mode == "live":
            run_manager.update_run(run_id, {"status": "pending"})
            # Link to session immediately in live mode — start_agent_tool follows right away.
            # In manual mode the run stays awaiting_approval until approved, so we defer
            # the session link to start_agent_tool to avoid a spurious message entry.
            if session_id:
                try:
                    add_run_to_session(session_id, run_id)
                except Exception:
                    pass

        svc_assign_agent(task.id, agent_id, params, run_id=run_id)
        try:
            svc_append_routing_log(
                task_id=task.id,
                task_title=task.title,
                agent_id=agent_id,
                reason=reason.strip() if reason else None,
                workspace=getattr(task, "workspace", None),
            )
        except Exception:
            pass
        if agent_id == "code_reviewer":
            svc_update_task(task.id, status=TaskStatus.reviewing)
        if assignment_mode != "live":
            svc_update_task(task.id, status=TaskStatus.pending)

        updated = svc_get_task(task.id)
        
        _task_ws = str(getattr(task, "workspace", "") or "")
        wait_for_completion = _get_wait_for_completion(_task_ws or ws)
        message = (
            "Agent assigned. Call start_agent_tool now with task id."
            if assignment_mode == "live"
            else 
                "Agent assigned. NEXT: call wait_for_agent_tool with task id to monitor."
                if wait_for_completion
                else "Agent assigned. STOP — report this assignment to the user."
        )
        return _json_ok({
            "message": message,
            "run_id": run_id,
            "assignment_mode": assignment_mode,
            "task": _task_to_dict(updated) if updated else None,
            "agent": spec.to_dict(),
        })
    except Exception as e:
        return _json_err(f"Failed to assign agent: {e}")


class StartAgentInput(BaseModel):
    task_id: str = Field(..., description="UUID of the task with an assigned agent")


@tool("start_agent_tool", args_schema=StartAgentInput)
def start_agent_tool(task_id: str) -> str:
    """Start execution of the agent already assigned to a task.

    The task must have an agent assigned via assign_agent_tool.
    Returns JSON with run status and updated task.
    """
    try:
        tid = _uuid_from_str(task_id)
        task = svc_get_task(tid)
        if not task:
            return _json_err("Task not found", code="not_found", extra={"task_id": task_id})
        ws = _active_workspace()
        if ws and not task_in_workspace(task, ws):
            return _json_err(
                f"Task '{task_id}' is outside the active workspace '{ws}'",
                code="forbidden",
            )
        agent_id = getattr(task, "assigned_agent_type", None)
        if not agent_id:
            return _json_err(
                "No agent assigned to this task; call assign_agent_tool first",
                code="invalid_task",
            )
        existing_run_id = getattr(task, "assigned_agent_run_id", None)
        if existing_run_id:
            run = run_manager.get_run_by_id(str(existing_run_id))
            run_status = str((run or {}).get("status") or "")
            if run_status == "running":
                return _json_ok({
                    "message": "Agent is already running",
                    "run_id": str(existing_run_id),
                    "status": run,
                    "task": _task_to_dict(task),
                })
            if run_status in {"stop", "stopped"}:
                return _json_err(
                    "Previous run was stopped; cannot start agent. Please assign again to create a new run.",
                    code="invalid_task",
                    extra={"run_id": str(existing_run_id), "run_status": run_status},
                )
            if run_status not in {"awaiting_approval", "pending", ""}:
                return _json_err(
                    "Task is not awaiting approval; it has already been started. "
                    "Use assign_agent_tool to reassign first.",
                    code="invalid_state",
                    extra={"task": _task_to_dict(task)},
                )
        params = getattr(task, "assigned_agent_params", None)
        preregistered_run_id = str(existing_run_id) if existing_run_id else None
        _task_ws = str(getattr(task, "workspace", "") or "")
        execution_mode = _get_execution_mode(_task_ws or ws)

        if execution_mode == "node":
            # Node mode: flip the pre-registered run to "assigned" so agent_state
            # resolves to AgentState.assigned (not pending_approval). Keep task status
            # as in_progress — the worker node will pick it up via agent_state alone.
            run_id = preregistered_run_id or str(uuid4())
            run_manager.update_run(run_id, {"status": "assigned"})
            svc_assign_agent(task.id, agent_id, params, run_id=run_id)
            svc_update_task(task.id, status=TaskStatus.in_progress)
        else:
            # Subprocess mode: launch immediately, reusing the pre-registered run_id.
            run_id, _ = rm_start_run(str(task.id), agent_id, params, run_id=preregistered_run_id)
            svc_assign_agent(task.id, agent_id, params, run_id=run_id)
            svc_update_task(task.id, status=TaskStatus.in_progress)
        # Link the run to the task's session now that it has actually started.
        # Idempotent — safe to call even if live mode already linked it in assign_agent_tool.
        try:
            _start_session_id = getattr(task, "session_id", None)
            if _start_session_id:
                add_run_to_session(_start_session_id, run_id)
        except Exception:
            pass
        wait_for_completion = _get_wait_for_completion(_task_ws or ws)

        # Register a session continuation only in fire-and-forget mode.
        # When wait_for_completion=true the orchestrator polls and handles followup
        # itself — registering a continuation would spawn a second orchestrator.
        try:
            _fmode = _get_followup_mode(_task_ws or ws)
            if _fmode == "continuous" and not wait_for_completion:
                from common.agent_context import current_session_id as _sess_ctx
                from common.session_service import register_continuation
                _sid = _sess_ctx.get() or os.environ.get("AGENT_SESSION_ID")
                if _sid:
                    register_continuation(
                        session_id=_sid,
                        task_id=str(task.id),
                        workspace=_task_ws or None,
                    )
        except Exception:
            pass
        if execution_mode == "node":
            message = (
                "Task queued for node execution. NEXT: call wait_for_agent_tool with task id to monitor."
                if wait_for_completion
                else "Task queued for node execution. YOUR TURN IS DONE. Do not call any more tools."
            )
        else:
            message = (
                "Agent started. NEXT: call wait_for_agent_tool with task id to monitor."
                if wait_for_completion
                else "Agent started. YOUR TURN IS DONE. Do not call any more tools."
            )
        return _json_ok({
            "message": message,
            "run_id": run_id,
            "execution_mode": execution_mode,
            "wait_for_completion": wait_for_completion,
        })
    except Exception as e:
        return _json_err(f"Failed to start agent: {e}")


class TaskIdInput(BaseModel):
    task_id: str = Field(..., description="UUID of the task")


@tool("reject_assignment_tool", args_schema=TaskIdInput)
def reject_assignment_tool(task_id: str) -> str:
    """Reject the pending agent assignment for a task.

    Clears the assigned agent and resets the task status to 'todo' so it can be reassigned.
    Call this when the user rejects the proposed assignment.
    """
    try:
        tid = _uuid_from_str(task_id)
        task = svc_get_task(tid)
        if not task:
            return _json_err("Task not found", code="not_found", extra={"task_id": task_id})
        ws = _active_workspace()
        if ws and not task_in_workspace(task, ws):
            return _json_err(
                f"Task '{task_id}' is outside the active workspace '{ws}'",
                code="forbidden",
            )
        if getattr(task, "assigned_agent_run_id", None):
            return _json_err(
                "Task already has an active run and cannot be rejected; stop it first",
                code="invalid_state",
            )
        _FINISHED_STATUSES = {TaskStatus.resolved, TaskStatus.reviewing, TaskStatus.reviewed, TaskStatus.done}
        restore_status = (
            task.pre_assignment_status
            if getattr(task, "pre_assignment_status", None) in _FINISHED_STATUSES
            else TaskStatus.todo
        )
        svc_clear_agent(tid)
        svc_update_task(tid, status=restore_status)
        updated = svc_get_task(tid)
        return _json_ok({
            "message": f"Assignment rejected. Task returned to '{restore_status.value}' — you can assign a different agent.",
            "task": _task_to_dict(updated) if updated else None,
        })
    except Exception as e:
        return _json_err(f"Failed to reject assignment: {e}")


@tool("stop_agent_tool", args_schema=TaskIdInput)
def stop_agent_tool(task_id: str) -> str:
    """Attempt to stop the latest running agent process for the task.

    Returns JSON with {stopped: bool, status: dict, task: Task}.
    """
    try:
        tid = _uuid_from_str(task_id)
        task = svc_get_task(tid)
        if not task:
            return _json_err("Task not found", code="not_found", extra={"task_id": task_id})
        ws = _active_workspace()
        if ws and not task_in_workspace(task, ws):
            return _json_err(
                f"Task '{task_id}' is outside the active workspace '{ws}'",
                code="forbidden",
            )
        current_run_id = str(getattr(task, "assigned_agent_run_id", None) or "")
        stopped = rm_stop_run(str(task.id), run_id=current_run_id or None)
        if not stopped:
            try:
                svc_update_task(
                    task.id,
                    status=TaskStatus.stopped,
                    assigned_agent_type=None,
                    assigned_agent_params=None,
                    assigned_agent_run_id=None,
                )
            except Exception:
                pass
        updated = svc_get_task(task.id)
        status = run_manager.get_run_by_id(current_run_id) if current_run_id else None
        return _json_ok({
            "stopped": True if not stopped else bool(stopped),
            "status": status,
            "task": _task_to_dict(updated) if updated else None,
        })
    except Exception as e:
        return _json_err(f"Failed to stop agent: {e}")


_DONE_RUN_STATUSES = {"completed", "done", "finished"}
_FAILED_RUN_STATUSES = {"failed", "error"}


def _get_followup_mode(workspace: Optional[str]) -> str:
    """Read followup_mode from workspace orchestrator settings. Defaults to 'single'."""
    try:
        from workspace import get_workspace_metadata
        ws_name = workspace or "default"
        return get_workspace_metadata(ws_name).get("orchestrator", {}).get("followup_mode", "single")
    except Exception:
        return "single"


def _get_wait_for_completion(workspace: Optional[str]) -> bool:
    """Read wait_for_completion from workspace orchestrator settings. Defaults to False."""
    try:
        from workspace import get_workspace_metadata
        ws_name = workspace or "default"
        return get_workspace_metadata(ws_name).get("orchestrator", {}).get("wait_for_completion", False)
    except Exception:
        return False


def _get_execution_mode(workspace: Optional[str]) -> str:
    """Read execution_mode from workspace orchestrator settings. Defaults to 'subprocess'."""
    try:
        from workspace import get_workspace_metadata
        ws_name = workspace or "default"
        return get_workspace_metadata(ws_name).get("orchestrator", {}).get("execution_mode", "subprocess")
    except Exception:
        return "subprocess"


class WaitForAgentInput(BaseModel):
    task_id: str = Field(..., description="UUID of the task to wait on")
    interval: int = Field(2, ge=2, le=2, description="Seconds to wait before checking status (2–2)")


@tool("wait_for_agent_tool", args_schema=WaitForAgentInput)
def wait_for_agent_tool(task_id: str, interval: int = 2) -> str:
    """Wait a short time, then return the current agent status for a task.

    Use this after start_agent_tool to poll progress without busy-waiting.
    Call it repeatedly until agent_finished or agent_failed is true.
    Returns the same fields as get_agent_status_tool.
    """
    import time
    time.sleep(max(2, min(interval, 60)))
    # return get_agent_status_tool.invoke({"task_id": task_id})
    task = svc_get_task(task_id)
    return _json_ok({
            "message": "Agent status update after waiting. Call get_agent_status_tool with task id.",
            "task": _task_to_dict(task) if task else None,
        })


@tool("get_agent_status_tool", args_schema=TaskIdInput)
def get_agent_status_tool(task_id: str) -> str:
    """Get the latest agent run status for a task along with task assignment info.

    Key fields in the response:
    - agent_finished: true when the agent completed successfully (task status 'resolved' or 'reviewed').
    - agent_failed: true when the agent run failed (task status 'blocked').
    - agent_running: true when the agent is still executing.
    - followup_mode: workspace setting — 'continuous' means automatically chain the next step;
      'single' means report back to the user and wait for their instructions before proceeding.
    - wait_for_completion: workspace setting — true means poll until the agent finishes;
      false means fire-and-forget (start the agent, report to the user, and stop).
    """
    try:
        tid = _uuid_from_str(task_id)
        task = svc_get_task(tid)
        ws = _active_workspace()
        if task and ws and not task_in_workspace(task, ws):
            return _json_err(
                f"Task '{task_id}' is outside the active workspace '{ws}'",
                code="forbidden",
            )
        # Use the run_id already on the task — no need to scan all runs by task_id.
        current_run_id = getattr(task, "assigned_agent_run_id", None)
        status = run_manager.get_run_by_id(str(current_run_id)) if current_run_id else None

        # If the assigned run belongs to the orchestrator itself (continuation),
        # check the most recent worker run instead — not the orchestrator's own status.
        if status and str(status.get("agent_id") or "") == "orchestrator":
            all_runs = run_manager.load_runs()
            worker_runs = sorted(
                [r for r in all_runs
                 if r.get("task_id") == str(tid)
                 and r.get("agent_id") != "orchestrator"
                 and r.get("run_id") != str(current_run_id)],
                key=lambda r: r.get("started_at") or "",
            )
            if worker_runs:
                status = worker_runs[-1]

        task_status = str(getattr(task, "status", "") or "")
        run_status = str((status or {}).get("status") or "")

        agent_finished = (
            task_status in {"resolved", "reviewed"}
            or run_status in _DONE_RUN_STATUSES
        )
        agent_failed = (
            task_status in {"blocked", "stopped"}
            or run_status in (_FAILED_RUN_STATUSES | {"stopped"})
        )
        # "pending"/"stop" are transient active states; treat as running
        agent_running = run_status in {"running", "in_progress", "pending", "stop"}

        # Catch-all: if no flag matched (e.g. awaiting_approval, unknown, no run),
        # keep polling rather than leaving the orchestrator with no branch to follow.
        if not agent_running and not agent_finished and not agent_failed:
            agent_running = True

        # In fire-and-forget mode (wait_for_completion=false) the orchestrator's job
        # is done as soon as the agent was started without error.  Treat any
        # non-failed state as "finished" so the LLM stops polling and goes to Step 5.
        _task_ws = str(getattr(task, "workspace", "") or "") if task else ""
        if not agent_failed and _get_wait_for_completion(_task_ws or ws) is False:
            agent_finished = True
            agent_running = False

        assigned_agent = str(getattr(task, "assigned_agent_type", "") or "") if task else ""
        routing_reason = str(getattr(task, "routing_reason", "") or "") if task else ""

        if agent_finished:
            message = "DONE. Task completed successfully. Stop all tool calls and summarise the orchestration: which agent was assigned, why, and that the task finished successfully."
        elif agent_failed:
            message = "DONE. Task failed. Stop all tool calls and summarise the orchestration: which agent was assigned, why, and that the task failed."
        else:
            message = "RUNNING. Use wait_for_agent_tool again."

        return _json_ok({
            "message": message,
            "agent_finished": agent_finished,
            "agent_failed": agent_failed,
            "agent_running": agent_running,
            "assigned_agent": assigned_agent,
            "routing_reason": routing_reason,
            "task_status": task_status,
        })
    except Exception as e:
        return _json_err(f"Failed to get agent status: {e}")


# -------------------- Agent factory tools --------------------

class CreateAgentInput(BaseModel):
    agent_id: str = Field(..., min_length=1, description="Unique identifier for the new agent (no spaces)")
    name: str = Field(..., min_length=1, description="Human-readable display name")
    description: str = Field("", description="What the agent does")
    domain: str = Field("general", description="Domain: general, development, orchestration, testing, etc.")
    system_prompt: str = Field(..., min_length=1, description="System instructions for the agent")
    tools: List[str] = Field(
        default_factory=lambda: ["read_file", "write_file", "list_files"],
        description="List of tool IDs to equip the agent with",
    )
    capacity: int = Field(1, ge=1, description="Max concurrent sessions")


@tool("create_agent_tool", args_schema=CreateAgentInput)
def create_agent_tool(
    agent_id: str,
    name: str,
    description: str = "",
    domain: str = "general",
    system_prompt: str = "",
    tools: Optional[List[str]] = None,
    capacity: int = 1,
) -> str:
    """Create a new agent in the system registry.

    The system prompt is written to ``agents/definitions/<agent_id>/instructions.md``
    rather than stored in agents.json. Structured fields (id, name, tools, capacity)
    are persisted in the registry.
    """
    try:
        if tools is None:
            tools = ["read_file", "write_file", "list_files"]
        if reg_get_agent(agent_id):
            return _json_err(f"Agent with id '{agent_id}' already exists", code="conflict")
        if not system_prompt or not system_prompt.strip():
            return _json_err("system_prompt is required", code="invalid")

        from agents import prompt_assembly
        prompt_assembly.write_instructions(agent_id, system_prompt)

        # Resolve the active workspace so the new agent is owned by — and only
        # visible in — the workspace it was created from (unless later shared).
        active_ws: Optional[str] = None
        try:
            from common.orchestrator_context import resolve_active_workspace
            from workspace import get_workspace_folder
            ws = resolve_active_workspace()
            if ws and ws != "default" and get_workspace_folder(ws):
                active_ws = ws
        except Exception:
            active_ws = None

        spec = AgentSpec(
            id=agent_id,
            name=name,
            description=description,
            domain=domain,
            type="langchain",
            entrypoint="agents.agent_factory:build_agent_executor",
            tools=tools,
            capacity=capacity,
            default_params={},
            owner_workspace=active_ws,
        )
        reg_add_agent(spec)

        # Auto-register the new agent in the owning workspace's allowed_agents
        # so it shows up immediately in the workspace UI. Falls back silently
        # when no workspace context is set (e.g. CLI invocation).
        added_to_workspace: Optional[str] = None
        try:
            from workspace import (
                get_workspace_metadata,
                update_workspace_metadata,
            )
            if active_ws:
                meta = get_workspace_metadata(active_ws)
                allowed = list(meta.get("allowed_agents") or [])
                if agent_id not in allowed:
                    allowed.append(agent_id)
                    update_workspace_metadata(active_ws, {"allowed_agents": allowed})
                added_to_workspace = active_ws
        except Exception:
            # Workspace association is best-effort; the agent itself is created.
            pass

        payload = {"agent": spec.to_dict(), "message": f"Agent '{name}' created successfully"}
        if added_to_workspace:
            payload["workspace"] = added_to_workspace
            payload["message"] = (
                f"Agent '{name}' created and added to workspace '{added_to_workspace}'"
            )
        return _json_ok(payload)
    except Exception as e:
        return _json_err(f"Failed to create agent: {e}")


class GetAgentInput(BaseModel):
    agent_id: str = Field(..., min_length=1, description="ID of the agent to retrieve")


class ModifyAgentInput(BaseModel):
    agent_id: str = Field(..., min_length=1, description="ID of the existing agent to modify")
    name: Optional[str] = Field(None, description="New human-readable display name")
    description: Optional[str] = Field(None, description="New agent description")
    domain: Optional[str] = Field(None, description="New domain")
    system_prompt: Optional[str] = Field(
        None,
        description="Behavior changes to merge into instructions.md. Existing instructions are preserved unless replace_system_prompt is true.",
    )
    replace_system_prompt: bool = Field(False, description="When true, system_prompt replaces instructions.md instead of appending a behavior update")
    capabilities: Optional[str] = Field(None, description="Replacement capabilities.md content; empty string deletes the file")
    usage: Optional[str] = Field(None, description="Replacement usage.md content; empty string deletes the file")
    tools: Optional[List[str]] = Field(None, description="Replacement list of tool IDs")
    capacity: Optional[int] = Field(None, ge=1, description="New max concurrent sessions")
    memory_type: Optional[str] = Field(None, description="Memory type: none, local, or shared")
    memory_data: Optional[Any] = Field(None, description="Memory payload, such as a shared memory pool id")
    skills_enabled: Optional[bool] = Field(None, description="Enable or disable procedural skills for this agent")
    provider: Optional[str] = Field(None, description="Model provider override; empty string clears")
    model: Optional[str] = Field(None, description="Model override; empty string clears")
    base_url: Optional[str] = Field(None, description="Provider base URL override; empty string clears")
    temperature: Optional[float] = Field(None, description="Temperature override")
    max_tokens: Optional[int] = Field(None, ge=1, description="Max tokens override")
    reasoning: Optional[Dict[str, Any]] = Field(
        None,
        description="Reasoning settings, e.g. {'think_enabled': true, 'think_mode': 'deep', 'plan_enabled': true, 'plan_format': 'bullet'}",
    )


@tool("get_agent_tool", args_schema=GetAgentInput)
def get_agent_tool(agent_id: str) -> str:
    """Get details of a specific agent by ID."""
    try:
        spec = reg_get_agent(agent_id)
        if not spec:
            return _json_err(f"Agent '{agent_id}' not found", code="not_found")
        from agents import prompt_assembly
        definition = {
            "instructions": prompt_assembly.read_instructions(agent_id),
            "capabilities": prompt_assembly.read_capabilities(agent_id),
            "usage": prompt_assembly.read_usage(agent_id),
        }
        return _json_ok({"agent": spec.to_dict(), "definition": definition})
    except Exception as e:
        return _json_err(f"Failed to get agent: {e}")


@tool("modify_agent_tool", args_schema=ModifyAgentInput)
def modify_agent_tool(
    agent_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    domain: Optional[str] = None,
    system_prompt: Optional[str] = None,
    replace_system_prompt: bool = False,
    capabilities: Optional[str] = None,
    usage: Optional[str] = None,
    tools: Optional[List[str]] = None,
    capacity: Optional[int] = None,
    memory_type: Optional[str] = None,
    memory_data: Optional[Any] = None,
    skills_enabled: Optional[bool] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    reasoning: Optional[Dict[str, Any]] = None,
) -> str:
    """Modify an existing agent's behavior and configuration.

    Updates only fields that are provided. The main behavior prompt lives in
    ``instructions.md``. By default, ``system_prompt`` is appended as a behavior
    update so existing suitable instructions remain intact; set
    ``replace_system_prompt`` to true for a full rewrite.
    """
    try:
        spec = reg_get_agent(agent_id)
        if not spec:
            return _json_err(f"Agent '{agent_id}' not found", code="not_found")

        changed: List[str] = []

        from agents import prompt_assembly
        folder = prompt_assembly.agent_dir(agent_id)

        if system_prompt is not None:
            update_text = system_prompt.strip()
            if not update_text:
                return _json_err("system_prompt cannot be empty", code="invalid")
            current_instructions = prompt_assembly.read_instructions(agent_id)
            if replace_system_prompt or not current_instructions.strip():
                next_instructions = update_text
            else:
                marker = "## Behavior Updates"
                if marker in current_instructions:
                    next_instructions = current_instructions.rstrip() + "\n\n" + update_text
                else:
                    next_instructions = (
                        current_instructions.rstrip()
                        + "\n\n"
                        + marker
                        + "\n\n"
                        + update_text
                    )
            prompt_assembly.write_instructions(agent_id, next_instructions)
            changed.append("instructions")

        if capabilities is not None:
            path = folder / prompt_assembly.CAPABILITIES_FILE
            if capabilities.strip():
                prompt_assembly.write_capabilities(agent_id, capabilities)
                changed.append("capabilities")
            elif path.exists():
                path.unlink()
                changed.append("capabilities")

        if usage is not None:
            path = folder / prompt_assembly.USAGE_FILE
            if usage.strip():
                prompt_assembly.write_usage(agent_id, usage)
                changed.append("usage")
            elif path.exists():
                path.unlink()
                changed.append("usage")

        def _blank_to_none(value: Optional[str]) -> Optional[str]:
            if value is None:
                return None
            stripped = value.strip()
            return stripped or None

        new_spec = AgentSpec(
            id=spec.id,
            name=name.strip() if name is not None and name.strip() else spec.name,
            type=spec.type,
            entrypoint=spec.entrypoint,
            description=description if description is not None else spec.description,
            domain=domain.strip() if domain is not None and domain.strip() else spec.domain,
            default_params=dict(spec.default_params or {}),
            tools=list(tools) if tools is not None else list(spec.tools or []),
            commands=list(spec.commands or []),
            capacity=int(capacity) if capacity is not None else spec.capacity,
            memory_type=memory_type if memory_type is not None else spec.memory_type,
            memory_data=memory_data if memory_data is not None else spec.memory_data,
            default_workspace_only=spec.default_workspace_only,
            owner_workspace=spec.owner_workspace,
            shared=spec.shared,
            provider=_blank_to_none(provider) if provider is not None else spec.provider,
            model=_blank_to_none(model) if model is not None else spec.model,
            base_url=_blank_to_none(base_url) if base_url is not None else spec.base_url,
            temperature=temperature if temperature is not None else spec.temperature,
            max_tokens=int(max_tokens) if max_tokens is not None else spec.max_tokens,
            api_key=spec.api_key,
            verbose=spec.verbose,
            streaming=spec.streaming,
            http_expose=spec.http_expose,
            http_port=spec.http_port,
            http_host_port=spec.http_host_port,
            node_type=spec.node_type,
            is_default_chat_agent=spec.is_default_chat_agent,
            skills_enabled=bool(skills_enabled) if skills_enabled is not None else spec.skills_enabled,
            reasoning=dict(reasoning) if reasoning is not None else dict(spec.reasoning or {}),
        )

        before = spec.to_dict()
        reg_add_agent(new_spec)
        after = new_spec.to_dict()
        for key in ("name", "description", "domain", "tools", "capacity", "memory_type", "memory_data", "skills_enabled", "provider", "model", "base_url", "temperature", "max_tokens", "reasoning"):
            if before.get(key) != after.get(key):
                changed.append(key)

        if not changed:
            return _json_ok({"agent": after, "message": f"Agent '{agent_id}' unchanged"})

        return _json_ok({
            "agent": after,
            "changed": sorted(set(changed)),
            "message": f"Agent '{agent_id}' modified successfully",
        })
    except Exception as e:
        return _json_err(f"Failed to modify agent: {e}")


class DeleteAgentInput(BaseModel):
    agent_id: str = Field(..., min_length=1, description="ID of the agent to delete")


@tool("delete_agent_tool", args_schema=DeleteAgentInput)
def delete_agent_tool(agent_id: str) -> str:
    """Delete an agent from the system registry by ID.

    Removes both the agents.json entry and the markdown definition folder.
    """
    try:
        protected = {"orchestrator", "decomposer", "agent_creator"}
        if agent_id in protected:
            return _json_err(f"Agent '{agent_id}' is a system agent and cannot be deleted", code="forbidden")
        removed = reg_remove_agent(agent_id)
        if not removed:
            return _json_err(f"Agent '{agent_id}' not found", code="not_found")
        from agents import prompt_assembly
        prompt_assembly.delete_definition(agent_id)
        return _json_ok({"message": f"Agent '{agent_id}' deleted successfully", "agent_id": agent_id})
    except Exception as e:
        return _json_err(f"Failed to delete agent: {e}")


__all__ = [
    "create_task",
    "add_subtask",
    "get_task",
    "list_tasks",
    "update_task",
    "stop_task",
    "block_task",
    "create_sequence",
    "list_agents_tool",
    "assign_agent_tool",
    "start_agent_tool",
    "reject_assignment_tool",
    "stop_agent_tool",
    "get_agent_status_tool",
    "wait_for_agent_tool",
    "create_agent_tool",
    "get_agent_tool",
    "modify_agent_tool",
    "delete_agent_tool",
]
