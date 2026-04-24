"""
AgentRunManager: shared run state, lifecycle tracking, and stop coordination.

Manages the agent_runs.json state file which records every run — whether started
by worker_runner (subprocess/Docker/remote) or directly for in-process chat runs.

State is stored in agents/state/agent_runs.json with entries:
- run_id (str, uuid4)
- task_id (str)
- agent_id (str)
- pid (int | None)
- status (str): running|stop|completed|stopped|failed|error
- started_at (iso str)
- finished_at (iso str | None)
- exit_code (int | None)
- error (str | None)

Public API (state):
- load_runs() -> list
- save_runs(runs)
- upsert_run(run)
- update_run(run_id, updates) -> dict | None
- get_run_by_id(run_id) -> dict | None
- utc_now_iso() -> str

Public API (lifecycle):
- get_status(task_id) -> dict | None
- stop_run(task_id) -> bool
- stop_run_by_id(run_id) -> bool
- get_in_progress_runs_for_node(node_id) -> list
- fail_in_progress_runs_for_node(node_id, reason) -> int
- get_node_run_logs_dir(node_id) -> Path
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4
import json
import os
import signal

from filelock import FileLock

from .remote_runner import get_remote_status, stop_remote_run

# -------------------- Paths & constants --------------------
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
STATE_DIR = PROJECT_ROOT / "agents" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
RUNS_FILE = STATE_DIR / "agent_runs.json"
RUNS_LOCK = STATE_DIR / "agent_runs.json.lock"
NODE_RUNS_DIR = STATE_DIR / "node_runs"
CHAT_LOGS_DIR = STATE_DIR / "chat_logs"
CHAT_LOGS_DIR.mkdir(parents=True, exist_ok=True)

# -------------------- Private state helpers --------------------

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
    for i, r in enumerate(runs):
        if r.get("run_id") == run.get("run_id"):
            runs[i] = {**r, **run}
            _save_runs(runs)
            return
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

def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:
        return True
    else:
        return True


# -------------------- Public shared state API --------------------

def load_runs(timeout: float = 10.0) -> List[Dict[str, Any]]:
    """Return all run records from shared state."""
    return _load_runs(timeout)


def save_runs(runs: List[Dict[str, Any]], timeout: float = 10.0) -> None:
    """Overwrite the entire run list. Use only when bulk mutations are needed."""
    _save_runs(runs, timeout)


def upsert_run(run: Dict[str, Any]) -> None:
    """Insert or update a run record by run_id."""
    _upsert_run(run)


def update_run(run_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Apply partial updates to a run record and return the merged record."""
    return _update_run(run_id, updates)


def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return _utc_now_iso()


def new_unique_run_id() -> str:
    """Return a UUID that does not collide with any existing run_id in state."""
    try:
        existing = {str(r.get("run_id")) for r in (_load_runs() or []) if r.get("run_id")}
    except Exception:
        existing = set()
    rid = str(uuid4())
    while rid in existing:
        rid = str(uuid4())
    return rid


def open_run(
    run_id: str,
    agent_id: str,
    *,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    session_type: Optional[str] = None,
    log_file: Optional[str] = None,
    workspace: Optional[str] = None,
    title: Optional[str] = None,
    message_origin: Optional[str] = None,
    pid: Optional[int] = None,
    status: str = "running",
    link_to_session: bool = True,
    **extra: Any,
) -> str:
    """Create a run record and optionally link it to a session.

    Extra keyword arguments are merged directly into the record (e.g.
    execution_mode, provider, model, is_flow).  Returns run_id.
    """
    record: Dict[str, Any] = {
        "run_id": run_id,
        "task_id": task_id,
        "agent_id": agent_id,
        "pid": pid,
        "status": status,
        "session_id": session_id,
        "started_at": _utc_now_iso(),
        "finished_at": None,
        "exit_code": None,
        "error": None,
        **extra,
    }
    if session_type is not None:
        record["session_type"] = session_type
    if log_file is not None:
        record["log_file"] = str(log_file)
    if workspace is not None:
        record["workspace"] = workspace
    if title is not None:
        record["title"] = title
    if message_origin is not None:
        record["message_origin"] = message_origin
    _upsert_run(record)
    if link_to_session and session_id:
        try:
            from common.session_service import add_run_to_session as _link
            _link(session_id, run_id)
        except Exception:
            pass
    if task_id and status == "running":
        try:
            from common import tasks_service as _ts
            from uuid import UUID as _UUID
            _ts.upsert_task_execution_log_entry(
                _UUID(str(task_id)),
                run_id,
                agent_id=agent_id,
                status="running",
                started_at=record["started_at"],
                finished_at=None,
                model=extra.get("model") or "",
            )
        except Exception:
            pass
    return run_id


def close_run(
    run_id: str,
    *,
    status: str,
    exit_code: int,
    error: Optional[str] = None,
    **extra: Any,
) -> Optional[Dict[str, Any]]:
    """Mark a run as finished and return the updated record.

    Extra keyword arguments (e.g. process=...) are merged into the update.
    """
    return _update_run(run_id, {
        "status": status,
        "finished_at": _utc_now_iso(),
        "exit_code": exit_code,
        "error": error,
        **extra,
    })


def finalize_task_from_run(run_id: str, status: str, exit_code: int) -> None:
    """Update the owning task's status after a run completes or fails."""
    try:
        run = get_run_by_id(run_id)
        task_id_str = (run or {}).get("task_id")
        if not task_id_str:
            return
        from common import tasks_service as _ts
        from uuid import UUID as _UUID
        tid = _UUID(str(task_id_str))

        # Update execution log entry with final status and token usage
        try:
            process = (run or {}).get("process") or {}
            token_usage = process.get("token_usage") or {}
            _ts.upsert_task_execution_log_entry(
                tid,
                run_id,
                agent_id=str((run or {}).get("agent_id") or ""),
                status=status,
                finished_at=(run or {}).get("finished_at") or _utc_now_iso(),
                exit_code=exit_code,
                error=str((run or {}).get("error") or "") or None,
                inbound_tokens=int(token_usage.get("inbound_tokens") or 0),
                outbound_tokens=int(token_usage.get("outbound_tokens") or 0),
                total_tokens=int(token_usage.get("total_tokens") or 0),
            )
        except Exception:
            pass

        if status == "completed":
            # Only advance to 'resolved' if the agent didn't already set a
            # terminal status itself (e.g. code_reviewer sets 'reviewed' or 'blocked').
            # Orchestrator and code_reviewer are not workers — they must not set 'resolved'.
            agent_id_for_run = str((run or {}).get("agent_id") or "")
            non_resolving_agents = {"orchestrator", "code_reviewer"}
            agent_set_statuses = {
                _ts.TaskStatus.reviewing,
                _ts.TaskStatus.reviewed,
                _ts.TaskStatus.blocked,
                _ts.TaskStatus.done,
            }
            if agent_id_for_run not in non_resolving_agents:
                current_task = _ts.get_task(tid)
                if current_task and current_task.status not in agent_set_statuses:
                    ws_name = str(getattr(current_task, "workspace", "") or "default")
                    try:
                        from workspace import get_workspace_metadata
                        followup_mode = get_workspace_metadata(ws_name).get("orchestrator", {}).get("followup_mode", "single")
                    except Exception:
                        followup_mode = "single"
                    if followup_mode == "continuous":
                        # Keep in_progress so the UI shows work is ongoing, but clear
                        # the agent assignment (agent_state=none) so the orchestrator's
                        # _followup filter picks it up to decide the next step.
                        _ts.update_task(tid, status=_ts.TaskStatus.in_progress)
                        _ts.clear_agent(tid)
                    else:
                        _ts.update_task(tid, status=_ts.TaskStatus.resolved)
                        _ts.clear_agent(tid)
            elif agent_id_for_run == "orchestrator":
                # Orchestrator is a non-resolving agent: do not auto-set resolved.
                # The orchestrator must call update_task with status=resolved via
                # tool when the work is complete.
                pass
        else:
            error_msg = str((run or {}).get("error") or "").strip()
            reason = error_msg or f"process exited with code {exit_code}"
            _ts.block_task(tid, reason=reason)
            _ts.clear_agent(tid)
            # Surface the error in the activity log so the task page shows it
            try:
                agent_id_str = str((run or {}).get("agent_id") or "")
                _ts.append_task_activity_log(
                    tid,
                    "run_failed",
                    f"Run failed: {reason}",
                    run_id=run_id,
                    agent_id=agent_id_str,
                    exit_code=exit_code,
                )
            except Exception:
                pass

        # Trigger any session continuations waiting for this task
        try:
            from common.session_service import pop_continuations_for_task
            continuations = pop_continuations_for_task(str(task_id_str))
            for cont in continuations:
                try:
                    _trigger_session_continuation(cont, status)
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass


def _trigger_session_continuation(cont: dict, finished_status: str) -> None:
    """Spawn the orchestrator as a background subprocess to continue the session."""
    import subprocess, sys
    from pathlib import Path as _Path

    task_id = cont.get("task_id")
    session_id = cont.get("session_id")
    workspace = cont.get("workspace") or ""
    agent_id = cont.get("agent_id", "orchestrator")
    if not task_id or not session_id:
        return

    project_root = _Path(__file__).resolve().parents[1]
    env = dict(__import__("os").environ)
    env["AGENT_SESSION_ID"] = session_id
    if workspace:
        env["AGENT_WORKSPACE"] = workspace

    # Pre-register the orchestrator run and assign it to the task so it is
    # visible in the UI as the active agent during continuation processing.
    run_id = str(uuid4())
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
    })
    try:
        from common import tasks_service as _ts
        from uuid import UUID as _UUID
        _ts.assign_agent(_UUID(task_id), agent_id, run_id=run_id)
        _ts.update_task(_UUID(task_id), status=_ts.TaskStatus.in_progress)
    except Exception:
        pass

    env["AGENT_LOG_FILE"] = ""  # run_agent.py will create its own log

    # Build a minimal instruction file so the agent gets context.
    # [MONITOR] prefix tells the orchestrator to skip Steps 1-3 and go directly
    # to Step 4 (get_agent_status_tool) so it does not re-assign the same task.
    instruction = (
        f"[MONITOR] Task ID: {task_id}\n\n"
        "A previously started agent has finished. "
        "Skip Steps 1-3. Go directly to Step 4 of your instructions."
    )
    args = [
        sys.executable, str(project_root / "run_agent.py"),
        agent_id,       # positional: agent
        instruction,    # positional: action
        "--task-id", task_id,
        "--run-id", run_id,
    ]
    if workspace:
        args += ["--workspace", workspace]

    log_dir = _Path(__file__).resolve().parent / "state" / "continuation_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"continuation_{task_id[:8]}_{utc_now_iso()[:10]}.log"

    with open(log_file, "w", encoding="utf-8") as lf:
        lf.write(f"[continuation] task={task_id} session={session_id} trigger_status={finished_status}\n\n")
        subprocess.Popen(
            args,
            cwd=str(project_root),
            env=env,
            stdout=lf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def get_node_run_logs_dir(node_id: str) -> Path:
    """Return node-scoped directory for run/chat logs."""
    safe = (str(node_id or "").strip() or "unknown").replace("/", "_")
    path = NODE_RUNS_DIR / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


# -------------------- Public lifecycle API --------------------

def get_run_by_id(run_id: str) -> Optional[Dict[str, Any]]:
    """Return a run record by run_id, or None."""
    for r in _load_runs():
        if r.get("run_id") == run_id:
            return r
    return None


def delete_awaiting_approval_run(task_id: str) -> bool:
    """Delete the awaiting_approval run record for a task. Returns True if one was removed."""
    runs = _load_runs()
    filtered = [
        r for r in runs
        if not (r.get("task_id") == task_id and r.get("status") == "awaiting_approval")
    ]
    if len(filtered) == len(runs):
        return False
    _save_runs(filtered)
    return True


def delete_assigned_run(task_id: str) -> bool:
    """Delete the assigned (node-queued) run record for a task. Returns True if one was removed."""
    runs = _load_runs()
    filtered = [
        r for r in runs
        if not (r.get("task_id") == task_id and r.get("status") == "assigned")
    ]
    if len(filtered) == len(runs):
        return False
    _save_runs(filtered)
    return True


def get_all_runs_for_node(node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Return all runs for a node, newest first."""
    runs = [r for r in _load_runs() if r.get("node_id") == node_id]
    runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return runs[:limit]


def get_in_progress_runs_for_node(node_id: str) -> List[Dict[str, Any]]:
    """Return node-bound runs that are currently in progress."""
    in_progress = {"running", "stop"}
    return [
        r for r in _load_runs()
        if r.get("node_id") == node_id and r.get("status") in in_progress
    ]


def fail_in_progress_runs_for_node(node_id: str, reason: str) -> int:
    """Mark all in-progress runs on a node as failed and return count."""
    runs = get_in_progress_runs_for_node(node_id)
    if not runs:
        return 0

    finished_at = _utc_now_iso()
    failed_count = 0
    for run in runs:
        run_id = run.get("run_id")
        if not run_id:
            continue
        updated = _update_run(run_id, {
            "status": "failed",
            "finished_at": finished_at,
            "exit_code": 1,
            "error": reason,
        })
        if updated:
            failed_count += 1
            try:
                from common import tasks_service
                from uuid import UUID
                task_id = updated.get("task_id")
                if task_id:
                    tid = UUID(str(task_id))
                    task = tasks_service.get_task(tid)
                    if task and getattr(task, "status", None) == "in_progress":
                        tasks_service.block_task(tid, reason=reason)
            except Exception:
                pass

    return failed_count


def stop_run(task_id: str, run_id: Optional[str] = None) -> bool:
    """Attempt to terminate the running run for a task.

    If run_id is provided it is looked up directly (preferred — avoids scanning
    all runs).  Falls back to scanning by task_id when run_id is not known.
    """
    rec = get_run_by_id(run_id)
    if not rec:
        return False
    return _stop_run_record(rec)


def stop_run_by_id(run_id: str) -> bool:
    """Attempt to terminate a specific running run by run_id."""
    rec = get_run_by_id(run_id)
    if not rec or rec.get("status") not in {"running", "stop"}:
        return False
    return _stop_run_record(rec)


def _stop_run_record(rec: Dict[str, Any]) -> bool:
    """Best-effort stop for local, docker, remote, and node-managed runs."""
    task_id = str(rec.get("task_id") or "")
    run_id = str(rec.get("run_id") or "")

    def _mark_task_stopped() -> None:
        if not task_id:
            return
        try:
            from common import tasks_service
            from uuid import UUID
            tasks_service.clear_agent(UUID(task_id))
            tasks_service.stop_task(UUID(task_id))
        except Exception:
            pass

    if rec.get("is_remote") and rec.get("agent_url"):
        stopped = stop_remote_run(rec["agent_url"], rec["run_id"])
        if stopped:
            _update_run(rec["run_id"], {"status": "stop", "finished_at": _utc_now_iso()})
            _mark_task_stopped()
        return stopped

    if rec.get("execution_mode") == "docker" and rec.get("container_name"):
        from .container_manager import stop_container
        sent = stop_container(rec["container_name"])
        if sent:
            _update_run(rec["run_id"], {"status": "stop", "finished_at": _utc_now_iso()})
            _mark_task_stopped()
        return sent

    pid = int(rec.get("pid") or 0)
    if pid <= 0:
        # Node-managed run: mark stop request and let node_runner finalize.
        if rec.get("node_id"):
            _update_run(run_id, {"status": "stop", "finished_at": _utc_now_iso(), "error": "stop requested by user"})
            log_file_path = rec.get("log_file")
            if log_file_path:
                try:
                    with open(log_file_path, "a", encoding="utf-8") as _lf:
                        _lf.write(f"\n[stopped] Run stopped by user at {_utc_now_iso()}\nStatus  : stopped\n")
                except Exception:
                    pass
            _mark_task_stopped()
            return True
        # In-process chat run: mark stop so the streaming handler can finalize.
        if str(rec.get("session_type") or "") == "chat":
            _update_run(run_id, {"status": "stop", "finished_at": _utc_now_iso(), "error": "stop requested by user"})
            _mark_task_stopped()
            return True
        return False

    # If the process has already exited, clean up the stale "running" record.
    if not _pid_exists(pid):
        _update_run(run_id, {
            "status": "stopped",
            "finished_at": _utc_now_iso(),
            "exit_code": 0,
        })
        _mark_task_stopped()
        return True

    sent = False
    if os.name == "nt":
        try:
            os.kill(pid, signal.SIGTERM)
            sent = True
        except Exception:
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
            os.killpg(pid, signal.SIGTERM)
            sent = True
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
                sent = True
            except Exception:
                sent = False

    if sent:
        _update_run(run_id, {"status": "stop", "finished_at": _utc_now_iso()})
        _mark_task_stopped()
    elif not _pid_exists(pid):
        # Process exited between our existence check and the signal attempt.
        _update_run(run_id, {
            "status": "stopped",
            "finished_at": _utc_now_iso(),
            "exit_code": 0,
        })
        _mark_task_stopped()
        return True
    return sent


def get_status(task_id: str, run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return run status for a task, or None.

    If run_id is provided it is looked up directly (preferred — avoids scanning
    all runs).  Falls back to scanning by task_id when run_id is not known.
    Auto-corrects the status if a process/container has exited without updating state.
    """
    rec = get_run_by_id(run_id)
    if rec:
        if rec.get("is_remote") and rec.get("agent_url"):
            remote_stat = get_remote_status(rec["agent_url"], rec["run_id"])
            if remote_stat.get("status") in {"completed", "done", "finished"}:
                rec = _update_run(rec["run_id"], {
                    "status": "completed",
                    "finished_at": _utc_now_iso(),
                    "exit_code": 0,
                }) or rec
            elif remote_stat.get("status") in {"failed", "error"}:
                rec = _update_run(rec["run_id"], {
                    "status": "failed",
                    "finished_at": _utc_now_iso(),
                    "exit_code": 1,
                    "error": remote_stat.get("error", "remote error"),
                }) or rec
            return rec

        if rec.get("execution_mode") == "docker" and rec.get("container_name"):
            from .container_manager import container_running
            if not container_running(rec["container_name"]):
                rec = _update_run(rec["run_id"], {
                    "status": "failed",
                    "finished_at": _utc_now_iso(),
                    "exit_code": 1,
                    "error": rec.get("error") or "container exited unexpectedly",
                }) or rec
        else:
            pid = int(rec.get("pid") or 0)
            if pid > 0 and not _pid_exists(pid):
                was_stopped = rec.get("status") == "stop"
                if was_stopped:
                    log_file_path = rec.get("log_file")
                    if log_file_path:
                        try:
                            with open(log_file_path, "a", encoding="utf-8") as _lf:
                                _lf.write(f"\n[stopped] Run stopped by user at {_utc_now_iso()}\nStatus  : stopped\n")
                        except Exception:
                            pass
                rec = _update_run(rec["run_id"], {
                    "status": "stopped" if was_stopped else "failed",
                    "finished_at": _utc_now_iso(),
                    "exit_code": 0 if was_stopped else 1,
                    "error": None if was_stopped else (rec.get("error") or "process exited unexpectedly"),
                }) or rec
        return rec

    # Not running — return the most recent completed/failed record for this task.
    runs = _load_runs()
    by_task = [r for r in runs if r.get("task_id") == str(task_id)]
    if not by_task:
        return None
    return max(by_task, key=lambda r: r.get("finished_at") or r.get("started_at") or "")
