"""
Flow-run records — one per flow *execution*, not per agent.

A flow is not an agent run, so it has no row in the ``runs`` table. But a flow
execution still needs the same lifecycle bookkeeping an agent run has (status,
pid, timing, task/session linkage, error), and crucially each *execution* needs
its own record: the same flow can run multiple times in parallel, so its
running state and orchestrator pid cannot live on the flow definition.

Records are keyed by ``flow_run_id`` (the id flow/launcher generates and passes
to runtime/flow_run.py as --run-id; the per-node agent runs carry it as flow_run_id).

Storage is the ``flow_runs`` table (see ``common.db``), which replaced a
``flow_runs.json`` file guarded by a FileLock. A flow run is written from at
least three processes at once — the launcher, the orchestrator subprocess and
the backend's stop route — and the whole-file rewrite meant every one of those
writers serialized on the lock and rewrote every other flow's records to change
one field. Existing files are imported on first open, see
``common.db_migrate.migrate_flow_runs``.

A record is a plain dict and the table keeps it whole in ``doc``: callers store
keys of their own on a run (a checkpoint, say) and read them back unchanged.
The columns beside ``doc`` are an indexed mirror of the fields queries filter
on, and every write here refreshes them together with the document. That
handling is shared with loop and team runs: see ``common.entity_runs``.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from common.db_migrate import FLOW_RUN_COLUMNS
from common.entity_runs import EntityRunStore
from common.paths import AGENTS_HUB_ROOT

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

    A per-run file this host does not have locally (its run finished on a
    different worker or backend replica, common/blobs.py) is fetched from the
    blob store instead of silently skipped: the store is listed by the
    ``flow_logs/<flow_id>/`` prefix and any file not already found on disk is
    downloaded before it is read.
    """
    from common import blobs

    events: List[Dict[str, Any]] = []
    run_dir = FLOW_LOGS_DIR / str(flow_id)
    local_names = set()
    if run_dir.is_dir():
        for f in run_dir.glob("*.json"):
            local_names.add(f.name)
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    events.extend(data)
            except Exception:
                continue

    try:
        remote_keys = blobs.list(f"flow_logs/{flow_id}/")
    except Exception:
        remote_keys = []
    for key in remote_keys:
        name = key.rsplit("/", 1)[-1]
        if not name.endswith(".json") or name in local_names:
            continue
        path = blobs.ensure_local(key)
        if path is None:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
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


# -------------------- Run records --------------------
# The record handling (merge, whole-record document, mirrored columns, the
# ``flow_runs.changed`` notice) is the shared implementation in
# common/entity_runs.py; this module only says what a flow run looks like.

_RUNS: EntityRunStore[Dict[str, Any]] = EntityRunStore(
    table="flow_runs",
    key="flow_run_id",
    columns=FLOW_RUN_COLUMNS,
    doc_column="doc",
    resource="flow_runs",
    parent_key="flow_id",
    order_by="COALESCE(started_at, ''), flow_run_id",
    live_statuses=("running", "pending"),
)


# -------------------- Public API --------------------

def load_flow_runs(timeout: float = 10.0) -> List[Dict[str, Any]]:
    """Return all flow-run records, oldest first.

    ``timeout`` is the old file-lock wait and is accepted but unused: SQLite
    does its own waiting (``PRAGMA busy_timeout``). The parameter stays because
    callers pass it positionally.
    """
    return _RUNS.list()


def _notify_flow_runs(flow_id: Optional[str] = None) -> None:
    _RUNS.notify(flow_id=flow_id)


def upsert_flow_run(rec: Dict[str, Any]) -> None:
    """Insert or update a flow-run record by ``flow_run_id``.

    An existing record is merged into, not replaced, so a field this caller did
    not mention survives. The same semantics the JSON store had.
    """
    _RUNS.upsert(rec)


def update_flow_run(flow_run_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Apply partial updates to a flow-run record and return the merged record.

    The merge happens inside one transaction, so two processes checkpointing
    different keys on the same run cannot drop each other's write.
    """
    return _RUNS.update(flow_run_id, updates)


def get_flow_run(flow_run_id: str) -> Optional[Dict[str, Any]]:
    """Return a flow-run record by id, or None."""
    return _RUNS.get(flow_run_id)


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
    for r in _RUNS.active({"flow_id": str(flow_id)}):
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
    """Finalize a flow-run record (completed / failed / stopped).

    Also mirrors the run's log file (best-effort), so a backend replica or
    worker on another host can serve it once the run is done, even though it
    never ran the orchestrator subprocess itself (common/blobs.py).
    """
    rec = update_flow_run(flow_run_id, {
        "status": status,
        "exit_code": exit_code,
        "error": error,
        "finished_at": _utc_now_iso(),
    })
    log_file = rec.get("log_file") if rec else None
    if log_file:
        try:
            from common import blobs
            blobs.mirror(blobs.rel(log_file))
        except Exception:
            pass
