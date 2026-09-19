"""
Assigning an agent to a task, as a function.

The policy here is the part worth not duplicating: which agents a workspace
admits, that only user-created tasks go to the decomposer, that assigning the
orchestrator to a parent starts its subtasks rather than running anything on the
parent, and that node mode records an assignment for a worker to pick up while
subprocess mode launches immediately.

Both entry points — ``POST /api/tasks/{id}/assign`` and the terminal client —
call this, so an assignment means the same thing whichever one made it.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from tasks import service as tasks_service
from tasks.context import augment_params_with_block_reason
from tasks.models import TaskStatus, CreatedBy
from agents import registry
from agents import agent_launcher
from managers import run_manager
from workspace import get_workspace_metadata
from common.session_service import add_event_to_session


class AssignError(Exception):
    """An assignment that policy or state does not allow.

    ``status`` mirrors the HTTP status the API reports, so the route re-raises it
    unchanged and non-HTTP callers just read ``detail``.
    """

    def __init__(self, detail: str, status: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status = status


# Assigning these two is always permitted: they are how work gets routed and
# broken down in the first place, so a restrictive allowed_agents list must not
# be able to wedge a workspace.
_ALWAYS_ALLOWED = ("orchestrator", "decomposer")


def assign_agent_to_task(
    task_id: UUID,
    agent_id: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    task_to_dict,
) -> Dict[str, Any]:
    """Assign ``agent_id`` to ``task_id`` and start it according to workspace mode.

    ``task_to_dict`` is passed in rather than imported: the serializer lives in
    the API layer today, and threading it through keeps this module free of a
    dependency on it.
    """
    t = tasks_service.get_task(task_id)
    if not t:
        raise AssignError("Task not found", status=404)
    if t.status == TaskStatus.stopped:
        raise AssignError("Task is stopped and cannot be assigned")
    # A blocked task IS assignable: re-assigning an agent is how a block gets
    # fixed. The block reason is carried into the new run's input automatically.

    spec = registry.get_agent(agent_id)
    if not spec:
        raise AssignError("Agent not found", status=404)

    if t.workspace:
        metadata = get_workspace_metadata(t.workspace)
        allowed = metadata.get("allowed_agents")  # None means unrestricted
        if allowed is not None and agent_id not in allowed and agent_id not in _ALWAYS_ALLOWED:
            raise AssignError(
                f"Agent '{agent_id}' is not authorized for workspace '{t.workspace}'",
                status=403,
            )

    # Policy: only user-created tasks may be assigned to the dedicated decomposer agent
    if agent_id == "decomposer" and getattr(t, "created_by", None) != CreatedBy.user:
        raise AssignError("Decomposer agent can only be assigned to user-created tasks")

    # A parent with subtasks is a container: assigning the orchestrator to it
    # does not run an agent on the parent — it starts processing the subtasks.
    # The parent moves to in_progress, runnable subtasks are promoted to ready
    # (picked up by the orchestrator like normal tasks), and the parent is
    # completed automatically when the last subtask is done.
    if agent_id == "orchestrator":
        subtasks = tasks_service.get_subtasks(task_id)
        if subtasks:
            promoted = tasks_service.activate_parent_container(task_id)
            updated = tasks_service.get_task(task_id)
            return {
                "task": task_to_dict(updated),
                "run_id": None,
                "pending_approval": False,
                "container": True,
                "subtasks_promoted": promoted,
            }

    ws_name = str(getattr(t, "workspace", "") or "default")
    execution_mode = get_workspace_metadata(ws_name).get("orchestrator", {}).get("execution_mode", "subprocess")

    # If this task is blocked, fold the block reason into the worker's input
    # so the newly assigned agent knows what to fix. Capture it now, before
    # the status flip below clears blocked_reason.
    assign_params = augment_params_with_block_reason(t, params)

    if execution_mode == "node":
        # Node mode: register an "assigned" run record so agent_state resolves to
        # AgentState.assigned (not pending_approval). Task stays "ready to assign"
        # until a worker node picks it up and transitions the run to "running".
        session_id = getattr(t, "session_id", None) or None
        if not session_id:
            try:
                from common.session_service import get_or_create_task_session
                session_id = get_or_create_task_session(title=t.title, workspace=t.workspace, task_id=str(task_id))
                tasks_service.update_task(task_id, session_id=session_id)
            except Exception:
                session_id = None
        run_id = str(uuid4())
        run_manager.upsert_run({
            "run_id": run_id,
            "task_id": str(task_id),
            "agent_id": agent_id,
            "status": "assigned",
            "session_type": "task",
            "session_id": session_id,
            "created_at": run_manager.utc_now_iso(),
            "started_at": None,
            "finished_at": None,
            "pid": None,
            "exit_code": None,
            "error": None,
        })
        tasks_service.assign_agent(task_id, agent_id, assign_params, run_id=run_id)
        new_status = TaskStatus.reviewing if agent_id == "code_reviewer" else TaskStatus.ready
        tasks_service.update_task(task_id, status=new_status)
    else:
        # start_run creates/reuses a session and returns (run_id, session_id)
        run_id, session_id = agent_launcher.start_run(str(task_id), agent_id, assign_params)
        tasks_service.assign_agent(task_id, agent_id, assign_params, run_id=run_id)
        new_status = TaskStatus.reviewing if agent_id == "code_reviewer" else TaskStatus.in_progress
        tasks_service.update_task(task_id, status=new_status)

    if session_id:
        try:
            add_event_to_session(session_id, {
                "type": "agent_assigned",
                "agent_id": agent_id,
                "timestamp": run_manager.utc_now_iso(),
                "description": f"Assignment of {agent_id} is done by user",
            })
        except Exception:
            pass

    updated = tasks_service.get_task(task_id)
    return {
        "task": task_to_dict(updated),
        "run_id": run_id,
        "pending_approval": False,
    }
