"""
The database and launch half of a delegation, executed wherever the shared
database is actually reachable.

``tools/delegation.py``'s ``delegate_task_tool`` decides *what* to delegate
(the agent, the input, the model) from inside the run that delegates. Which
process then creates the subtask and launches the delegate depends on where
that run executes:

* a subprocess on a host (the default): this module runs in the tool's own
  process, exactly as the tool itself used to do it (``DirectStateTransport``);
* a run container: the tool posts the request to the backend
  (``HttpStateTransport`` → ``POST /api/run-state/tasks/{id}/delegate``) and
  the backend runs this module. The delegate is then launched by the backend
  the same way a launch from the dashboard is, so it gets its own container
  with its own limits and environment (or goes on the run queue for a worker
  in the ``api`` role), instead of being a bare subprocess nested inside the
  parent's container, which is what happened when the tool launched from in
  there itself. It also works when the container's state directory is
  read-only (``AGENT_RUN_STATE_TRANSPORT=http``), where the tool could not
  even create the subtask row.

:func:`launch_delegation` takes the request as one JSON-able mapping and
answers with one JSON-able dict, ``{"ok": true, ...}`` or ``{"ok": false,
"error", "code", ...}``, so the same function serves the in-process call and
the route. :func:`delegation_status` is the read side the tool polls while it
waits, for the same reason.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

log = logging.getLogger(__name__)


def _err(message: str, code: str, **extra: Any) -> Dict[str, Any]:
    return {"ok": False, "error": message, "code": code, **extra}


def launch_delegation(parent_task_id: str, request: Mapping[str, Any]) -> Dict[str, Any]:
    """Create the subtask and launch the delegate on it.

    ``request`` carries: ``agent_id``, ``input``, optional ``title`` and
    ``model`` (``provider/model`` or a bare id), ``workspace`` (the caller's
    active workspace), ``caller_agent_id`` (the delegating agent, for the
    ``delegates`` allowlist and the self-delegation rule), ``depth`` (how many
    delegations deep the caller already is) and ``env`` (what the child's
    environment must carry: its depth, the parent run id).

    On success the answer has ``task`` (the subtask as the task tools render
    it), ``run`` (the run record as launched), ``requested`` (the provider and
    model asked for, if any) and ``agent`` (id and name of the delegate).
    """
    from agents.registry import get_agent, resolve_agent_id
    from common.agent_context import current_agent_id
    from common.workspace_context import filter_agents_for_workspace
    from tasks.service import CreatedBy, TaskStatus, add_subtask, assign_agent, get_task, update_task
    from tools.delegation import (DEFAULT_MAX_CONCURRENT_DELEGATES, PARENT_RUN_ENV, enabled_models,
                                  max_depth, resolve_model, running_delegate_count)
    from tools.langchain_tools import _delegation_blocked
    from tools.task_management import _task_to_dict, _uuid_from_str

    agent_id = resolve_agent_id(request.get("agent_id"))
    text = str(request.get("input") or "")
    if not agent_id or not text.strip():
        return _err("agent_id and input are required", "bad_request")

    parent = get_task(_uuid_from_str(parent_task_id))
    if parent is None:
        return _err("The current task no longer exists", "not_found", task_id=parent_task_id)

    # The delegating run works in one workspace: a parent task of another one
    # is answered like a missing task (common/workspace_scope.py), unless
    # the caller is one of the service's own agents.
    from common.workspace_scope import is_service_wide, same_workspace
    req_ws = str(request.get("workspace") or "")
    if (req_ws and not is_service_wide(request.get("caller_agent_id"))
            and not same_workspace(parent.workspace, req_ws)):
        return _err("The current task no longer exists", "not_found", task_id=parent_task_id)

    # `@coder` and the like name whoever holds the role in the workspace the
    # work happens in (agents/roles.py); the subtask records that agent.
    from agents.roles import resolve as resolve_role
    agent_id = resolve_role(agent_id, req_ws or parent.workspace)
    spec = get_agent(agent_id)
    if not spec:
        return _err("Agent not found", "not_found", agent_id=agent_id)
    ws = str(request.get("workspace") or "") or parent.workspace
    if ws and not filter_agents_for_workspace([spec], ws):
        return _err(f"Agent '{agent_id}' is not available in workspace '{ws}'", "forbidden", agent_id=agent_id)

    # The allowlist and the self-delegation rule read the delegating agent
    # from the context variable; on the backend that is the request's caller.
    caller = str(request.get("caller_agent_id") or "") or None
    token = current_agent_id.set(caller) if caller else None
    # A role in the caller's allowlist resolves in the delegation's workspace.
    from common.workspace_context import _workspace_ctx
    ws_token = _workspace_ctx.set(ws) if ws else None
    try:
        blocked = _delegation_blocked(agent_id)
    finally:
        if ws_token is not None:
            _workspace_ctx.reset(ws_token)
        if token is not None:
            current_agent_id.reset(token)
    if blocked:
        return _err(blocked, "forbidden", agent_id=agent_id)

    depth = int(request.get("depth") or 0)
    if depth + 1 >= max_depth() + 1:
        return _err(
            f"This run is already {depth} delegation(s) deep; the limit is {max_depth()}. "
            "Do the work yourself or report what you could not do.",
            "too_deep", depth=depth, max_depth=max_depth())

    # The delegating run's own concurrency limit (agents/registry.py
    # max_concurrent_delegates, or this run's own overrides): how many of its
    # delegated subtasks may be running at once. Counted against the parent
    # run's live children (tools/delegation.running_delegate_count); a
    # wait=false launch still counts until it finishes.
    parent_run_id = str((request.get("env") or {}).get(PARENT_RUN_ENV) or "")
    if parent_run_id:
        try:
            delegate_limit = int(request.get("max_concurrent_delegates") or DEFAULT_MAX_CONCURRENT_DELEGATES)
        except (TypeError, ValueError):
            delegate_limit = DEFAULT_MAX_CONCURRENT_DELEGATES
        delegate_limit = max(1, min(32, delegate_limit))
        running = running_delegate_count(parent_run_id)
        if running >= delegate_limit:
            return _err(
                f"This run already has {running} delegated subtask(s) running; the limit is "
                f"{delegate_limit}. Wait for one to finish, or raise max_concurrent_delegates "
                "for this agent or run.",
                "too_many_delegates", running=running, max_concurrent_delegates=delegate_limit)

    requested: Dict[str, str] = {}
    model = request.get("model")
    if model and str(model).strip():
        try:
            provider_id, model_id = resolve_model(str(model))
        except ValueError as exc:
            return _err(str(exc), "bad_model", models=[m["id"] for m in enabled_models()])
        requested = {"provider": provider_id, "model": model_id}

    title = str(request.get("title") or "").strip() or text.strip().splitlines()[0][:80]
    child = add_subtask(parent.id, title, description=text.strip(), created_by=CreatedBy.orchestrator)
    if child.status == TaskStatus.blocked:
        return _err(
            f"The subtask was created blocked ({child.blocked_reason}); unblock the parent task first.",
            "blocked", task_id=str(child.id))
    try:
        update_task(child.id, routing_reason=f"delegated by {parent.assigned_agent_type or 'the task agent'}"
                    + (f" on {requested['provider']}/{requested['model']}" if requested else ""))
    except Exception:  # noqa: BLE001 - the note is decoration
        log.debug("routing reason update failed for %s", child.id, exc_info=True)

    session_id = getattr(parent, "session_id", None)
    params: Dict[str, Any] = dict(requested)

    from agents.agent_launcher import preregister_run, start_run
    from common.budget import BudgetExceededError
    from common.entity_sink import record_entity
    from runtime.entity_launch import child_env

    extra_env = request.get("env")
    extra_env = {str(k): str(v) for k, v in extra_env.items()} if isinstance(extra_env, Mapping) else {}

    run_id = preregister_run(str(child.id), agent_id, session_id=session_id)
    try:
        with child_env(extra_env):
            run_id, _ = start_run(str(child.id), agent_id, params, run_id=run_id)
    except BudgetExceededError as exc:
        update_task(child.id, status=TaskStatus.blocked, blocked_reason=str(exc))
        return _err(str(exc), "budget", task_id=str(child.id))
    if parent_run_id:
        # So a later delegation from the same parent run can count this one
        # among its running children (running_delegate_count above).
        try:
            from managers.run_manager import update_run as _update_run_record
            _update_run_record(run_id, {"parent_run_id": parent_run_id})
        except Exception:  # noqa: BLE001 - the concurrency count degrades to "none running", never blocks the launch
            log.debug("could not stamp parent_run_id on delegated run %s", run_id, exc_info=True)
    assign_agent(child.id, agent_id, params, run_id=run_id)
    update_task(child.id, status=TaskStatus.in_progress)
    if session_id:
        try:
            from common.session_service import add_run_to_session
            add_run_to_session(session_id, run_id)
        except Exception:  # noqa: BLE001 - the session link is best-effort, as in start_agent_tool
            log.debug("session link failed for run %s", run_id, exc_info=True)
    record_entity("task", str(child.id), "delegated", title)

    from managers.run_manager import get_run_by_id
    run = get_run_by_id(run_id) or {"run_id": run_id, "status": "pending"}
    fresh = get_task(child.id) or child
    return {
        "ok": True,
        "task": _task_to_dict(fresh),
        "run": run,
        "requested": requested,
        "agent": {"id": spec.id, "name": spec.name},
    }


def delegation_status(task_id: str, run_id: Optional[str]) -> Dict[str, Any]:
    """What the waiting tool needs about a delegated subtask: the run record,
    the task as the task tools render it, and the task's stored result (the
    run record's own ``output`` is the tool's fallback when it is empty)."""
    from managers.run_manager import get_run_by_id
    from tasks.service import get_task, get_task_result
    from tools.task_management import _task_to_dict, _uuid_from_str

    run = get_run_by_id(run_id) if run_id else None
    task_dict: Optional[Dict[str, Any]] = None
    output = ""
    try:
        task = get_task(_uuid_from_str(task_id))
        if task is not None:
            task_dict = _task_to_dict(task)
            output = str(get_task_result(task.id) or "")
    except Exception:  # noqa: BLE001 - a missing or unreadable task leaves the run record to speak
        log.debug("delegation status: task lookup failed for %s", task_id, exc_info=True)
    return {"run": run, "task": task_dict, "output": output}
