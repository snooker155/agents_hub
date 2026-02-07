"""
AgentRunManager: starts, tracks and stops agent runs as subprocesses.

State is stored in orchestrator/state/agent_runs.json with entries:
- run_id (str, uuid4)
- task_id (str)
- agent_id (str)
- pid (int)
- status (str): running|stop|completed|stopped|failed|error
- started_at (iso str)
- finished_at (iso str | None)
- exit_code (int | None)
- error (str | None)

Public API:
- start_run(task_id: str, agent_id: str, params: dict | None) -> str (run_id)
- stop_run(task_id: str) -> bool
- get_status(task_id: str) -> dict | None

Implementation details:
- Runs are executed as a Python subprocess that invokes this module in a
  special "worker" mode. The worker imports the agent entrypoint from the
  registry and attempts to execute a single step (best-effort). Regardless of
  the agent execution result, the worker updates the shared state with the final
  status, exit code and error.
- Process termination uses SIGTERM/SIGKILL on POSIX and TerminateProcess on
  Windows. We start a new process session so we can terminate the whole group on
  POSIX.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import argparse
import json
import os
import platform
import signal
import subprocess
import sys
import traceback
import time
from uuid import uuid4

from filelock import FileLock

# Local imports
from .registry import get_agent
from .remote_runner import start_remote_run, get_remote_status, stop_remote_run

# -------------------- Paths & constants --------------------
HERE = Path(__file__).resolve().parent
STATE_DIR = HERE.parent / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
RUNS_FILE = STATE_DIR / "agent_runs.json"
RUNS_LOCK = STATE_DIR / "agent_runs.json.lock"

# -------------------- Utilities --------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_runs(timeout: float = 10.0) -> List[Dict[str, Any]]:
    if not RUNS_FILE.exists():
        return []
    with FileLock(str(RUNS_LOCK), timeout=timeout):
        try:
            txt = RUNS_FILE.read_text(encoding="utf-8")
            return json.loads(txt) if txt.strip() else []
        except Exception:
            return []


def _save_runs(runs: List[Dict[str, Any]], timeout: float = 10.0) -> None:
    RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(runs, ensure_ascii=False, indent=2)
    with FileLock(str(RUNS_LOCK), timeout=timeout):
        tmp = RUNS_FILE.with_suffix(RUNS_FILE.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, RUNS_FILE)


def _upsert_run(run: Dict[str, Any]) -> None:
    runs = _load_runs()
    found = False
    for i, r in enumerate(runs):
        if r.get("run_id") == run.get("run_id"):
            runs[i] = {**r, **run}
            found = True
            break
    if not found:
        runs.append(run)
    _save_runs(runs)


def _update_run(run_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    runs = _load_runs()
    for i, r in enumerate(runs):
        if r.get("run_id") == run_id:
            newr = {**r, **updates}
            runs[i] = newr
            _save_runs(runs)
            return newr
    return None


def _find_latest_running_for_task(task_id: str) -> Optional[Dict[str, Any]]:
    runs = _load_runs()
    candidates = [r for r in runs if r.get("task_id") == str(task_id) and r.get("status") in {"running", "stop"}]
    # Pick the most recent by started_at
    def _key(r: Dict[str, Any]) -> str:
        return r.get("started_at") or ""
    return max(candidates, key=_key) if candidates else None


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        # On POSIX, signal 0 checks existence. On Windows, os.kill raises if process doesn't exist.
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:
        return True
    else:
        return True


# -------------------- Public API --------------------

def start_run(task_id: str, agent_id: str, params: Optional[Dict[str, Any]] = None, foreground: bool = False) -> str:
    """Start an agent run and record its state. Returns run_id.

    Checks for agent capacity before starting.
    Supports local subprocess runs and remote HTTP-based runs.
    """
    from ..tasks_service import get_task as svc_get_task
    from .swe_runner import build_run_spec

    spec = get_agent(agent_id)
    if not spec:
        raise ValueError(f"Unknown agent_id: {agent_id}")

    # Capacity check
    runs = _load_runs()
    active_runs = [r for r in runs if r.get("agent_id") == agent_id and r.get("status") == "running"]
    if len(active_runs) >= getattr(spec, "capacity", 1):
        raise RuntimeError(f"Agent '{agent_id}' has reached its capacity ({spec.capacity})")

    task = svc_get_task(task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")

    run_spec = build_run_spec(task, params)
    cwd = run_spec.cwd
    run_id = str(uuid4())

    if getattr(spec, "is_remote", False) and spec.agent_url:
        # Remote execution
        run_id = start_remote_run(
            spec.agent_url,
            str(task_id),
            " ".join(run_spec.cmd),  # simplified for remote
            cwd,
            params
        )
        run_rec: Dict[str, Any] = {
            "run_id": run_id,
            "task_id": str(task_id),
            "agent_id": agent_id,
            "pid": None,
            "status": "running",
            "is_remote": True,
            "agent_url": spec.agent_url,
            "started_at": _utc_now_iso(),
            "finished_at": None,
            "exit_code": None,
            "error": None,
        }
        _upsert_run(run_rec)
        return run_id

    # Local subprocess execution
    args = run_spec.cmd
    env = run_spec.env
    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        # Create a new process group on Windows for better termination control
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        # POSIX: start new session to be able to kill the whole group
        start_new_session = True

    # Prepare log directory
    log_dir = Path(cwd) / ".logs" if cwd else STATE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"agent_run_{run_id}.log"

    if foreground:
        # Run in foreground, wait and stream output
        # Also tee output to log file if possible, or just run normally
        proc = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            check=False,
        )
        status = "completed" if proc.returncode == 0 else "failed"
        run_rec: Dict[str, Any] = {
            "run_id": run_id,
            "task_id": str(task_id),
            "agent_id": agent_id,
            "pid": None,
            "status": status,
            "started_at": _utc_now_iso(),
            "finished_at": _utc_now_iso(),
            "exit_code": proc.returncode,
            "error": None if status == "completed" else f"process exited with code {proc.returncode}",
            "log_file": str(log_file),
        }
        _upsert_run(run_rec)
        if status == "failed":
            # Re-raise to propagate error to CLI user
            raise subprocess.CalledProcessError(proc.returncode, args)
    else:
        # Background execution via worker wrapper to ensure status is updated on completion
        worker_args = [
            sys.executable,
            "-m",
            "orchestrator.agents.run_manager",
            "worker",
            "--run-id",
            run_id,
            "--log-file",
            str(log_file),
            "--cwd",
            str(cwd),
            "--cmd-json",
            json.dumps(args),
        ]

        # We don't pass full env via CLI, instead we let the worker inherit or re-setup.
        # But some env might be needed. For now, let's assume inheritance is enough for background.
        # Actually, let's pass a few critical ones if needed, or just rely on inheritance.

        proc = subprocess.Popen(
            worker_args,
            start_new_session=start_new_session,
            creationflags=creationflags,
            cwd=os.getcwd(), # run worker from project root
        )

        run_rec: Dict[str, Any] = {
            "run_id": run_id,
            "task_id": str(task_id),
            "agent_id": agent_id,
            "pid": proc.pid, # This is the PID of the worker wrapper
            "status": "running",
            "started_at": _utc_now_iso(),
            "finished_at": None,
            "exit_code": None,
            "error": None,
            "log_file": str(log_file),
        }
        _upsert_run(run_rec)
    return run_id


def get_run_by_id(run_id: str) -> Optional[Dict[str, Any]]:
    """Return a run record by run_id if present in state, else None."""
    runs = _load_runs()
    for r in runs:
        if r.get("run_id") == run_id:
            return r
    return None


def stop_run(task_id: str) -> bool:
    """Attempt to terminate the latest running run for a task. Returns True if successful."""
    rec = _find_latest_running_for_task(str(task_id))
    if not rec:
        return False

    if rec.get("is_remote") and rec.get("agent_url"):
        stopped = stop_remote_run(rec["agent_url"], rec["run_id"])
        if stopped:
             _update_run(rec["run_id"], {"status": "stop"})
        return stopped

    pid = int(rec.get("pid") or 0)
    if pid <= 0:
        return False

    sent = False
    if os.name == "nt":
        # Best-effort termination on Windows
        try:
            # Try graceful first
            os.kill(pid, signal.SIGTERM)
            sent = True
        except Exception:
            # Force kill using TerminateProcess via ctypes
            try:
                import ctypes  # type: ignore
                PROCESS_TERMINATE = 0x0001
                handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
                if handle:
                    ctypes.windll.kernel32.TerminateProcess(handle, 1)
                    ctypes.windll.kernel32.CloseHandle(handle)
                    sent = True
            except Exception:
                sent = False
    else:
        try:
            # Try to terminate the whole process group first
            os.killpg(pid, signal.SIGTERM)
            sent = True
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
                sent = True
            except Exception:
                sent = False

    if sent:
        _update_run(rec["run_id"], {"status": "stop"})
    return sent


def get_status(task_id: str) -> Optional[Dict[str, Any]]:
    """Return the latest run status for the given task id (dict) or None if not found.

    If the run is marked running but the process no longer exists, we will mark it as
    failed with exit_code=1.
    """
    rec = _find_latest_running_for_task(str(task_id))
    if rec:
        if rec.get("is_remote") and rec.get("agent_url"):
            remote_stat = get_remote_status(rec["agent_url"], rec["run_id"])
            if remote_stat.get("status") in {"completed", "done", "finished"}:
                rec = _update_run(rec["run_id"], {
                    "status": "completed",
                    "finished_at": _utc_now_iso(),
                    "exit_code": 0
                }) or rec
            elif remote_stat.get("status") in {"failed", "error"}:
                rec = _update_run(rec["run_id"], {
                    "status": "failed",
                    "finished_at": _utc_now_iso(),
                    "exit_code": 1,
                    "error": remote_stat.get("error", "remote error")
                }) or rec
            return rec

        pid = int(rec.get("pid") or 0)
        if pid > 0 and not _pid_exists(pid):
            # Process vanished; finalize
            rec = _update_run(
                rec["run_id"],
                {
                    "status": "failed",
                    "finished_at": _utc_now_iso(),
                    "exit_code": 1,
                    "error": rec.get("error") or "process exited unexpectedly",
                },
            ) or rec
        return rec

    # If not running, return the most recent by finished_at
    runs = _load_runs()
    by_task = [r for r in runs if r.get("task_id") == str(task_id)]
    if not by_task:
        return None
    def _key(r: Dict[str, Any]) -> str:
        return r.get("finished_at") or r.get("started_at") or ""
    return max(by_task, key=_key)


# -------------------- Worker mode --------------------

def _worker_main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["worker"])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--cmd-json", required=True)
    args = parser.parse_args()

    cmd = json.loads(args.cmd_json)
    run_id = args.run_id
    log_file = Path(args.log_file)
    cwd = args.cwd

    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "w", encoding="utf-8") as log_fh:
            log_fh.write(f"--- Worker started at {_utc_now_iso()} ---\n")
            log_fh.write(f"Command: {cmd}\n")
            log_fh.write(f"CWD: {cwd}\n\n")
            log_fh.flush()

            proc = subprocess.run(
                cmd,
                cwd=cwd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                check=False,
            )

            status = "completed" if proc.returncode == 0 else "failed"
            _update_run(run_id, {
                "status": status,
                "finished_at": _utc_now_iso(),
                "exit_code": proc.returncode,
                "error": None if status == "completed" else f"process exited with code {proc.returncode}",
            })
    except Exception as e:
        error_msg = f"Worker error: {e}\n{traceback.format_exc()}"
        try:
            with open(log_file, "a", encoding="utf-8") as log_fh:
                log_fh.write(error_msg)
        except Exception:
            pass
        _update_run(run_id, {
            "status": "failed",
            "finished_at": _utc_now_iso(),
            "error": str(e),
        })

if __name__ == "__main__":
    _worker_main()
