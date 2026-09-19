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

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

from common.paths import AGENTS_HUB_ROOT
from managers.run_manager import _utc_now_iso

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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

    with open(log_file, "w", encoding="utf-8") as lf:
        lf.write(
            f"--- Flow run started at {_utc_now_iso()} ---\n"
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

    # Record the orchestrator pid on the flow-run record so a stop request can
    # terminate this specific instance, and flip the flow's coarse running marker.
    run_store.mark_running(run_id, proc.pid)
    _set_flow_running(flow_id, True)
    return run_id, session_id


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
