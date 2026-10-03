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
from tasks.models import Executor, TaskStatus, CreatedBy
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


def validate_launch_params(agent_id: str, params: Optional[Dict[str, Any]]) -> None:
    """Check ``params["agent_version"]`` and ``params["overrides"]`` (with the
    older ``tool_policy``/``output_schema`` aliases) for a run of ``agent_id``.

    Raises :class:`AssignError`: 400 for a version the agent does not have or
    an invalid overrides object, 409 for a tool set the capability guard refuses.
    """
    params = params or {}
    if params.get("agent_version") is not None:
        try:
            tasks_service.validate_agent_version(agent_id, int(params["agent_version"]))
        except (TypeError, ValueError) as exc:
            raise AssignError(str(exc), status=400) from exc
    if any(params.get(k) not in (None, "", {}) for k in ("overrides", "tool_policy", "output_schema")):
        from agents import run_overrides
        from agents.capability_guard import CapabilityViolation
        try:
            run_overrides.validate_for_agent(
                agent_id, params.get("overrides"),
                tool_policy=params.get("tool_policy"), output_schema=params.get("output_schema"))
        except CapabilityViolation as exc:
            raise AssignError(str(exc), status=409) from exc
        except run_overrides.OverrideError as exc:
            raise AssignError(str(exc), status=400) from exc


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

    # A one-off version pin and per-run overrides in the launch params
    # (agents/agent_launcher.py reads both): checked here so a bad pin, an
    # unknown override key or a tool set the capability guard refuses is a
    # 400/409 to the caller, not a run that fails after it was started.
    validate_launch_params(agent_id, params)

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


def assign_executor_to_task(
    task_id: UUID,
    executor: Executor | Dict[str, Any] | str,
    params: Optional[Dict[str, Any]] = None,
    *,
    task_to_dict,
) -> Dict[str, Any]:
    """Assign an executor (an agent, a flow, a team, a loop or a scenario) to a task and start it.

    Dispatches on ``executor.kind``: an agent goes through
    :func:`assign_agent_to_task` unchanged (allowed_agents, the decomposer
    rule, the orchestrator-on-a-container special case, node vs subprocess
    mode); a flow, a team or a loop is launched through its own launcher
    (``flow.launcher.start_flow_run`` / ``teams.launcher.start_team_run`` /
    ``loops.runner.run_loop``) and the resulting run is recorded on the task
    through :func:`tasks.service.assign_executor`.

    The workspace's ``allowed_agents`` restriction applies only to the agent
    branch — a flow is scoped by its own ``allowed_flows`` (enforced by
    ``flow.launcher.trigger_flow``, a different entry point; a task-attached
    flow run has always launched unrestricted, matching ``start_flow_run``),
    and a team or a loop has no equivalent workspace allowlist today.

    Raises ``AssignError`` (status 400) for a malformed ``executor`` (missing
    ``id``, or a ``kind`` that is not one of the five) rather than letting
    pydantic's ``ValidationError`` escape — the route's only other catch-all
    is a 500, and a bad request body is not a server error.
    """
    if isinstance(executor, str):
        executor = Executor(kind="agent", id=executor)
    elif not isinstance(executor, Executor):
        try:
            executor = Executor.model_validate(executor)
        except Exception as e:  # noqa: BLE001 - a malformed request body is a 400, not a 500
            raise AssignError(f"Invalid executor: {e}", status=400)

    if executor.kind == "agent":
        return assign_agent_to_task(task_id, executor.id, params, task_to_dict=task_to_dict)
    if executor.kind == "flow":
        return _assign_flow_to_task(task_id, executor.id, params, task_to_dict=task_to_dict)
    if executor.kind == "team":
        return _assign_team_to_task(task_id, executor.id, params, task_to_dict=task_to_dict)
    if executor.kind == "loop":
        return _assign_loop_to_task(task_id, executor.id, params, task_to_dict=task_to_dict)
    if executor.kind == "scenario":
        return _assign_scenario_to_task(task_id, executor.id, params, task_to_dict=task_to_dict)
    raise AssignError(f"Unknown executor kind: {executor.kind}", status=400)  # pragma: no cover - Executor.kind is a Literal


def _assign_common_checks(task_id: UUID):
    """The refusals every non-agent executor shares with the agent path: the
    task must exist and must not be stopped (re-assigning is how a blocked
    task gets picked up again, same as an agent)."""
    t = tasks_service.get_task(task_id)
    if not t:
        raise AssignError("Task not found", status=404)
    if t.status == TaskStatus.stopped:
        raise AssignError("Task is stopped and cannot be assigned")
    return t


def _assign_flow_to_task(
    task_id: UUID, flow_id: str, params: Optional[Dict[str, Any]], *, task_to_dict,
) -> Dict[str, Any]:
    """Start a flow on a task and record it as the task's executor.

    ``flow.launcher.start_flow_run`` does its own flow/task validation (a
    missing flow or task raises ``ValueError``) and launches the flow
    subprocess, which drives the task's status itself from there on
    (``runtime/flow_run.py``); this only launches it and records the
    assignment, then moves the task off ``todo`` the same way assigning an
    agent does.
    """
    _assign_common_checks(task_id)

    from flow import launcher as flow_launcher
    try:
        run_id, _session_id = flow_launcher.start_flow_run(str(task_id), flow_id, params or {})
    except ValueError as e:
        raise AssignError(str(e), status=404)

    tasks_service.assign_executor(task_id, Executor(kind="flow", id=flow_id), params, run_id=run_id)
    tasks_service.update_task(task_id, status=TaskStatus.in_progress)
    updated = tasks_service.get_task(task_id)
    return {"task": task_to_dict(updated), "run_id": run_id, "pending_approval": False}


def _assign_team_to_task(
    task_id: UUID, team_id: str, params: Optional[Dict[str, Any]], *, task_to_dict,
) -> Dict[str, Any]:
    """Start a team run on a task.

    ``teams.launcher.start_team_run`` claims the task itself (the same
    ``assign_executor`` call ``teams.runner._claim_task`` makes today) — the
    user picked this team and pressed run, so there is nobody left to approve
    it, same as a team started from the Teams page. This does not assign the
    task a second time; it only confirms the claim landed as kind "team" (a
    launcher still writing the older agent-shaped assignment would otherwise
    leave the task's executor recorded as an agent named after the team) and
    fixes it up if not.
    """
    t = _assign_common_checks(task_id)

    goal = str((params or {}).get("goal") or t.description or t.title or "")
    from teams import launcher as team_launcher
    try:
        run = team_launcher.start_team_run(
            team_id, goal, workspace=t.workspace, task_id=str(task_id),
        )
    except ValueError as e:
        raise AssignError(str(e), status=404)

    run_id = getattr(run, "team_run_id", None)
    if run_id is None and isinstance(run, dict):
        run_id = run.get("team_run_id")

    updated = tasks_service.get_task(task_id)
    if updated is None or updated.executor is None or updated.executor.kind != "team":
        tasks_service.assign_executor(task_id, Executor(kind="team", id=team_id), params, run_id=run_id)
        updated = tasks_service.get_task(task_id)
    return {"task": task_to_dict(updated), "run_id": run_id, "pending_approval": False}


def _assign_loop_to_task(
    task_id: UUID, loop_id: str, params: Optional[Dict[str, Any]], *, task_to_dict,
) -> Dict[str, Any]:
    """Start a loop run on a task through ``loops.launcher.start_loop_run``:
    the record is written, the task recorded as executor kind ``loop`` with
    the run id, and the process spawned (or queued) before this returns."""
    t = _assign_common_checks(task_id)

    goal = str((params or {}).get("goal") or t.description or t.title or "")
    from loops.launcher import start_loop_run
    run = start_loop_run(loop_id, goal, workspace=t.workspace, task_id=str(task_id))
    run_id = getattr(run, "loop_run_id", None)

    loop_params = {"loop_id": loop_id, "workspace": t.workspace, **(params or {})}
    tasks_service.assign_executor(task_id, Executor(kind="loop", id=loop_id), loop_params, run_id=run_id)
    tasks_service.update_task(task_id, status=TaskStatus.in_progress)
    updated = tasks_service.get_task(task_id)
    return {"task": task_to_dict(updated), "run_id": run_id, "pending_approval": False}


def _assign_scenario_to_task(
    task_id: UUID, scenario_id: str, params: Optional[Dict[str, Any]], *, task_to_dict,
) -> Dict[str, Any]:
    """Start a scenario run on a task through ``playground.launcher.start_scenario_run``.

    The launcher records the assignment itself (it calls ``assign_executor``
    with the run id, the way the loop launcher does) and the run finalizes
    the task when it ends (``playground.runner.run_simulation``); this only
    launches it and moves the task off ``todo``. A scenario that cannot run
    (no roles, agents mode without docker) is a 400, not a 404: the scenario
    exists, it is its configuration that refuses.
    """
    t = _assign_common_checks(task_id)

    from playground.launcher import start_scenario_run
    try:
        run = start_scenario_run(scenario_id, workspace=t.workspace, task_id=str(task_id))
    except ValueError as e:
        status = 404 if "not found" in str(e).lower() else 400
        raise AssignError(str(e), status=status)
    run_id = getattr(run, "sim_run_id", None)

    scenario_params = {"scenario_id": scenario_id, "workspace": t.workspace, **(params or {})}
    tasks_service.assign_executor(task_id, Executor(kind="scenario", id=scenario_id),
                                  scenario_params, run_id=run_id)
    tasks_service.update_task(task_id, status=TaskStatus.in_progress)
    updated = tasks_service.get_task(task_id)
    return {"task": task_to_dict(updated), "run_id": run_id, "pending_approval": False}
