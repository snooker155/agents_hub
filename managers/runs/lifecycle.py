"""
A run's life: opening it, closing it, asking what it is doing, and stopping it.

Everything here is about one run record as a *process*, not as a row — which pid
or container or node is carrying it, whether that carrier is still alive, and
how to signal it. Persistence itself belongs to :mod:`managers.runs.store`,
which this module calls; reactions on the owning task belong to
:mod:`managers.runs.task_finalize`, which calls this module.
"""
from __future__ import annotations

import logging
import os
import signal
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from common import db
# PROJECT_ROOT is re-exported by the managers.run_manager facade.
from common.paths import AGENTS_HUB_ROOT, PROJECT_ROOT  # noqa: F401

from .notifications import _notify_task_run_started
from .store import _row_to_record, _update_run, _upsert_run, _utc_now_iso

log = logging.getLogger(__name__)

# Kept for managers.run_manager, which re-exports it by name (a facade over
# the runs/ split, not something this module still uses itself).
HERE = Path(__file__).resolve().parent
RUN_LOGS_DIR = AGENTS_HUB_ROOT / "run_logs"


def _definition_hash_for(agent_id: Optional[str]) -> Optional[str]:
    """Best-effort definition fingerprint for a run record.

    A run must never fail because the registry, a definition file, or the
    hashing itself is unavailable — any failure here just means the run
    record carries no ``definition_hash`` (logged, not raised).
    """
    if not agent_id:
        return None
    try:
        from agents.versions import definition_fingerprint
        return definition_fingerprint(agent_id)["hash"]
    except Exception:
        log.warning("could not compute definition_hash for agent '%s'", agent_id, exc_info=True)
        return None


def run_log_path(run_id: str) -> Path:
    """Canonical log file path for an agent run. Every run channel writes here
    so the UI can treat every run uniformly."""
    RUN_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return RUN_LOGS_DIR / f"agent_run_{run_id}.log"


def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return _utc_now_iso()


def new_unique_run_id() -> str:
    """Return a UUID that does not collide with any existing run_id in state."""
    conn = db.get_conn()
    rid = str(uuid4())
    while conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (rid,)).fetchone():
        rid = str(uuid4())
    return rid


def preopen_run(
    run_id: str,
    agent_id: str,
    *,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    session_type: str = "task",
    status: str = "pending",
    log_file: Optional[str] = None,
    link_to_session: bool = True,
    **extra: Any,
) -> str:
    """Pre-create a placeholder run record before the subprocess is spawned.

    The launcher (parent process) writes this so the dashboard sees the run — and
    its log_file — immediately, before the subprocess starts and calls open_run().
    It deliberately leaves started_at/pid null and writes no task execution-log
    entry: those are open_run()'s job once the run is actually running. Used for
    both pre-approval (status="awaiting_approval") and pre-spawn (status="pending")
    placeholders. Extra kwargs are merged into the record. Returns run_id.
    """
    record: Dict[str, Any] = {
        "run_id": run_id,
        "task_id": str(task_id) if task_id is not None else None,
        "agent_id": agent_id,
        "status": status,
        "session_type": session_type,
        "session_id": session_id or None,
        "created_at": _utc_now_iso(),
        "started_at": None,
        "finished_at": None,
        "pid": None,
        "exit_code": None,
        "error": None,
        **extra,
    }
    if log_file is not None:
        record["log_file"] = str(log_file)
    if "definition_hash" not in record:
        dh = _definition_hash_for(agent_id)
        if dh:
            record["definition_hash"] = dh
    _upsert_run(record)
    if link_to_session and session_id:
        try:
            from common.session_service import add_run_to_session as _link
            _link(session_id, run_id)
        except Exception:
            pass
    return run_id


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
    if "definition_hash" not in record:
        dh = _definition_hash_for(agent_id)
        if dh:
            record["definition_hash"] = dh
    _upsert_run(record)
    if link_to_session and session_id:
        try:
            from common.session_service import add_run_to_session as _link
            _link(session_id, run_id)
        except Exception:
            pass
    _notify_task_run_started(record)
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


def close_run_from_result(
    run_id: str,
    result: Any,
    **extra: Any,
) -> Optional[Dict[str, Any]]:
    """Close a run, deriving status/exit_code/error/output from an agent result.

    ``result`` is any object exposing ``.ok``, ``.agent_output`` and ``.error``
    (the shape returned by ``agents.agent_invoke.invoke_agent``). On success the
    stripped output is recorded; on failure the error string is. Extra kwargs
    (e.g. ``process=...``) are forwarded to ``close_run``. This is the shared
    result→run-record derivation used by the single-agent and HTTP entry points.

    When the result carries a structured response (AgentResult.response), it is
    folded into the process payload so the run's ``response`` block stores both
    the plain text and the structured JSON.
    """
    ok = bool(getattr(result, "ok", False))
    output = (getattr(result, "agent_output", None) or "").strip()
    proc = extra.pop("process", None)
    if isinstance(proc, dict):
        structured = None
        resp_obj = getattr(result, "response", None)
        if resp_obj is not None:
            try:
                structured = resp_obj.to_payload() if hasattr(resp_obj, "to_payload") else None
            except Exception:
                structured = None
        proc = {**proc, "response_text": output if ok else "", "response_obj": structured}
        extra["process"] = proc
    if ok:
        return close_run(
            run_id,
            status="completed",
            exit_code=0,
            output=output,
            **extra,
        )
    return close_run(
        run_id,
        status="failed",
        exit_code=1,
        error=str(getattr(result, "error", None) or "agent error"),
        **extra,
    )


def _carried_here(rec: Dict[str, Any]) -> bool:
    """Whether this process may probe the run's pid or container: the record
    names no host (an older record, or one started before hosts were
    recorded) or names this one. A run launched by a worker on another host
    is judged by its heartbeat instead (see managers/run_watchdog.py)."""
    import socket
    host = str(rec.get("host") or "")
    return not host or host == socket.gethostname()


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


# -------------------- Public lifecycle API --------------------

def get_run_by_id(run_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return a run record by run_id, or None."""
    if not run_id:
        return None
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (str(run_id),)).fetchone()
    return _row_to_record(row) if row is not None else None


def get_all_runs_for_node(node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Return all runs for a node, newest first."""
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM runs WHERE node_id = ?", (str(node_id),)).fetchall()
    runs = [_row_to_record(r) for r in rows]
    runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return runs[:limit]


def get_in_progress_runs_for_node(node_id: str) -> List[Dict[str, Any]]:
    """Return node-bound runs that are currently in progress."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM runs WHERE node_id = ? AND status IN ('running', 'stop')",
        (str(node_id),)).fetchall()
    return [_row_to_record(r) for r in rows]


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
                from tasks import service as tasks_service
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


def _find_active_run_for_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Newest in-progress run for a task — the stop_run fallback when the
    caller knows only the task id."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM runs WHERE task_id = ? AND status IN ('running', 'stop')",
        (str(task_id),)).fetchall()
    runs = [_row_to_record(r) for r in rows]
    if not runs:
        return None
    return max(runs, key=lambda r: r.get("started_at") or r.get("created_at") or "")


def stop_run(task_id: str, run_id: Optional[str] = None) -> bool:
    """Attempt to terminate the running run for a task.

    If run_id is provided it is looked up directly (preferred).  Falls back to
    the task's newest in-progress run when run_id is not known.
    """
    rec = get_run_by_id(run_id) if run_id else None
    if not rec and task_id:
        rec = _find_active_run_for_task(str(task_id))
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
    """Best-effort stop for local, docker, and node-managed runs."""
    task_id = str(rec.get("task_id") or "")
    run_id = str(rec.get("run_id") or "")

    def _mark_task_stopped() -> None:
        if not task_id:
            return
        try:
            from tasks import service as tasks_service
            from uuid import UUID
            tasks_service.clear_agent(UUID(task_id))
            tasks_service.stop_task(UUID(task_id))
        except Exception:
            pass
        # Stopping the run stops the task: cancel its pending continuations too,
        # or a follow-up orchestrator would either fire on the stopped task or
        # (if bound to this run) sit in the queue forever.
        try:
            from common.session_service import drop_continuations_for_task
            dropped = drop_continuations_for_task(task_id)
            if dropped:
                from tasks import service as tasks_service
                from uuid import UUID
                tasks_service.append_task_activity_log(
                    UUID(task_id),
                    "continuation_cancelled",
                    f"Run stopped — cancelled {dropped} pending continuation(s)",
                    run_id=run_id,
                )
        except Exception:
            pass

    # A run executing *inside this process* — a team turn, a playground
    # decision — carries the server's own pid, because that is whose thread it
    # is running on. Signalling that pid would take the backend down with it,
    # so these are stopped the way an in-process chat turn is: mark the record
    # and let the run's stop callback abort at the next model or tool boundary.
    if rec.get("in_process"):
        _update_run(run_id, {"status": "stop", "finished_at": _utc_now_iso(),
                             "error": "stop requested by user"})
        _mark_task_stopped()
        return True

    # container_name, not execution_mode, is what identifies a container-hosted
    # run here: the container's own agent_run.py calls open_run() on startup,
    # which unconditionally records AGENT_EXECUTION_MODE from its own env —
    # forced to "local" inside every container so an agent never tries to
    # nest containers of its own — so execution_mode flips back to "local"
    # moments after this record was created. container_name is never set for
    # anything but a container-hosted run, so it alone is the stable signal.
    if rec.get("container_name"):
        from ..container_manager import stop_container
        sent = stop_container(rec["container_name"])
        if sent:
            _update_run(rec["run_id"], {"status": "stop", "finished_at": _utc_now_iso()})
            _mark_task_stopped()
        return sent

    pid = int(rec.get("pid") or 0)
    if pid > 0 and not _carried_here(rec):
        # The process lives on another host: no signal can reach it from
        # here. The run's heartbeat reads the status back every few seconds
        # (runtime/agent_run.py) and sends itself the signal.
        _update_run(run_id, {"status": "stop", "finished_at": _utc_now_iso(),
                             "error": "stop requested by user"})
        _mark_task_stopped()
        return True

    if pid <= 0:
        # Node-managed run: mark stop request and let node_run finalize.
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
            # Delegated workers (run_agent_tool) execute in-process under this
            # chat turn with their own run records; mark them too so their
            # RunStopCallback aborts at the next LLM/tool boundary.
            sess = str(rec.get("session_id") or "")
            if sess:
                conn = db.get_conn()
                rows = conn.execute(
                    "SELECT * FROM runs WHERE session_id = ? AND status = 'running' "
                    "AND message_origin = 'delegation'", (sess,)).fetchall()
                for r in rows:
                    _update_run(str(r["run_id"]), {"status": "stop", "error": "stop requested by user"})
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
        # See the comment in _stop_run_record: container_name, not
        # execution_mode, is the reliable signal for a container-hosted run.
        if not _carried_here(rec):
            # Another host's process: only its heartbeat says anything, and
            # the watchdog is the one that acts on a stale one.
            pass
        elif rec.get("container_name"):
            from ..container_manager import container_running
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
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM runs WHERE task_id = ?", (str(task_id),)).fetchall()
    by_task = [_row_to_record(r) for r in rows]
    if not by_task:
        return None
    return max(by_task, key=lambda r: r.get("finished_at") or r.get("started_at") or "")
