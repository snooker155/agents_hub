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
``get_task_result``. The subtask and the launch are ``tasks/delegate.py``,
run through the state transport (``common/state_transport.py``): in-process
for a run that is a host subprocess, on the backend (over
``/api/run-state``) for a run in a container, so the delegate is launched by
the host like any other run rather than nested inside the parent's
container, and so it works when the container cannot open the database. The subtask stays under the parent on the task page, so
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
* this run may not have more than ``max_concurrent_delegates`` (default 6,
  the agent's own field or this run's own ``overrides``) delegated subtasks
  running at once; a ``wait=false`` launch counts until its child finishes;
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

#: How many of this run's delegated subtasks may be running at once (the
#: agent's own ``max_concurrent_delegates`` field, or a run's own
#: ``overrides.max_concurrent_delegates``), resolved once at launch and
#: stamped into the run's own environment the same way the delegation depth
#: is (agents/agent_launcher.py, agents/run_overrides.py).
MAX_CONCURRENT_ENV = "AGENTS_HUB_MAX_CONCURRENT_DELEGATES"
DEFAULT_MAX_CONCURRENT_DELEGATES = 6

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


def current_max_concurrent_delegates() -> int:
    """This run's own delegate concurrency limit, as stamped into its
    environment at launch (agents/agent_launcher.py); 6 when unset, e.g. for
    a run started before this existed, or outside a launched run entirely."""
    try:
        return max(1, min(32, int(os.environ.get(MAX_CONCURRENT_ENV, "") or DEFAULT_MAX_CONCURRENT_DELEGATES)))
    except ValueError:
        return DEFAULT_MAX_CONCURRENT_DELEGATES


def running_delegate_count(parent_run_id: str) -> int:
    """How many runs ``delegate_task_tool`` launched from ``parent_run_id``
    are still live (:data:`LIVE_STATUSES`): what the concurrency limit counts
    against. A ``wait=false`` launch counts until the child finishes, same as
    one the caller is still waiting on."""
    if not parent_run_id:
        return 0
    try:
        from common.entity_runs import leaf_children
        from managers.run_manager import get_run_by_id
    except Exception:  # noqa: BLE001 - an import hiccup must not block every delegation
        log.debug("delegate concurrency: could not import run lookups", exc_info=True)
        return 0
    count = 0
    try:
        for child_id in leaf_children(parent_run_id):
            rec = get_run_by_id(child_id) or {}
            if str(rec.get("status") or "") in LIVE_STATUSES:
                count += 1
    except Exception:  # noqa: BLE001 - a store hiccup counts as "no running children" rather than refusing every launch
        log.debug("delegate concurrency count failed for %s", parent_run_id, exc_info=True)
    return count


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


def _transport():
    """Where the subtask is created and the delegate launched
    (``tasks/delegate.py`` runs there). Inside a container that is always the
    backend, over HTTP: the host launches the delegate, so it gets its own
    container or a place on the run queue, instead of a subprocess nested in
    this one (docs/containers.md, "Delegation from a container"). Elsewhere
    it is the run's own state transport, direct by default."""
    from common.hostnet import in_container
    from common.state_transport import HttpStateTransport, get_state_transport
    transport = get_state_transport()
    if in_container() and not isinstance(transport, HttpStateTransport):
        return HttpStateTransport()
    return transport


def _parent_stopped(transport: Any) -> bool:
    """Whether the run this tool executes in was told to stop meanwhile."""
    parent_run = os.environ.get("AGENT_RUN_ID") or ""
    if not parent_run:
        return False
    try:
        rec = transport.get_run(parent_run) or {}
    except Exception:  # noqa: BLE001 - a store hiccup is not a stop
        return False
    return str(rec.get("status") or "") in ("stop", "stopped")


def _answer(task: Dict[str, Any], run: Dict[str, Any], agent: Dict[str, Any], *,
            requested: Dict[str, str], waited: float, finished: bool, output: str = "",
            timed_out: bool = False, stopped: bool = False) -> str:
    status = str(run.get("status") or "")
    agent_id = str(agent.get("id") or "")
    # The task's stored result first, the run record's own output as the fallback.
    text = ((output or "").strip() or str(run.get("output") or "").strip()) if finished else ""
    error = str(run.get("error") or "") if finished and status != "completed" else ""
    payload: Dict[str, Any] = {
        "task_id": str(task.get("id") or ""),
        "run_id": str(run.get("run_id") or ""),
        "agent_id": agent_id,
        "agent_name": str(agent.get("name") or agent_id),
        "provider": str(run.get("provider") or requested.get("provider") or ""),
        "model": str(run.get("model") or requested.get("model") or ""),
        "status": status,
        "task_status": str(task.get("status") or ""),
        "finished": finished,
        "waited_seconds": round(waited, 1),
        "output": text,
        "error": error,
        "task": task,
    }
    if stopped:
        return _json_err(
            f"Delegation to '{agent_id}' was stopped with your run. Do not retry.",
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
        f"Delegate '{agent_id}' ended with status {status}: {error or 'no error recorded'}",
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
        from common.agent_context import current_agent_id, current_task_id
        from common.attribution import launching_user
        from tools.langchain_tools import _active_workspace

        parent_id = current_task_id.get()
        if not parent_id:
            return _json_err(
                "delegate_task_tool only works inside a task run. In a chat, hand the "
                "request over with run_agent_tool instead.",
                code="no_task")

        # What to delegate is decided here; the subtask and the launch happen
        # wherever the transport puts them (tasks/delegate.py has the checks).
        request: Dict[str, Any] = {
            "agent_id": agent_id,
            "input": input,
            "title": title,
            "model": model,
            "workspace": _active_workspace(),
            "caller_agent_id": current_agent_id.get(),
            "depth": current_depth(),
            "env": _child_env(),
            "launched_by": launching_user(),
            # This run's own concurrency limit (see current_max_concurrent_delegates),
            # checked by launch_delegation against this run's already-running children.
            "max_concurrent_delegates": current_max_concurrent_delegates(),
        }
        transport = _transport()
        result = transport.delegate(parent_id, request)
        if result is None:
            return _json_err(
                "The delegation could not be launched: the backend that launches runs is "
                "unreachable from this run (a container whose environment allows no network "
                "cannot delegate). Do the work yourself or report what you could not do.",
                code="unreachable")
        if not result.get("ok"):
            extra = {k: v for k, v in result.items() if k not in ("ok", "error", "code")}
            return _json_err(str(result.get("error") or "The delegation was refused"),
                             code=str(result.get("code") or "bad_request"), extra=extra or None)

        task: Dict[str, Any] = dict(result.get("task") or {})
        run: Dict[str, Any] = dict(result.get("run") or {})
        agent: Dict[str, Any] = dict(result.get("agent") or {"id": agent_id, "name": agent_id})
        requested: Dict[str, str] = dict(result.get("requested") or {})
        task_id = str(task.get("id") or "")
        run_id = str(run.get("run_id") or "")

        started = time.monotonic()
        if not wait:
            return _answer(task, run, agent, requested=requested, waited=0.0, finished=False)

        limit = min(float(timeout_seconds) if timeout_seconds else default_wait_seconds(), MAX_WAIT_SECONDS)
        while True:
            snap = transport.delegation_status(task_id, run_id) or {}
            run = dict(snap.get("run") or run)
            task = dict(snap.get("task") or task)
            status = str(run.get("status") or "")
            if status not in LIVE_STATUSES:
                return _answer(task, run, agent, requested=requested,
                               waited=time.monotonic() - started, finished=True,
                               output=str(snap.get("output") or ""), stopped=(status == "stopped"))
            if _parent_stopped(transport):
                try:
                    transport.stop_run(task_id, run_id)
                except Exception:  # noqa: BLE001 - the watchdog reaps what a failed stop leaves
                    log.debug("could not stop delegated run %s", run_id, exc_info=True)
                snap = transport.delegation_status(task_id, run_id) or {}
                run = dict(snap.get("run") or run)
                task = dict(snap.get("task") or task)
                return _answer(task, run, agent, requested=requested,
                               waited=time.monotonic() - started, finished=False, stopped=True)
            if time.monotonic() - started >= limit:
                return _answer(task, run, agent, requested=requested,
                               waited=time.monotonic() - started, finished=False, timed_out=True)
            time.sleep(POLL_SECONDS)
    except Exception as e:  # noqa: BLE001 - a tool answers with an error envelope, never a traceback
        log.debug("delegate_task_tool failed", exc_info=True)
        return _json_err(f"Failed to delegate: {e}", code="error")


DELEGATION_TOOLS = [delegate_task_tool, list_models_tool]

__all__ = [
    "DEFAULT_MAX_DEPTH", "DEPTH_ENV", "MAX_DEPTH_ENV", "PARENT_RUN_ENV", "WAIT_ENV",
    "MAX_CONCURRENT_ENV", "DEFAULT_MAX_CONCURRENT_DELEGATES",
    "DELEGATION_TOOLS", "current_depth", "current_max_concurrent_delegates",
    "running_delegate_count", "delegate_task_tool", "enabled_models",
    "list_models_tool", "max_depth", "resolve_model",
]
