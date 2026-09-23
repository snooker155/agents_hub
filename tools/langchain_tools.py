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

from managers import run_manager
from agents.registry import (
    AgentSpec,
    add_agent as reg_add_agent,
    get_agent as reg_get_agent,
    list_agents as reg_list_agents,
    remove_agent as reg_remove_agent,
)
from managers.run_manager import (
    stop_run as rm_stop_run,
)
from agents.agent_launcher import (
    start_run as rm_start_run,
    preregister_run as rm_preregister_run,
)
from common.entity_sink import record_entity
from common.workspace_context import (
    filter_agents_for_workspace,
    task_in_workspace,
)
from common.session_service import get_or_create_task_session, add_run_to_session
from tasks.context import augment_params_with_block_reason
from tasks.service import (
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
from tools._json import json_err as _json_err, json_ok as _json_ok



# -------------------- Agent coordination tools --------------------

def _caller_delegates() -> Optional[set]:
    """Delegation allowlist of the agent currently executing, or None.

    Returns the set of agent ids the running agent is allowed to delegate to
    (AgentSpec.delegates). Returns None when there is no caller in context or
    the caller has no restriction configured — callers treat None as "no
    restriction" and skip filtering entirely.
    """
    from common.agent_context import current_agent_id
    caller_id = current_agent_id.get()
    if not caller_id:
        return None
    caller = reg_get_agent(caller_id)
    if not caller or not caller.delegates:
        return None
    return set(caller.delegates)


def _filter_delegatable(specs: List[Any]) -> List[Any]:
    """Restrict specs to the running agent's delegation allowlist (if any)."""
    allow = _caller_delegates()
    if allow is None:
        return specs
    return [s for s in specs if s.id in allow]


# Appended to every delegation-block reason. A blocked delegation is terminal
# for this call — retrying it, or churning through other agents, is what turns a
# single refusal into an infinite loop. This guidance tells the model to stop and
# report instead. Kept in the tool (not agent instructions) so every delegating
# agent gets the same failure protocol.
_DELEGATION_STOP_GUIDANCE = (
    "This is a permanent restriction for this call: do not retry it, and do not "
    "loop trying other agents. If no other agent clearly fits the request, stop "
    "now and reply to the user, stating plainly what you could not do and why."
)


def _delegation_blocked(agent_id: str) -> Optional[str]:
    """Return an error reason if the running agent may not delegate to agent_id.

    None means the delegation is permitted (no restriction, or target allowed).
    A returned reason always ends with _DELEGATION_STOP_GUIDANCE so the model
    treats the block as terminal and reports back instead of retry-looping.
    """
    from common.agent_context import current_agent_id
    caller_id = current_agent_id.get()
    # Self-delegation is off by default: a self-run recurses the same agent and
    # never produces a distinct worker result. An agent may opt in by setting
    # allow_self_delegation, in which case a self-target is explicitly permitted
    # (and bypasses the delegates allowlist below — it is targeting itself).
    if caller_id and agent_id == caller_id:
        caller = reg_get_agent(caller_id)
        if caller and getattr(caller, "allow_self_delegation", False):
            return None
        return (
            f"Agent '{caller_id}' cannot delegate to itself (self-delegation is "
            f"disabled; enable it on the agent to allow recursion). "
            f"{_DELEGATION_STOP_GUIDANCE}"
        )
    allow = _caller_delegates()
    if allow is None or agent_id in allow:
        return None
    return (
        f"Agent '{caller_id}' is not allowed to delegate to '{agent_id}'. "
        f"Allowed delegation targets: {sorted(allow)}. {_DELEGATION_STOP_GUIDANCE}"
    )



@tool("list_agents_tool")
def list_agents_tool() -> str:
    """List all available agents from the registry. Returns JSON with an array of agents.

    Each agent has id/name/description. The entry for the calling agent is flagged
    with `is_self: true`; when that agent has self-delegation enabled it also carries
    `self_delegation: true`, meaning you may pass its id to run_agent_tool to recurse.
    """
    try:
        from common.agent_context import current_agent_id
        caller_id = current_agent_id.get()
        ws = _active_workspace()
        specs = filter_agents_for_workspace(reg_list_agents(), ws)
        specs = _filter_delegatable(specs)
        agents = []
        for s in specs:
            entry = {"id": s.id, "name": s.name, "description": s.description}
            if caller_id and s.id == caller_id:
                entry["is_self"] = True
                # Only advertise self-delegation when it is actually enabled, so the
                # model doesn't attempt a self-call the gate will refuse.
                if getattr(s, "allow_self_delegation", False):
                    entry["self_delegation"] = True
            agents.append(entry)
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
        blocked = _delegation_blocked(agent_id)
        if blocked:
            return _json_err(blocked, code="forbidden", extra={"agent_id": agent_id})
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

        # If the task is currently blocked (e.g. the reviewer rejected the prior
        # attempt), carry the block reason into the worker's input so the next
        # agent knows exactly what to fix. Done here, while the reason is still
        # set: starting the run flips the task to in_progress, which clears it.
        params = augment_params_with_block_reason(task, params)

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
                    task_id=str(task.id),
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
        record_entity("task", str(task.id), "assigned", (task.title or "").strip())
        return _json_ok({
            "message": message,
            "run_id": run_id,
            "assignment_mode": assignment_mode,
            "task": _task_to_dict(updated) if updated else None,
            "agent": spec.to_dict(),
        })
    except Exception as e:
        return _json_err(f"Failed to assign agent: {e}")


class InvokeAgentInput(BaseModel):
    agent_id: str = Field(..., min_length=1, description="Agent identifier from the registry")
    input: str = Field(
        ...,
        min_length=1,
        description=(
            "The full, self-contained instruction for the delegated agent. It does "
            "NOT see this conversation, your own instructions, or the previous "
            "steps, so include every piece of context and every constraint it needs. "
            "For a multi-level or recursive task, spell out what the agent must do "
            "AND that it should delegate further — e.g. state the remaining depth "
            "and how many sub-agents to spawn — or the recursion stops at this level."
        ),
    )
    workspace: Optional[str] = Field(
        None,
        description=(
            "Leave empty. The delegated agent runs in the current workspace by "
            "default; only set this to intentionally target a DIFFERENT workspace. "
            "Do not pass 'default' unless you truly mean the workspace named "
            "'default' — a stray value here moves the sub-agent out of the current "
            "workspace and away from its files."
        ),
    )


@tool("run_agent_tool", args_schema=InvokeAgentInput)
def run_agent_tool(agent_id: str, input: str, workspace: Optional[str] = None) -> str:
    """Delegate a request to another agent and get its result back, without a task.

    Runs `agent_id` on the given `input` synchronously, to completion, and returns
    the worker's output in this tool's result so YOU (the orchestrator) can act on
    it. The delegation appears in the chat as a tool call. No task is created and
    no approval gate applies — use this for a free-form chat request where the
    user just wants agents run. For tracked, multi-step work that needs a task
    record or approval, use assign_agent_tool + start_agent_tool.

    After this returns, review the worker's output (in the `output` field) and
    decide the next step yourself: if the request is fully handled, write a short
    final message to the user summarising that output; if another agent should
    continue the work (e.g. a reviewer or follow-up specialist), call
    run_agent_tool again with that agent and an input built from this output.
    Each run has already finished when the tool returns — never poll or wait.

    You may target your OWN agent id here when self-delegation is enabled for you
    (list_agents_tool marks your entry with `self_delegation: true`). This is how
    you recurse — hand a sub-goal back to a fresh instance of yourself. When it is
    not enabled, a self-target is refused; that is the only case where calling
    yourself fails.

    The child sees ONLY the `input` you pass — never this conversation or your
    instructions. So for a multi-level or recursive task the continuation must
    live inside `input`: state what the child should do and, if the recursion is
    meant to go deeper, that it should itself call run_agent_tool again, with the
    remaining depth and fan-out spelled out (e.g. "then delegate this same task to
    2 more agents, each at depth N-1"). Leave `workspace` empty so the child stays
    in the current workspace.

    If a delegation is refused (the result has `ok: false` — e.g. self-delegation
    is disabled for you, or the target is not an allowed/available agent), treat
    it as terminal: do NOT retry the same call or churn through other agents
    hunting for one that is accepted. Pick a different agent only when one clearly
    fits the request; if none does, stop and reply to the user, stating what you
    could not do and why. Repeatedly retrying delegations is a loop, not progress.

    Returns JSON with the worker's `output`, `run_id`, `session_id`, agent info,
    and whether the run succeeded.
    """
    try:
        # Taskless delegation is only for free-form requests (e.g. chat). When the
        # orchestrator is running inside a tracked task, current_task_id is set and
        # the established assign/start task flow must be used instead.
        from common.agent_context import current_task_id
        active_task = current_task_id.get()
        if active_task:
            return _json_err(
                "run_agent_tool is for taskless (chat) delegation only. This run is "
                f"bound to task {active_task}; use assign_agent_tool + start_agent_tool instead.",
                code="task_context",
                extra={"task_id": active_task},
            )
        spec = reg_get_agent(agent_id)
        if not spec:
            return _json_err("Agent not found", code="not_found", extra={"agent_id": agent_id})
        # The delegated child inherits the caller's current workspace: taskless
        # chat delegation should stay where the conversation is, with its files.
        # The active workspace therefore wins over the `workspace` argument (which
        # models tend to fill spuriously with "default", pushing the sub-agent out
        # of the current workspace); the argument is only a fallback for callers
        # with no active workspace context.
        ws = _active_workspace() or workspace
        if ws and not filter_agents_for_workspace([spec], ws):
            return _json_err(
                f"Agent '{agent_id}' is not available in workspace '{ws}'",
                code="forbidden",
            )
        blocked = _delegation_blocked(agent_id)
        if blocked:
            return _json_err(blocked, code="forbidden", extra={"agent_id": agent_id})

        from agents.agent_factory import create_agent
        from agents.agent_invoke import invoke_agent
        from agents.callbacks import FileStatsCallback
        from managers.run_manager import (
            run_log_path,
            _utc_now_iso,
            close_run,
            close_run_from_result,
            new_unique_run_id,
            open_run,
            update_run,
        )
        from workspace import resolve_workspace_arg
        from common.agent_context import current_session_id as _sess_ctx

        ws_path, _ws_name = resolve_workspace_arg(ws)
        # Reuse the orchestrator's chat session so the worker run is grouped with
        # this conversation rather than spawning an orphan session.
        session_id = _sess_ctx.get() or os.environ.get("AGENT_SESSION_ID") or None

        run_id = new_unique_run_id()
        title = input.strip()[:60]
        started = _utc_now_iso()

        # The worker runs in-process — no subprocess stdout tee, no SSE stream —
        # so scaffold a chat-format log file and write tool/LLM markers into it
        # via FileStatsCallback. The chat-log parser then surfaces this run's
        # logs and insights like any other chat message.
        msg_id = str(uuid4())[:8]
        log_file = run_log_path(run_id)
        log_file.write_text("\n".join([
            f"=== Chat message  run_id={run_id} ===",
            f"Started   : {started}",
            f"Agent     : {agent_id}",
            f"Workspace : {ws or '—'}",
            f"Session ID: {session_id or '—'}",
            "Origin    : delegation (run_agent_tool)",
            f"Title     : {title}",
            "",
            f"=== Message at {started} id={msg_id} ===",
            f"Agent: {agent_id}",
            "",
            "--- User message ---",
            input,
            "",
            "--- Agent response (stream) ---",
            "",
        ]), encoding="utf-8")

        open_run(
            run_id,
            agent_id,
            task_id=None,
            session_id=session_id,
            session_type="chat",
            message_origin="delegation",
            channel="chat_delegate",
            workspace=ws or None,
            title=title,
            status="running",
            log_file=str(log_file),
            input=input,
        )

        # A delegate runs under its own secret scope: its own allowlist, the
        # same workspace and the same person as the delegating run.
        from common import secrets as _secrets
        _parent_scope = _secrets.active_scope()
        _scope = _secrets.activate(ws or "", agent_id,
                                   _parent_scope[2] if _parent_scope else None)
        _scope.__enter__()
        try:
            worker = create_agent(agent_id, workspace=ws_path)
        except Exception as e:
            _scope.__exit__(None, None, None)
            # Close the record here, or a build failure leaves it "running" forever.
            close_run(run_id, status="failed", exit_code=1, error=f"create_agent failed: {e}")
            return _json_err(
                f"Failed to build agent '{agent_id}': {e}",
                code="worker_failed",
                extra={"run_id": run_id, "agent_id": agent_id, "succeeded": False},
            )
        try:
            update_run(run_id, {"provider": worker.provider or "", "model": worker.model or ""})
        except Exception:
            pass
        # Seed the input context so the worker's system prompt is visible in the
        # dashboard while it runs (replaced by the full context at close).
        from managers.run_manager import seed_run_input_context
        seed_run_input_context(run_id, getattr(worker, "system_prompt", "") or "", input)

        stats = FileStatsCallback(log_file)
        # A user stop (chat stop button cascades to delegation runs) flips this
        # run's status to "stop"; the callback then aborts the worker at the
        # next LLM/tool boundary instead of letting it run to completion.
        from agents.callbacks import RunStopCallback
        stop_cb = RunStopCallback(run_id)
        # When this delegation runs inside a streaming chat turn, forward the
        # child's tool/thought events onto the parent's SSE stream so the browser
        # renders a live nested block. No-op outside streaming (emitter is None).
        from common import stream_sink
        from agents.callbacks import DelegationStreamCallback
        emitter = stream_sink.get_emitter()
        deleg_scope = None
        deleg_depth = 0
        extra_cbs = [stop_cb]
        if emitter is not None:
            parent_run_id = stream_sink.current_run_id()
            deleg_scope = stream_sink.delegation_scope(run_id)
            deleg_depth = deleg_scope[2]
            emitter({
                "type": "delegation_start",
                "run_id": run_id,
                "parent_run_id": parent_run_id,
                "agent_id": agent_id,
                "agent_name": spec.name,
                "depth": deleg_depth,
                "input": input,
                "title": title,
            })
            extra_cbs.append(
                DelegationStreamCallback(
                    emitter, run_id=run_id, agent_id=agent_id, depth=deleg_depth
                )
            )

        # run_id is tracked via open_run/close_run_from_result below; we don't pass
        # it into invoke_agent because StandardAgent.run() takes no positional run_id.
        try:
            invocation = invoke_agent(
                worker, input, stats=stats, extra_callbacks=extra_cbs, catch_exceptions=True
            )
        finally:
            # Leave the delegation scope before emitting the end event, so a nested
            # delegation's depth/parent bookkeeping is fully unwound.
            if deleg_scope is not None:
                stream_sink.reset_scope(deleg_scope)
            _scope.__exit__(None, None, None)
        result = invocation.result
        stopped = stop_cb.cancelled
        if stopped:
            close_run(
                run_id, status="stopped", exit_code=1,
                error="stopped by user", process=invocation.process,
            )
        else:
            close_run_from_result(run_id, result, process=invocation.process)

        ok = bool(getattr(result, "ok", False)) and not stopped
        output = (getattr(result, "agent_output", None) or "").strip()
        error = str(getattr(result, "error", None) or "") if not ok else ""

        if emitter is not None:
            emitter({
                "type": "delegation_end",
                "run_id": run_id,
                "depth": deleg_depth,
                "agent_id": agent_id,
                "ok": ok,
                "stopped": stopped,
                "output": output if ok else "",
                "error": error if not ok else "",
                "duration_ms": invocation.duration_ms,
            })

        # Footer mirrors the chat driver's, so the parser picks up the response
        # text and the message summary (tokens / tool calls / duration).
        if stopped:
            stats.write("(stopped by user)")
        else:
            stats.write(output if ok else f"Error: {error or 'unknown error'}")
        stats.write(
            f"[message_summary] id={msg_id} "
            f"inbound_tokens={stats.prompt_tokens} "
            f"outbound_tokens={stats.completion_tokens} "
            f"total_tokens={stats.total_tokens} "
            f"tool_calls={stats.tool_calls} "
            f"duration_ms={invocation.duration_ms}"
        )
        stats.write("")
        stats.write(f"Finished: {_utc_now_iso()}")
        stats.write(f"Status  : {'stopped' if stopped else 'completed' if ok else 'failed'}")
        stats.close()

        if stopped:
            return _json_err(
                f"Worker '{agent_id}' was stopped by the user. Do not retry.",
                code="worker_stopped",
                extra={"run_id": run_id, "agent_id": agent_id, "succeeded": False},
            )

        if ok:
            return _json_ok({
                "message": (
                    "Worker finished. Review this output and decide: if the request "
                    "is fully handled, summarise it for the user; if follow-up work "
                    "is needed, chain another agent with run_agent_tool."
                ),
                "output": output,
                "run_id": run_id,
                "session_id": session_id,
                "agent": spec.to_dict(),
                "workspace": ws or None,
                "succeeded": True,
            })
        return _json_err(
            f"Worker '{agent_id}' failed: {error or 'unknown error'}",
            code="worker_failed",
            extra={"run_id": run_id, "agent_id": agent_id, "succeeded": False},
        )
    except Exception as e:
        return _json_err(f"Failed to invoke agent: {e}")


# -------------------- Flow tools --------------------

@tool("list_flows_tool")
def list_flows_tool() -> str:
    """List the agent flows available in the active workspace.

    A flow is a saved multi-agent graph that can be executed end to end. Use
    this when the user wants to run a flow, so you can present the choices and
    ask them which one to run. Returns JSON with an array of flows, each with
    `id`, `name`, `description`, and `running` (whether an instance is active).

    Only flows belonging to the active workspace (or with no workspace set) are
    returned. After listing, ASK the user to pick a target flow and confirm —
    then call run_flow_tool with the chosen flow_id.
    """
    try:
        from flow import store as flow_store
        ws = _active_workspace()
        flows = []
        for f in flow_store.list_flows():
            f_ws = f.get("workspace")
            if ws and f_ws and f_ws != ws:
                continue
            flows.append({
                "id": f.get("id"),
                "name": f.get("name") or f.get("id"),
                "description": f.get("description") or "",
                "workspace": f_ws,
                "running": bool(f.get("running")),
            })
        return _json_ok({"flows": flows, "workspace": ws})
    except Exception as e:
        return _json_err(f"Failed to list flows: {e}")


class RunFlowInput(BaseModel):
    flow_id: str = Field(..., min_length=1, description="ID of the flow to run (from list_flows_tool)")
    description: Optional[str] = Field(
        None, description="Optional run description / input passed to the flow"
    )
    user_approved: bool = Field(
        False,
        description=(
            "Must be True. Set only after the user has explicitly selected this "
            "flow and approved running it. Never set True on the user's behalf."
        ),
    )
    create_task: bool = Field(
        False,
        description=(
            "Leave False for a normal taskless run (the default). Set True ONLY "
            "when the user explicitly asks to create/track a task for this flow run."
        ),
    )


@tool("run_flow_tool", args_schema=RunFlowInput)
def run_flow_tool(
    flow_id: str,
    description: Optional[str] = None,
    user_approved: bool = False,
    create_task: bool = False,
) -> str:
    """Run an agent flow in the active workspace, AFTER the user has approved it.

    Taskless by default: the run is ephemeral (tracked only as a background run /
    session, like run_agent_tool) and is NOT surfaced as a user task. Only when
    the user explicitly asks to create or track a task should you pass
    create_task=True.

    Approval gate: this tool refuses unless `user_approved` is True. Before
    calling it you MUST (1) call list_flows_tool, (2) ask the user which flow to
    run and to confirm, and (3) only once they have explicitly chosen a target
    flow and approved, call this with that flow_id and user_approved=True. Do not
    set user_approved yourself without a clear "yes, run it" from the user.

    Launches the flow as a background process. Returns JSON with the run_id,
    session_id, and task_id (an internal task that drives execution; omitted from
    the user unless create_task was requested). The flow runs asynchronously —
    report that it has started; do not poll for completion here.
    """
    try:
        if not user_approved:
            return _json_err(
                "Flow run not approved. First call list_flows_tool, ask the user to "
                "select a target flow and confirm, then call run_flow_tool again with "
                "user_approved=True.",
                code="approval_required",
                extra={"flow_id": flow_id},
            )

        from flow import store as flow_store
        from flow import launcher as flow_launcher

        flow = flow_store.get_flow(flow_id)
        if not flow:
            return _json_err("Flow not found", code="not_found", extra={"flow_id": flow_id})

        ws = _active_workspace()
        flow_ws = flow.get("workspace")
        if ws and flow_ws and flow_ws != ws:
            return _json_err(
                f"Flow '{flow_id}' belongs to workspace '{flow_ws}', not the active "
                f"workspace '{ws}'",
                code="forbidden",
            )

        ws_name = flow_ws or ws
        desc = (description or flow.get("description") or "").strip()
        flow_name = flow.get("name") or flow_id

        # The flow runtime is task-driven (it finalizes a task record on completion),
        # so a backing task always exists. For a taskless run we mark it
        # orchestrator-created so it is not presented as a user task; only when the
        # user explicitly asks do we create a user-facing task.
        from tasks.service import create_task as svc_create_task
        task = svc_create_task(
            title=f"Flow: {flow_name}",
            description=desc,
            workspace=ws_name,
            created_by=CreatedBy.user if create_task else CreatedBy.orchestrator,
        )

        params = {"workspace": ws_name, "description": desc}
        run_id, session_id = flow_launcher.start_flow_run(str(task.id), flow_id, params)
        svc_assign_agent(task.id, "flow-custom-graph", params, run_id=run_id)

        payload: Dict[str, object] = {
            "message": (
                f"Flow '{flow_name}' started. It runs in the background — report this "
                "to the user; do not poll for completion."
            ),
            "flow_id": flow_id,
            "flow_name": flow_name,
            "run_id": run_id,
            "session_id": session_id,
            "workspace": ws_name,
            "taskless": not create_task,
        }
        # Only surface the task id when the user asked to track it; otherwise it is
        # an internal execution record and not something to report.
        if create_task:
            payload["task_id"] = str(task.id)
        return _json_ok(payload)
    except Exception as e:
        return _json_err(f"Failed to run flow: {e}")


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
                        run_id=run_id,
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
        record_entity("task", str(task.id), "started", (task.title or "").strip())
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
        record_entity("task", str(task.id), "stopped", (task.title or "").strip())
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

        # In fire-and-forget mode (wait_for_completion=false) an orchestrator that
        # just started the agent must not keep polling: while the worker is still
        # running, clamp to "finished" so the LLM ends its turn. A worker that has
        # GENUINELY finished or failed (the continuation/monitor case) is not
        # clamped — the orchestrator must proceed to Step 5 and act on the result.
        _task_ws = str(getattr(task, "workspace", "") or "") if task else ""
        _wait_mode = _get_wait_for_completion(_task_ws or ws)
        clamped_start_turn = False
        if agent_running and not agent_failed and _wait_mode is False:
            agent_finished = True
            agent_running = False
            clamped_start_turn = True

        assigned_agent = str(getattr(task, "assigned_agent_type", "") or "") if task else ""
        # During continuation processing the task's assignment points at the
        # orchestrator itself; report the worker whose run was inspected instead.
        if assigned_agent == "orchestrator" and status:
            _worker_agent = str(status.get("agent_id") or "")
            if _worker_agent and _worker_agent != "orchestrator":
                assigned_agent = _worker_agent
        routing_reason = str(getattr(task, "routing_reason", "") or "") if task else ""
        blocked_reason = str(getattr(task, "blocked_reason", "") or "") if task else ""

        if agent_failed:
            message = (
                "DONE. The agent run failed or the task is blocked. Go to Step 5: "
                "handle the block — re-assign a suitable fixing agent, or report to "
                "the user if resolving it needs their input."
            )
        elif clamped_start_turn:
            message = (
                "DONE for this turn. The agent was started and runs in the background; "
                "a follow-up fires automatically when it finishes. Stop all tool calls "
                "and report to the user that the agent was started."
            )
        elif agent_finished and task_status == "done":
            message = (
                "DONE. The task is already finalised (status done). Stop all tool "
                "calls and summarise the outcome: which agents ran and what was "
                "verified."
            )
        elif agent_finished and task_status == "reviewed":
            message = (
                "DONE. The review passed — the task is reviewed and complete. Go to "
                "Step 5: call the update_task tool (NOT update_scheduled) with "
                "status=done for this task, then summarise the outcome. Do not chain "
                "another agent."
            )
        elif agent_finished:
            message = (
                "DONE. The worker agent finished. Go to Step 5: decide the next action — "
                "chain the next agent (e.g. a reviewer) with assign_agent_tool + "
                "start_agent_tool, or, if nothing is left to chain in monitor mode, call "
                "the update_task tool (NOT update_scheduled) with status=resolved and "
                "summarise the outcome."
            )
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
            "blocked_reason": blocked_reason,
            "followup_mode": _get_followup_mode(_task_ws or ws),
            "wait_for_completion": _wait_mode,
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
            from common.workspace_context import resolve_active_workspace
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

        record_entity("agent", agent_id, "created", name)
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
    episodic_write_enabled: Optional[bool] = Field(None, description="Enable or disable the episodic write tool (record_episode). Other memory (recall/remember/recall_episodes) is unaffected.")
    provider: Optional[str] = Field(None, description="Model provider override; empty string clears")
    model: Optional[str] = Field(None, description="Model override; empty string clears")
    base_url: Optional[str] = Field(None, description="Provider base URL override; empty string clears")
    temperature: Optional[float] = Field(None, description="Temperature override")
    max_tokens: Optional[int] = Field(None, ge=1, description="Max tokens override")
    reasoning: Optional[Dict[str, Any]] = Field(
        None,
        description="Reasoning settings, e.g. {'think_enabled': true, 'think_mode': 'deep', 'thinking_level': 'high', 'plan_enabled': true, 'plan_format': 'bullet'}. 'thinking_level' (off|low|medium|high) is the native model reasoning parameter, separate from the 'think' scratchpad tool.",
    )
    delegates: Optional[List[str]] = Field(
        None,
        description="Delegation allowlist: agent ids this agent may delegate to / see. Pass an empty list to lift any restriction (delegate to any workspace agent).",
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
        record_entity("agent", agent_id, "viewed", spec.name or "")
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
    episodic_write_enabled: Optional[bool] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    reasoning: Optional[Dict[str, Any]] = None,
    delegates: Optional[List[str]] = None,
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
            episodic_write_enabled=bool(episodic_write_enabled) if episodic_write_enabled is not None else spec.episodic_write_enabled,
            reasoning=dict(reasoning) if reasoning is not None else dict(spec.reasoning or {}),
            delegates=(
                [d.strip() for d in delegates if str(d).strip()]
                if delegates is not None else list(spec.delegates or [])
            ),
        )

        before = spec.to_dict()
        reg_add_agent(new_spec)
        after = new_spec.to_dict()
        for key in ("name", "description", "domain", "tools", "capacity", "memory_type", "memory_data", "skills_enabled", "episodic_write_enabled", "provider", "model", "base_url", "temperature", "max_tokens", "reasoning", "delegates"):
            if before.get(key) != after.get(key):
                changed.append(key)

        if not changed:
            record_entity("agent", agent_id, "viewed", new_spec.name or "")
            return _json_ok({"agent": after, "message": f"Agent '{agent_id}' unchanged"})

        record_entity("agent", agent_id, "updated", new_spec.name or "")
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
        protected = {"orchestrator", "decomposer", "agent_creator", "flow_creator"}
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
    "run_agent_tool",
    "list_flows_tool",
    "run_flow_tool",
    "reject_assignment_tool",
    "stop_agent_tool",
    "get_agent_status_tool",
    "wait_for_agent_tool",
    "create_agent_tool",
    "get_agent_tool",
    "modify_agent_tool",
    "delete_agent_tool",
]
