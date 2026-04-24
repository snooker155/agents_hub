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

from .run_manager import STATE_DIR, _upsert_run, _update_run, _utc_now_iso
from common.paths import TASKS_FILE as DEFAULT_TASKS_FILE

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_FLOWS_FILE = PROJECT_ROOT / "agents" / "state" / "flows.json"


def _set_flow_running(flow_id: str, running: bool) -> None:
    try:
        flows = json.loads(_FLOWS_FILE.read_text(encoding="utf-8"))
        for f in flows:
            if f["id"] == flow_id:
                f["running"] = running
                break
        _FLOWS_FILE.write_text(json.dumps(flows, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def start_flow_run(
    task_id: str,
    flow_id: str,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """Launch a flow run subprocess and return (run_id, session_id).

    Creates a shared session for the whole flow, a meta run record to track
    overall status, and spawns run_flow.py which creates per-node run records.
    """
    from common.tasks_service import get_task as svc_get_task
    from common import tasks_service as _ts
    from common.session_service import get_or_create_task_session, add_run_to_session
    from workspace import WORKSPACES_ROOT

    params = params or {}

    task = svc_get_task(task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")

    ws_name = params.get("workspace") or getattr(task, "workspace", None) or ""
    ws_path = (WORKSPACES_ROOT / ws_name).resolve() if ws_name else Path.cwd()

    # One session for the entire flow
    session_id = get_or_create_task_session(
        title=task.title,
        workspace=ws_name,
        is_flow=True,
    )
    try:
        _ts.update_task(task_id, session_id=session_id)
    except Exception:
        pass

    run_id = str(uuid4())

    log_dir = STATE_DIR / "run_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"flow_run_{run_id}.log"

    # Pre-create the meta run record so the dashboard sees the flow immediately
    _upsert_run({
        "run_id": run_id,
        "task_id": str(task_id),
        "agent_id": "flow-custom-graph",
        "flow_id": flow_id,
        "status": "pending",
        "session_type": "task",
        "session_id": session_id,
        "is_flow": True,
        "created_at": _utc_now_iso(),
        "started_at": None,
        "finished_at": None,
        "pid": None,
        "exit_code": None,
        "error": None,
        "log_file": str(log_file),
    })
    add_run_to_session(session_id, run_id)

    env = _build_env(ws_path, ws_name, session_id, str(log_file))

    desc = params.get("description") or ""
    args = [
        sys.executable,
        str(PROJECT_ROOT / "run_flow.py"),
        "--flow-id", flow_id,
        "--workspace", str(ws_path),
        "--task-id", str(task_id),
        "--run-id", run_id,
        "--session-id", session_id,
    ]
    if desc:
        args += ["--desc", desc]

    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        start_new_session = True

    with open(log_file, "w", encoding="utf-8") as lf:
        lf.write(
            f"--- Flow run started at {_utc_now_iso()} ---\n"
            f"Flow ID : {flow_id}\n"
            f"Command : {args}\n\n"
        )
        lf.flush()
        proc = subprocess.Popen(
            args,
            start_new_session=start_new_session,
            creationflags=creationflags,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=lf,
            stderr=subprocess.STDOUT,
        )

    _update_run(run_id, {"pid": proc.pid, "status": "running", "started_at": _utc_now_iso()})
    _set_flow_running(flow_id, True)
    return run_id, session_id


def _build_env(ws_path: Path, ws_name: str, session_id: str, log_file: str) -> Dict[str, str]:
    env = os.environ.copy()
    env["WORKSPACE_ROOT"] = str(ws_path)
    env["AGENT_WORKSPACE"] = ws_name
    env["AGENT_SESSION_ID"] = session_id
    env["AGENT_LOG_FILE"] = log_file

    # Propagate tasks file path so node runs update the same store
    from common.config import settings
    tasks_file = str(settings.tasks_file or DEFAULT_TASKS_FILE)
    if not Path(tasks_file).is_absolute():
        tasks_file = str((PROJECT_ROOT / tasks_file).resolve())
    env["TASKS_FILE"] = tasks_file

    if settings.openai_api_key:
        env["OPENAI_API_KEY"] = settings.openai_api_key

    # Inject workspace / global model settings
    try:
        from workspace import get_workspace_metadata, get_workspace_default_model_config
        ws_meta = get_workspace_metadata(ws_name)
        if isinstance(ws_meta, dict):
            override = ws_meta.get("model_override") or {}
            ws_default = get_workspace_default_model_config(ws_meta)
            op = (override.get("provider") or "").strip()
            if op and op not in ("global", "workspace_default"):
                env["AGENT_PROVIDER"] = op
                if override.get("model"):
                    env["AGENT_MODEL"] = override["model"]
            elif op != "global":
                dp = (ws_default.get("provider") or "").strip()
                if dp and dp != "global":
                    env["AGENT_PROVIDER"] = dp
                    if ws_default.get("model"):
                        env["AGENT_MODEL"] = ws_default["model"]
    except Exception:
        pass

    return env
