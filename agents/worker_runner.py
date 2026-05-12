"""
TaskRunner: launches agent task runs as subprocesses or Docker containers,
and records each run in the shared agent_runs.json state.

This module owns everything related to *starting* a run. Lifecycle tracking
(status polling, stop, failure propagation) lives in run_manager.py.
Run record creation and completion updates for local subprocess runs are
handled by run_agent.py (which receives --run-id and manages its own lifecycle).

Public API:
- start_run(task_id, agent_id, params, foreground, session_id) -> (run_id, session_id)
- FACTORY_AGENT_IDS: frozenset of agent IDs that represent factory/graph flows
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

from .registry import get_agent
from .run_manager import (
    STATE_DIR,
    _upsert_run,
    _utc_now_iso,
)
from common.config import settings

# -------------------- Model / provider resolution --------------------


def preregister_run(task_id: str, agent_id: str, session_id: Optional[str] = None) -> str:
    """Pre-create a run record before the subprocess is launched.

    Returns the pre-allocated run_id.  Call start_run(..., run_id=<id>) later to
    actually spawn the subprocess using the same run_id.
    """
    run_id = str(uuid4())
    _upsert_run({
        "run_id": run_id,
        "task_id": str(task_id),
        "agent_id": agent_id,
        "status": "awaiting_approval",
        "session_type": "task",
        "session_id": session_id or None,
        "created_at": _utc_now_iso(),
        "started_at": None,
        "finished_at": None,
        "pid": None,
        "exit_code": None,
        "error": None,
    })
    return run_id


# -------------------- Public API --------------------

def start_run(
    task_id: str,
    agent_id: str,
    params: Optional[Dict[str, Any]] = None,
    foreground: bool = False,
    run_id: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """Launch an agent run (subprocess, Docker, or remote) and record its state.

    Creates or reuses a session for the task, then spawns the subprocess.
    Returns (run_id, session_id). For local subprocess runs, the run record is
    created by run_agent.py at startup (it receives --run-id). For remote runs
    the record is created here since run_agent.py is not involved.
    """
    from common.tasks_service import get_task as svc_get_task
    from common import tasks_service as _ts
    from .agent_runner import build_run_spec

    spec = get_agent(agent_id)
    if not spec:
        raise ValueError(f"Unknown agent_id: {agent_id}")

    task = svc_get_task(task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")

    # Ensure a session exists for this task
    session_id = getattr(task, "session_id", None) or None
    if not session_id:
        try:
            from common.session_service import get_or_create_task_session
            session_id = get_or_create_task_session(
                title=task.title,
                workspace=task.workspace,
            )
        except Exception:
            session_id = None
    if session_id and not getattr(task, "session_id", None):
        try:
            _ts.update_task(task_id, session_id=session_id)
        except Exception:
            pass

    run_id = run_id or str(uuid4())

    run_spec = build_run_spec(task, agent_id, params)
    cwd = run_spec.cwd

    # Local / Docker subprocess execution
    args = run_spec.cmd
    worker_env = run_spec.env or {}
    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        start_new_session = True

    log_dir = STATE_DIR / "run_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"agent_run_{run_id}.log"

    # Pre-create a "pending" placeholder so the dashboard sees the run
    # before the subprocess has had time to start and call open_run itself.
    # Include log_file so the message Logs tab can read it even if the
    # subprocess crashes before open_run() is called.
    _upsert_run({
        "run_id": run_id,
        "task_id": str(task_id),
        "agent_id": agent_id,
        "status": "pending",
        "session_type": "task",
        "session_id": session_id,
        "created_at": _utc_now_iso(),
        "started_at": None,
        "finished_at": None,
        "pid": None,
        "exit_code": None,
        "error": None,
        "log_file": str(log_file),
    })

    # Pass run metadata via env so run_agent.py can create its own run record
    worker_env["AGENT_LOG_FILE"] = str(log_file)
    if session_id:
        worker_env["AGENT_SESSION_ID"] = session_id

    # Append --run-id so run_agent.py manages its own lifecycle (creation + completion)
    args += ["--run-id", run_id]

    if foreground:
        proc = subprocess.run(args, cwd=cwd, env=worker_env, check=False)
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, args)
    else:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "w", encoding="utf-8") as lf:
            lf.write(f"--- Run started at {_utc_now_iso()} ---\n")
            lf.write(f"Command: {args}\nCWD: {cwd}\n\n")
            lf.flush()
            subprocess.Popen(
                args,
                start_new_session=start_new_session,
                creationflags=creationflags,
                cwd=cwd,
                env=worker_env,
                stdout=lf,
                stderr=subprocess.STDOUT,
            )

    return run_id, session_id
