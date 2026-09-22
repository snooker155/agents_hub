"""
FlowRunner: launches agent flow runs as subprocesses.

A flow run creates one session for the whole flow. Each node in the DAG
gets its own run record (open_run / close_run), all linked to that session.
An additional "meta" run record tracks the overall flow status and is the
one tied to the task for finalization purposes.

Public API:
- start_flow_run(task_id, flow_id, params) -> (run_id, session_id)
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

from common.paths import AGENTS_HUB_ROOT, PROJECT_ROOT
from managers.run_manager import _utc_now_iso

#: The agent id a ``human_interrupt`` node parks its task under. A flow node is
#: not an agent, so this names the *kind* of pause rather than something the
#: registry can re-run: the answer resumes the flow process, it does not start
#: an agent. The task route keys its flow branch off this.
INTERRUPT_AGENT_ID = "human_interrupt"


def _set_flow_running(flow_id: str, running: bool) -> None:
    try:
        from flow import store as flow_store
        flow_store.set_running(flow_id, running)
    except Exception:
        pass


def start_flow_run(
    task_id: str,
    flow_id: str,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """Launch a flow run subprocess and return (run_id, session_id).

    Creates a shared session for the whole flow, a meta run record to track
    overall status, and spawns runtime/flow_run.py which creates per-node run records.
    """
    from tasks import service as _ts
    from common.session_service import get_or_create_task_session
    from workspace import as_param_dict, resolve_task_workspace
    from flow import store as flow_store

    if not flow_store.get_flow(flow_id):
        raise ValueError(f"Flow not found: {flow_id}")

    task = _ts.get_task(task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")

    # Flow nodes operate in the same directory as standalone runs of an attached
    # task: ws_path is the project subfolder when set, else the workspace root,
    # else cwd. The workspace root holds .logs/ (metadata lives in workspaces.json).
    params = as_param_dict(params)
    ws_name, ws_path = resolve_task_workspace(task, params)

    # One session for the entire flow
    session_id = get_or_create_task_session(
        title=task.title,
        workspace=ws_name,
        is_flow=True,
        task_id=str(task_id),
    )
    try:
        _ts.update_task(task_id, session_id=session_id)
    except Exception:
        pass

    # run_id is the flow-run id (passed to the subprocess as --run-id and used as
    # flow_run_id / run_group for the per-node runs and log events). A flow is not
    # an agent run, so it gets a flow-run record (flow.run_store) — one per
    # execution, so parallel runs of the same flow don't clobber each other —
    # rather than a row in the ``runs`` table.
    from flow import run_store
    run_id = str(uuid4())

    log_dir = AGENTS_HUB_ROOT / "run_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"flow_run_{run_id}.log"

    run_store.open_flow_run(
        run_id, flow_id,
        task_id=str(task_id), session_id=session_id, workspace=ws_name,
        title=task.title, log_file=str(log_file), status="pending",
    )

    env = _build_env(ws_name, session_id, str(log_file))

    args = [
        sys.executable,
        str(PROJECT_ROOT / "runtime" / "flow_run.py"),
        "--flow-id", flow_id,
        "--workspace", str(ws_path),
        "--task-id", str(task_id),
        "--run-id", run_id,
        "--session-id", session_id,
    ]

    desc = params.get("description") or ""
    if desc:
        args += ["--desc", desc]

    # Optional structured seed state (scheduled/webhook triggers), passed as JSON
    # and merged into the flow's initial state by runtime/flow_run.py.
    seed = params.get("seed")
    if isinstance(seed, dict) and seed:
        import json as _json
        args += ["--seed", _json.dumps(seed)]

    pid = _spawn_flow_process(args, log_file, env, flow_id, header="Flow run started")

    # Record the orchestrator pid on the flow-run record so a stop request can
    # terminate this specific instance, and flip the flow's coarse running marker.
    run_store.mark_running(run_id, pid)
    _set_flow_running(flow_id, True)
    return run_id, session_id


def _spawn_flow_process(args, log_file, env, flow_id: str, *, header: str, mode: str = "w") -> int:
    """Start runtime/flow_run.py detached and return its pid.

    Shared by the first launch and by :func:`resume_flow_run`, which appends to
    the same log file so one flow run reads as one story even when it took two
    processes to finish it.
    """
    with open(log_file, mode, encoding="utf-8") as lf:
        lf.write(
            f"--- {header} at {_utc_now_iso()} ---\n"
            f"Flow ID : {flow_id}\n"
            f"Command : {args}\n\n"
        )
        lf.flush()

        creationflags = 0
        start_new_session = False
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            start_new_session = True

        proc = subprocess.Popen(
            args,
            start_new_session=start_new_session,
            creationflags=creationflags,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=lf,
            stderr=subprocess.STDOUT,
        )
    return proc.pid


class FlowResumeError(Exception):
    """A flow run cannot be resumed (unknown run, or no checkpoint to resume from)."""


def resume_flow_run(
    flow_run_id: str,
    answer: Optional[str] = None,
    *,
    auto: bool = False,
) -> Dict[str, Any]:
    """Continue a flow run from its checkpoint, in a fresh subprocess.

    The run keeps its own id, task and session: a resume is the same execution
    carrying on, not a new one, so the history tab, the task and the per-node
    runs all stay where they were. ``runtime/flow_run.py`` is relaunched with
    ``--resume-from``, replays the nodes the checkpoint records as done and runs
    the rest.

    ``answer`` is the person's reply to a ``human_interrupt`` node: it is written
    into the checkpoint's state under the key that node declared, and the node
    is marked done, so the resumed run continues past it with the answer in
    state. ``auto`` marks a resume the watchdog performed rather than a person,
    and is what its ``resume_attempts`` cap counts.
    """
    from tasks import service as _ts
    from workspace import as_param_dict, resolve_task_workspace
    from flow import store as flow_store
    from flow import run_store

    rec = run_store.get_flow_run(flow_run_id)
    if not rec:
        raise FlowResumeError(f"Flow run not found: {flow_run_id}")

    checkpoint = dict(rec.get("checkpoint") or {})
    if not checkpoint:
        raise FlowResumeError(
            f"Flow run {flow_run_id} has no checkpoint to resume from"
        )

    flow_id = str(rec.get("flow_id") or "")
    task_id = str(rec.get("task_id") or "")
    session_id = str(rec.get("session_id") or "")
    if not flow_store.get_flow(flow_id):
        raise FlowResumeError(f"Flow not found: {flow_id}")
    task = _ts.get_task(task_id) if task_id else None
    if task is None:
        raise FlowResumeError(f"Task not found for flow run {flow_run_id}")

    # Fold the answer into the checkpoint: the interrupt node becomes a node
    # that is done, and its answer is in state where successors read it.
    interrupt = dict(checkpoint.get("interrupt") or {})
    if answer is not None and interrupt:
        key = str(interrupt.get("output_key") or "answer")
        node_id = str(interrupt.get("node_id") or "")
        state = dict(checkpoint.get("state") or {})
        state[key] = answer
        checkpoint["state"] = state
        done = [d for d in (checkpoint.get("done") or []) if d.get("node_id") != node_id]
        if node_id:
            done.append({"node_id": node_id, "output": answer, "ok": True})
        checkpoint["done"] = done
    checkpoint.pop("interrupt", None)

    ws_name, ws_path = resolve_task_workspace(task, as_param_dict({"workspace": rec.get("workspace")}))

    log_file = rec.get("log_file")
    if not log_file:
        log_dir = AGENTS_HUB_ROOT / "run_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = str(log_dir / f"flow_run_{flow_run_id}.log")

    attempts = int(rec.get("resume_attempts") or 0) + (1 if auto else 0)
    run_store.update_flow_run(flow_run_id, {
        "checkpoint": checkpoint,
        "status": "running",
        "resume_attempts": attempts,
        "resumed_at": _utc_now_iso(),
        "heartbeat_at": _utc_now_iso(),
        "finished_at": None,
        "exit_code": None,
        "error": None,
    })

    args = [
        sys.executable,
        str(PROJECT_ROOT / "runtime" / "flow_run.py"),
        "--flow-id", flow_id,
        "--workspace", str(ws_path),
        "--task-id", task_id,
        "--run-id", flow_run_id,
        "--session-id", session_id,
        "--resume-from", flow_run_id,
    ]
    env = _build_env(ws_name, session_id, str(log_file))
    pid = _spawn_flow_process(args, log_file, env, flow_id, header="Flow run resumed", mode="a")

    run_store.mark_running(flow_run_id, pid)
    _set_flow_running(flow_id, True)
    try:
        _ts.update_task(task.id, status=_ts.TaskStatus.in_progress, pending_question=None)
    except Exception:
        pass
    return {
        "flow_run_id": flow_run_id, "flow_id": flow_id, "task_id": task_id,
        "session_id": session_id, "pid": pid, "resume_attempts": attempts,
        "resumed_nodes": len(checkpoint.get("done") or []),
    }


class FlowNotFoundError(Exception):
    """The flow id does not exist."""


class FlowNotAuthorizedError(Exception):
    """The flow is not permitted to run in the target workspace."""


class FlowConcurrencyError(Exception):
    """Too many instances of this flow are already running."""

    def __init__(self, flow_id: str, active: int, limit: int):
        self.flow_id = flow_id
        self.active = active
        self.limit = limit
        super().__init__(
            f"Flow '{flow_id}' already has {active} active run(s) "
            f"(max_concurrent={limit}); trigger refused."
        )


def trigger_flow(
    flow_id: str,
    *,
    workspace: Optional[str] = None,
    description: Optional[str] = None,
    seed: Optional[Dict[str, Any]] = None,
    max_concurrent: int = 0,
    created_by: str = "trigger",
) -> Dict[str, Any]:
    """Create a task and start a flow run, guarded by concurrency + authorization.

    Shared by the ``/trigger`` webhook and the scheduled ``flow`` job so both get
    identical semantics. ``max_concurrent`` (0 = unlimited) caps how many
    instances of this flow may run at once. Raises ``FlowNotFoundError`` /
    ``FlowNotAuthorizedError`` / ``FlowConcurrencyError`` on the respective
    failures. Returns ``{task_id, run_id, session_id, workspace}``.
    """
    from tasks import service as _ts
    from flow import store as flow_store
    from flow.run_store import get_active_flow_runs
    from workspace import create_workspace_folder, get_workspace_metadata

    flow = flow_store.get_flow(flow_id)
    if not flow:
        raise FlowNotFoundError(f"Flow not found: {flow_id}")

    ws_name = workspace or flow.get("workspace")
    ws_name = create_workspace_folder(ws_name).name if ws_name else create_workspace_folder().name

    # Workspace flow allowlist (None = unrestricted, mirrors allowed_agents).
    allowed = get_workspace_metadata(ws_name).get("allowed_flows")
    if allowed is not None and flow_id not in allowed:
        raise FlowNotAuthorizedError(
            f"Flow '{flow.get('name') or flow_id}' is not authorized for workspace '{ws_name}'"
        )

    # Concurrency guard: prevents a runaway scheduled/webhook trigger from
    # stacking up unbounded flow subprocesses.
    if max_concurrent and max_concurrent > 0:
        active = len(get_active_flow_runs(flow_id))
        if active >= max_concurrent:
            raise FlowConcurrencyError(flow_id, active, max_concurrent)

    task = _ts.create_task(
        title=f"Flow: {flow.get('name') or flow_id}",
        description=description or flow.get("description", "") or "",
        workspace=ws_name,
    )
    try:
        _ts.append_task_activity_log(
            task.id, "flow_triggered", f"Flow triggered ({created_by})", flow_id=flow_id
        )
    except Exception:
        pass

    params: Dict[str, Any] = {
        "workspace": ws_name,
        "description": description or flow.get("description", "") or "",
        "flow_id": flow_id,
    }
    if isinstance(seed, dict) and seed:
        params["seed"] = seed

    run_id, session_id = start_flow_run(str(task.id), flow_id, params)
    try:
        _ts.assign_agent(task.id, flow.get("name") or flow_id, params, run_id=run_id)
    except Exception:
        pass
    return {
        "task_id": str(task.id),
        "run_id": run_id,
        "session_id": session_id,
        "workspace": ws_name,
    }


def _build_env(ws_name: str, session_id: str, log_file: str) -> Dict[str, str]:
    # base env + run metadata. No flow-wide model override: each node resolves its
    # own model via create_agent's cascade (agent definition → workspace override →
    # workspace settings → global), matching the single-agent path (agent_run.py).
    from common.subprocess_env import base_subprocess_env, add_run_env
    env = base_subprocess_env(ws_name)
    add_run_env(env, session_id=session_id, log_file=log_file)
    return env
