"""
Delegation inside a task: hand one piece of the work to another agent, on a
model of the caller's choosing, and get its output back.

``run_agent_tool`` (tools/langchain_tools.py) is the chat form of delegation:
it runs the other agent in process, with no task record, and is refused inside
a tracked task on purpose, so that tracked work stays visible. This module is
the task form. ``delegate_task_tool`` creates a **subtask** of the task the
caller is working, launches the chosen agent on it through the ordinary
launcher (a real run: its own process or container, the environment, the
money cap, the live stream, the log), and, by default, waits for that run to
finish and returns the result the way the caller would read it with
``get_task_result``. The subtask stays under the parent on the task page, so
what was delegated, to whom, on which model and what came back is all on
record.

The model is the part that makes this the shape of a lead handing work to a
colleague rather than a recursion: ``model`` names one of the catalog's
enabled models (``provider/model``, or a bare id when it is unambiguous;
``list_models_tool`` prints them), and the run is built with that model in
place of the delegate's own. A cheap model for a mechanical part, a stronger
one for a hard part, the same one otherwise.

Limits, all of which the tool states in its refusal:

* only inside a task (``common.agent_context.current_task_id``);
* the delegate must exist, be available in the workspace, and be allowed by
  the caller's ``delegates`` list and the self-delegation rule
  (``tools.langchain_tools._delegation_blocked``);
* nesting stops at ``AGENTS_HUB_DELEGATION_MAX_DEPTH`` levels (3 by
  default): the depth travels to the child in its environment;
* the workspace's hard budget cap applies to the launch as to any run, and
  the subtask inherits the parent task's per-run cap and environment.

Waiting is a poll of the run record every two seconds, bounded by
``timeout_seconds`` (``AGENTS_HUB_DELEGATION_WAIT``, 900 s by default). A
parent run that is stopped while it waits stops the child too, so a Stop on
the task page ends the whole tree. ``wait=false`` returns as soon as the run
is launched; ``get_agent_status_tool`` and ``get_task_result`` on the subtask
id then read the outcome, exactly as for any other task.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools._json import json_err as _json_err, json_ok as _json_ok

log = logging.getLogger(__name__)

#: How deep a chain of delegations may go. The first task run is depth 0.
MAX_DEPTH_ENV = "AGENTS_HUB_DELEGATION_MAX_DEPTH"
DEFAULT_MAX_DEPTH = 3
#: The child's depth, put into its environment by the launch.
DEPTH_ENV = "AGENTS_HUB_DELEGATION_DEPTH"
#: The run that delegated, for the child's record and for anything that
#: wants to draw the tree.
PARENT_RUN_ENV = "AGENTS_HUB_DELEGATION_PARENT_RUN"
#: Default and ceiling for how long the tool waits on the child.
WAIT_ENV = "AGENTS_HUB_DELEGATION_WAIT"
DEFAULT_WAIT_SECONDS = 900.0
MAX_WAIT_SECONDS = 4 * 3600.0
POLL_SECONDS = 2.0

#: Run statuses that mean the child is not finished.
LIVE_STATUSES = frozenset({
    "pending", "queued", "leased", "assigned", "starting", "running", "stop", "awaiting_approval",
})


def max_depth() -> int:
    try:
        return max(1, int(os.environ.get(MAX_DEPTH_ENV, "") or DEFAULT_MAX_DEPTH))
    except ValueError:
        return DEFAULT_MAX_DEPTH


def current_depth() -> int:
    """How many delegations deep this run already is (0 for a task run a
    person or the scheduler started)."""
    try:
        return max(0, int(os.environ.get(DEPTH_ENV, "") or 0))
    except ValueError:
        return 0


def default_wait_seconds() -> float:
    try:
        return float(os.environ.get(WAIT_ENV, "") or DEFAULT_WAIT_SECONDS)
    except ValueError:
        return DEFAULT_WAIT_SECONDS


# ── the catalog, as an agent may pick from it ────────────────────────────────

def enabled_models() -> List[Dict[str, Any]]:
    """Every enabled catalog model as ``{id, provider, model, context_window}``,
    in catalog order. ``id`` is ``provider/model``, the form ``/v1`` serves
    and the form ``delegate_task_tool`` takes."""
    from providers.catalog import load_catalog_raw
    catalog = load_catalog_raw() or {}
    out: List[Dict[str, Any]] = []
    seen = set()
    for provider, entry in catalog.items():
        for record in (entry or {}).get("models", []) or []:
            model_id = str(record.get("id") or "").strip()
            if not model_id or not record.get("enabled"):
                continue
            key = f"{provider}/{model_id}"
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "id": key, "provider": str(provider), "model": model_id,
                "context_window": int(record.get("context_window") or 0),
            })
    return out


def resolve_model(spec: str, models: Optional[List[Dict[str, Any]]] = None) -> Tuple[str, str]:
    """``(provider, model)`` for a catalog id, or raise ``ValueError`` naming
    what is available. ``provider/model`` first; a bare model id is accepted
    when exactly one enabled model has it."""
    wanted = str(spec or "").strip()
    models = enabled_models() if models is None else models
    if not wanted:
        raise ValueError("model is empty")
    for m in models:
        if m["id"] == wanted:
            return m["provider"], m["model"]
    bare = [m for m in models if m["model"] == wanted]
    if len(bare) == 1:
        return bare[0]["provider"], bare[0]["model"]
    if len(bare) > 1:
        raise ValueError(
            f"'{wanted}' is served by several providers; name one: "
            + ", ".join(m["id"] for m in bare))
    available = ", ".join(m["id"] for m in models) or "none"
    raise ValueError(f"'{wanted}' is not an enabled catalog model; available: {available}")


def _workspace_default_model(ws: Optional[str]) -> Dict[str, str]:
    if not ws:
        return {"provider": "", "model": ""}
    try:
        from workspace import get_workspace_default_model_config, get_workspace_metadata
        return get_workspace_default_model_config(get_workspace_metadata(ws) or {})
    except Exception:  # noqa: BLE001 - a workspace without metadata simply has no default
        log.debug("workspace default model lookup failed for %s", ws, exc_info=True)
        return {"provider": "", "model": ""}


@tool("list_models_tool")
def list_models_tool() -> str:
    """List the models a delegate may run on: every enabled model of the hub's
    catalog, the workspace's default model and your own.

    Returns JSON with `models` (each `id` is `provider/model`, the value to pass
    as `model` to delegate_task_tool, with its `context_window`), `workspace_default`
    and `self` (the provider and model this agent is configured with, or empty when
    it follows the workspace default). Pick a cheaper model for mechanical parts
    and a stronger one for hard parts; leave `model` empty to let the delegate use
    its own.
    """
    try:
        from tools.langchain_tools import _active_workspace
        from common.agent_context import current_agent_id
        ws = _active_workspace()
        own: Dict[str, str] = {"provider": "", "model": ""}
        caller = current_agent_id.get()
        if caller:
            from agents.registry import get_agent
            spec = get_agent(caller)
            if spec:
                own = {"provider": str(spec.provider or ""), "model": str(spec.model or "")}
        return _json_ok({
            "models": enabled_models(),
            "workspace_default": _workspace_default_model(ws),
            "self": own,
            "workspace": ws or None,
        })
    except Exception as e:  # noqa: BLE001 - a tool answers with an error envelope, never a traceback
        return _json_err(f"Failed to list models: {e}")


# ── the delegation ───────────────────────────────────────────────────────────

class DelegateTaskInput(BaseModel):
    agent_id: str = Field(..., min_length=1, description="The agent to hand the work to (see list_agents_tool)")
    input: str = Field(
        ..., min_length=1,
        description=(
            "The full, self-contained instruction for the delegate. It does NOT see "
            "your task, your conversation or your instructions: include every fact, "
            "file path, constraint and the shape of the answer you need."
        ),
    )
    model: Optional[str] = Field(
        None,
        description=(
            "A catalog model for this delegation, as `provider/model` from "
            "list_models_tool (a bare model id works when unambiguous). Empty: the "
            "delegate's own model."
        ),
    )
    title: Optional[str] = Field(None, description="A short title for the subtask; the input's first line otherwise")
    wait: bool = Field(True, description="Wait for the delegate to finish and return its output (default). False: return right after the launch and read the result later with get_task_result on the subtask id.")
    timeout_seconds: Optional[int] = Field(
        None, ge=1,
        description="How long to wait at most (default 900 s). When it runs out the run keeps going and the answer says so.",
    )


def _child_env() -> Dict[str, str]:
    env = {DEPTH_ENV: str(current_depth() + 1)}
    parent_run = os.environ.get("AGENT_RUN_ID") or ""
    if parent_run:
        env[PARENT_RUN_ENV] = parent_run
    return env


def _parent_stopped() -> bool:
    """Whether the run this tool executes in was told to stop meanwhile."""
    parent_run = os.environ.get("AGENT_RUN_ID") or ""
    if not parent_run:
        return False
    try:
        from managers.run_manager import get_run_by_id
        rec = get_run_by_id(parent_run) or {}
    except Exception:  # noqa: BLE001 - a store hiccup is not a stop
        return False
    return str(rec.get("status") or "") in ("stop", "stopped")


def _result_of(child_id: Any, run: Dict[str, Any]) -> Tuple[str, str]:
    """``(output, error)`` for a finished child."""
    output = ""
    try:
        from tasks.service import get_task_result
        output = str(get_task_result(child_id) or "")
    except Exception:  # noqa: BLE001 - the run record's own output is the fallback
        log.debug("task result lookup failed for %s", child_id, exc_info=True)
    if not output:
        output = str(run.get("output") or "")
    error = str(run.get("error") or "") if str(run.get("status") or "") != "completed" else ""
    return output.strip(), error


def _answer(child: Any, run: Dict[str, Any], spec: Any, *, requested: Dict[str, str],
            waited: float, finished: bool, timed_out: bool = False, stopped: bool = False) -> str:
    from tools.task_management import _task_to_dict
    from tasks.service import get_task
    status = str(run.get("status") or "")
    output, error = _result_of(child.id, run) if finished else ("", "")
    fresh = get_task(child.id) or child
    payload: Dict[str, Any] = {
        "task_id": str(child.id),
        "run_id": str(run.get("run_id") or ""),
        "agent_id": spec.id,
        "agent_name": spec.name,
        "provider": str(run.get("provider") or requested.get("provider") or ""),
        "model": str(run.get("model") or requested.get("model") or ""),
        "status": status,
        "task_status": str(getattr(fresh.status, "value", fresh.status) or ""),
        "finished": finished,
        "waited_seconds": round(waited, 1),
        "output": output,
        "error": error,
        "task": _task_to_dict(fresh),
    }
    if stopped:
        return _json_err(
            f"Delegation to '{spec.id}' was stopped with your run. Do not retry.",
            code="stopped", extra=payload)
    if timed_out:
        payload["message"] = (
            "The delegate is still running. Continue with other work, or call "
            "get_agent_status_tool and get_task_result with this task_id later."
        )
        return _json_ok(payload)
    if not finished:
        payload["message"] = (
            "Delegate launched. Read its result later with get_task_result on this "
            "task_id (get_agent_status_tool says whether it has finished)."
        )
        return _json_ok(payload)
    if status == "completed":
        payload["message"] = (
            "Delegate finished. Use its output for your own task; delegate again for "
            "another part, or finish the task yourself."
        )
        return _json_ok(payload)
    return _json_err(
        f"Delegate '{spec.id}' ended with status {status}: {error or 'no error recorded'}",
        code="delegate_failed", extra=payload)


@tool("delegate_task_tool", args_schema=DelegateTaskInput)
def delegate_task_tool(agent_id: str, input: str, model: Optional[str] = None,
                       title: Optional[str] = None, wait: bool = True,
                       timeout_seconds: Optional[int] = None) -> str:
    """Hand one part of your current task to another agent, optionally on a
    model you choose, and get its output back.

    Creates a subtask under the task you are working, launches `agent_id` on it
    as a real run (own process, the task's environment and money cap, visible on
    the task page), and by default waits for it to finish. The result is the
    delegate's output, plus the subtask id and run id. Use list_agents_tool to
    choose the agent and list_models_tool to choose `model` (`provider/model`);
    leave `model` empty to keep the delegate's own.

    The delegate sees ONLY `input`. Put everything it needs there: the goal, the
    facts, file paths, constraints, and what shape of answer you want back.

    With `wait` false the call returns as soon as the run starts; read the
    outcome later with get_agent_status_tool and get_task_result on the subtask
    id. A refusal (`ok: false`) is final for this call: do not retry it or hunt
    through other agents. Chains stop at a fixed depth, so a delegate cannot
    delegate without end.
    """
    try:
        from common.agent_context import current_task_id
        from tools.langchain_tools import _active_workspace, _delegation_blocked
        from agents.registry import get_agent
        from common.workspace_context import filter_agents_for_workspace
        from tasks.service import (
            CreatedBy, TaskStatus, add_subtask, assign_agent, get_task, update_task,
        )
        from tools.task_management import _uuid_from_str

        parent_id = current_task_id.get()
        if not parent_id:
            return _json_err(
                "delegate_task_tool only works inside a task run. In a chat, hand the "
                "request over with run_agent_tool instead.",
                code="no_task")
        parent = get_task(_uuid_from_str(parent_id))
        if parent is None:
            return _json_err("The current task no longer exists", code="not_found",
                             extra={"task_id": parent_id})

        spec = get_agent(agent_id)
        if not spec:
            return _json_err("Agent not found", code="not_found", extra={"agent_id": agent_id})
        ws = _active_workspace() or parent.workspace
        if ws and not filter_agents_for_workspace([spec], ws):
            return _json_err(f"Agent '{agent_id}' is not available in workspace '{ws}'",
                             code="forbidden", extra={"agent_id": agent_id})
        blocked = _delegation_blocked(agent_id)
        if blocked:
            return _json_err(blocked, code="forbidden", extra={"agent_id": agent_id})

        depth = current_depth()
        if depth + 1 >= max_depth() + 1:
            return _json_err(
                f"This run is already {depth} delegation(s) deep; the limit is {max_depth()}. "
                "Do the work yourself or report what you could not do.",
                code="too_deep", extra={"depth": depth, "max_depth": max_depth()})

        requested: Dict[str, str] = {}
        if model and str(model).strip():
            try:
                provider_id, model_id = resolve_model(model)
            except ValueError as exc:
                return _json_err(str(exc), code="bad_model",
                                 extra={"models": [m["id"] for m in enabled_models()]})
            requested = {"provider": provider_id, "model": model_id}

        subtask_title = (title or "").strip() or input.strip().splitlines()[0][:80]
        child = add_subtask(parent.id, subtask_title, description=input.strip(),
                            created_by=CreatedBy.orchestrator)
        if child.status == TaskStatus.blocked:
            return _json_err(
                f"The subtask was created blocked ({child.blocked_reason}); unblock the "
                "parent task first.",
                code="blocked", extra={"task_id": str(child.id)})
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

        run_id = preregister_run(str(child.id), agent_id, session_id=session_id)
        try:
            with child_env(_child_env()):
                run_id, _ = start_run(str(child.id), agent_id, params, run_id=run_id)
        except BudgetExceededError as exc:
            update_task(child.id, status=TaskStatus.blocked, blocked_reason=str(exc))
            return _json_err(str(exc), code="budget", extra={"task_id": str(child.id)})
        assign_agent(child.id, agent_id, params, run_id=run_id)
        update_task(child.id, status=TaskStatus.in_progress)
        if session_id:
            try:
                from common.session_service import add_run_to_session
                add_run_to_session(session_id, run_id)
            except Exception:  # noqa: BLE001 - the session link is best-effort, as in start_agent_tool
                log.debug("session link failed for run %s", run_id, exc_info=True)
        record_entity("task", str(child.id), "delegated", subtask_title)

        from managers.run_manager import get_run_by_id, stop_run
        started = time.monotonic()
        run = get_run_by_id(run_id) or {"run_id": run_id, "status": "pending"}
        if not wait:
            return _answer(child, run, spec, requested=requested, waited=0.0, finished=False)

        limit = min(float(timeout_seconds) if timeout_seconds else default_wait_seconds(), MAX_WAIT_SECONDS)
        while True:
            run = get_run_by_id(run_id) or run
            status = str(run.get("status") or "")
            if status not in LIVE_STATUSES:
                return _answer(child, run, spec, requested=requested,
                               waited=time.monotonic() - started, finished=True,
                               stopped=(status == "stopped"))
            if _parent_stopped():
                try:
                    stop_run(str(child.id), run_id)
                except Exception:  # noqa: BLE001 - the watchdog reaps what a failed stop leaves
                    log.debug("could not stop delegated run %s", run_id, exc_info=True)
                run = get_run_by_id(run_id) or run
                return _answer(child, run, spec, requested=requested,
                               waited=time.monotonic() - started, finished=False, stopped=True)
            if time.monotonic() - started >= limit:
                return _answer(child, run, spec, requested=requested,
                               waited=time.monotonic() - started, finished=False, timed_out=True)
            time.sleep(POLL_SECONDS)
    except Exception as e:  # noqa: BLE001 - a tool answers with an error envelope, never a traceback
        log.debug("delegate_task_tool failed", exc_info=True)
        return _json_err(f"Failed to delegate: {e}", code="error")


DELEGATION_TOOLS = [delegate_task_tool, list_models_tool]

__all__ = [
    "DEFAULT_MAX_DEPTH", "DEPTH_ENV", "MAX_DEPTH_ENV", "PARENT_RUN_ENV", "WAIT_ENV",
    "DELEGATION_TOOLS", "current_depth", "delegate_task_tool", "enabled_models",
    "list_models_tool", "max_depth", "resolve_model",
]
