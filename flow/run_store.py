"""
Flow-run records — one per flow *execution*, not per agent.

A flow is not an agent run, so it has no row in the ``runs`` table. But a flow
execution still needs the same lifecycle bookkeeping an agent run has (status,
pid, timing, task/session linkage, error), and crucially each *execution* needs
its own record: the same flow can run multiple times in parallel, so its
running state and orchestrator pid cannot live on the flow definition.

Records are keyed by ``flow_run_id`` (the id flow/launcher generates and passes
to runtime/flow_run.py as --run-id; the per-node agent runs carry it as flow_run_id).
The file format and locking mirror managers.run_manager's agent-run store.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from filelock import FileLock

from common.paths import AGENTS_HUB_ROOT

FLOW_RUNS_FILE = AGENTS_HUB_ROOT / "flow_runs.json"
FLOW_RUNS_LOCK = AGENTS_HUB_ROOT / "flow_runs.json.lock"

FLOW_LOGS_DIR = AGENTS_HUB_ROOT / "flow_logs"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_alive(pid: Optional[int]) -> bool:
    """True if a process with this pid currently exists. Signal 0 probes
    liveness without affecting the process; ESRCH (no such process) means dead,
    EPERM (exists but not ours) means alive."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    except Exception:
        return True
    return True


# -------------------- Flow interaction logs --------------------
# Each flow *run* writes its events to flow_logs/<flow_id>/<run_group>.json — one
# file per execution, where run_group is the flow_run_id (task runs) or the
# conversation id (chat). Readers merge every per-run file for a flow (plus the
# legacy flat flow_logs/<flow_id>.json some old runs wrote) into one timeline.

def flow_log_path(flow_id: str, run_group: str) -> Path:
    """Path to a single flow run's log file: flow_logs/<flow_id>/<run_group>.json."""
    return FLOW_LOGS_DIR / str(flow_id) / f"{run_group}.json"


def append_flow_log(flow_id: str, run_group: str, payload: Dict[str, Any]) -> None:
    """Append one event to a flow run's per-run log file. Best-effort."""
    path = flow_log_path(flow_id, run_group)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except Exception:
            existing = []
        existing.append(payload)
        path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def log_flow_event(
    flow_id: str,
    run_group: str,
    payload: Dict[str, Any],
    *,
    kind: str,
) -> None:
    """Tag a flow-log event with its ``run_group`` + ``kind`` and append it.

    Every event is written to ``flow_logs/<flow_id>/<run_group>.json`` and tagged
    so the dashboard History tab groups events by run. ``kind`` distinguishes the
    two execution surfaces — ``"task"`` (runtime/flow_run.py subprocess, run_group=flow
    run id) vs ``"chat"`` (in-process flow chat, run_group=conversation id). The
    single helper keeps both surfaces' tagging identical.
    """
    payload.setdefault("run_group", run_group)
    payload.setdefault("kind", kind)
    append_flow_log(flow_id, run_group, payload)
    _notify_flow_runs(flow_id)


def read_flow_logs(flow_id: str) -> List[Dict[str, Any]]:
    """Return all log events for a flow, merged across its per-run files.

    Reads every flow_logs/<flow_id>/*.json plus the legacy flat
    flow_logs/<flow_id>.json (older runs), then sorts by timestamp so the merged
    stream stays chronological. Per-run separation is preserved by each event's
    run_group tag, which the dashboard groups on.
    """
    events: List[Dict[str, Any]] = []
    run_dir = FLOW_LOGS_DIR / str(flow_id)
    if run_dir.is_dir():
        for f in run_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    events.extend(data)
            except Exception:
                continue
    legacy = FLOW_LOGS_DIR / f"{flow_id}.json"
    if legacy.exists():
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
            if isinstance(data, list):
                events.extend(data)
        except Exception:
            pass
    events.sort(key=lambda e: e.get("timestamp") or "")
    return events


def _load(timeout: float = 10.0) -> List[Dict[str, Any]]:
    if not FLOW_RUNS_FILE.exists():
        return []
    with FileLock(str(FLOW_RUNS_LOCK), timeout=timeout):
        try:
            txt = FLOW_RUNS_FILE.read_text(encoding="utf-8")
            return json.loads(txt) if txt.strip() else []
        except Exception:
            return []


def _save(runs: List[Dict[str, Any]], timeout: float = 10.0) -> None:
    FLOW_RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(runs, ensure_ascii=False, indent=2)
    with FileLock(str(FLOW_RUNS_LOCK), timeout=timeout):
        tmp = FLOW_RUNS_FILE.with_suffix(FLOW_RUNS_FILE.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, FLOW_RUNS_FILE)


# -------------------- Public API --------------------

def load_flow_runs(timeout: float = 10.0) -> List[Dict[str, Any]]:
    """Return all flow-run records."""
    return _load(timeout)


def _notify_flow_runs(flow_id: Optional[str] = None) -> None:
    try:
        from common.session_broker import notify_change
        notify_change("flow_runs", flow_id=flow_id)
    except Exception:
        pass


def upsert_flow_run(rec: Dict[str, Any]) -> None:
    """Insert or update a flow-run record by ``flow_run_id``."""
    runs = _load()
    for i, r in enumerate(runs):
        if r.get("flow_run_id") == rec.get("flow_run_id"):
            runs[i] = {**r, **rec}
            _save(runs)
            _notify_flow_runs(rec.get("flow_id") or r.get("flow_id"))
            return
    runs.append(rec)
    _save(runs)
    _notify_flow_runs(rec.get("flow_id"))


def update_flow_run(flow_run_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Apply partial updates to a flow-run record and return the merged record."""
    runs = _load()
    for i, r in enumerate(runs):
        if r.get("flow_run_id") == flow_run_id:
            newr = {**r, **updates}
            runs[i] = newr
            _save(runs)
            try:
                from common.session_broker import notify_change
                notify_change("flow_runs", flow_run_id=flow_run_id, flow_id=newr.get("flow_id"))
            except Exception:
                pass
            return newr
    return None


def get_flow_run(flow_run_id: str) -> Optional[Dict[str, Any]]:
    """Return a flow-run record by id, or None."""
    for r in _load():
        if r.get("flow_run_id") == flow_run_id:
            return r
    return None


def get_active_flow_runs(flow_id: str) -> List[Dict[str, Any]]:
    """Return the genuinely not-yet-finished runs for a flow (running or pending).

    Multiple instances of the same flow may be active at once, so this returns
    a list — callers that stop a flow must handle all of them.

    Reconciles crashed runs: a record marked running/pending whose process pid is
    no longer alive (the subprocess was killed, OOM'd, or the host rebooted before
    it could log flow_finish/flow_stopped) is auto-closed as ``stopped`` here and
    excluded. Without this, a dead run keeps a flow flagged active forever — and
    the editor, which derives the active node from the log stream, leaves the last
    started node lit indefinitely. The reconcile emits a flow_stopped log event so
    that node clears too. Pending runs have no pid yet, so they are never reaped.
    """
    active: List[Dict[str, Any]] = []
    reaped = False
    for r in _load():
        if r.get("flow_id") != flow_id or r.get("status") not in {"running", "pending"}:
            continue
        # Only reap once a pid has been recorded (status == running); a pending run
        # hasn't been handed a pid yet and is legitimately not-yet-started.
        pid = r.get("pid")
        if r.get("status") == "running" and pid is not None and not _pid_alive(pid):
            frid = r.get("flow_run_id")
            close_flow_run(frid, status="stopped", error="process no longer alive (reconciled)")
            try:
                log_flow_event(flow_id, frid, {
                    "timestamp": _utc_now_iso(),
                    "type": "flow_stopped",
                    "message": "run record reconciled — process no longer alive",
                }, kind="task")
            except Exception:
                pass
            reaped = True
            continue
        active.append(r)
    # If reaping left no live instance, clear the flow's coarse "running" marker so
    # the flow list stops showing it as active (the list reads flow.running, not
    # these records). Mirrors the stop route's cleanup.
    if reaped and not active:
        try:
            from flow import store as _flow_store
            _flow_store.set_running(flow_id, False)
        except Exception:
            pass
    return active


def open_flow_run(
    flow_run_id: str,
    flow_id: str,
    *,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    workspace: Optional[str] = None,
    title: Optional[str] = None,
    log_file: Optional[str] = None,
    pid: Optional[int] = None,
    status: str = "pending",
) -> Dict[str, Any]:
    """Create (or reset) a flow-run record at launch and return it."""
    rec: Dict[str, Any] = {
        "flow_run_id": flow_run_id,
        "flow_id": flow_id,
        "task_id": task_id,
        "session_id": session_id,
        "workspace": workspace,
        "title": title,
        "log_file": str(log_file) if log_file else None,
        "pid": pid,
        "status": status,
        "created_at": _utc_now_iso(),
        "started_at": None,
        "finished_at": None,
        "exit_code": None,
        "error": None,
    }
    upsert_flow_run(rec)
    return rec


def mark_running(flow_run_id: str, pid: int) -> None:
    """Transition a flow run to running and record the orchestrator pid."""
    update_flow_run(flow_run_id, {
        "status": "running",
        "pid": pid,
        "started_at": _utc_now_iso(),
    })


def close_flow_run(
    flow_run_id: str,
    *,
    status: str,
    exit_code: int = 0,
    error: Optional[str] = None,
) -> None:
    """Finalize a flow-run record (completed / failed / stopped)."""
    update_flow_run(flow_run_id, {
        "status": status,
        "exit_code": exit_code,
        "error": error,
        "finished_at": _utc_now_iso(),
    })
