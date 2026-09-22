from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Sequence
from uuid import UUID

from pydantic import BaseModel
from .models import Task, TaskStatus, CreatedBy
from memory.store import MemoryStore  # noqa: F401 — re-exported for backward compatibility
from common.paths import TASKS_FILE as DEFAULT_TASKS_FILE
from common import db

from pydantic_core import to_jsonable_python as _pydantic_encoder

def _model_to_dict(obj: BaseModel) -> dict:
    return obj.model_dump()

def _parse_task(data: dict) -> Task:
    # Remove legacy persisted field; agent_state is now derived at runtime
    data.pop("agent_state", None)
    # Remove fields that have been moved to per-task sidecar storage
    data.pop("activity_log", None)
    data.pop("activity_log_enabled", None)
    data.pop("result", None)
    # Ensure keys exist with defaults
    data.setdefault("assigned_agent_type", None)
    data.setdefault("assigned_agent_params", None)
    data.setdefault("assigned_agent_run_id", None)
    # Backward compatibility: migrate legacy 'project_folder' to 'workspace'
    if "workspace" not in data and "project_folder" in data:
        data["workspace"] = data.get("project_folder")
    # Ensure workspace key exists
    data.setdefault("workspace", None)
    # Ensure project key exists
    data.setdefault("project", None)
    # Ensure project_id key exists
    data.setdefault("project_id", None)
    # Jira-style key and dependency list (added later; default for legacy tasks)
    data.setdefault("key", None)
    if not isinstance(data.get("depends"), list):
        data["depends"] = []

    return Task.model_validate(data)

def _json_default(o):
    try:
        return _pydantic_encoder(o)
    except Exception:
        pass
    # Fallbacks
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, UUID):
        return str(o)
    raise TypeError(f"Object of type {type(o)!r} is not JSON serializable")


def _task_doc(task: Task) -> str:
    return json.dumps(_model_to_dict(task), ensure_ascii=False, default=_json_default)


def _doc_to_task(doc: str) -> Optional[Task]:
    try:
        data = json.loads(doc)
        if isinstance(data, dict):
            return _parse_task(data)
    except Exception:
        pass
    return None


def _write_task_row(conn, task: Task) -> None:
    d = _model_to_dict(task)
    conn.execute(
        "INSERT OR REPLACE INTO tasks (id, key, parent_id, status, workspace, "
        "project_id, created_at, updated_at, doc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (str(task.id), task.key,
         str(task.parent_id) if task.parent_id else None,
         str(d.get("status") or ""), task.workspace,
         str(task.project_id) if task.project_id else None,
         task.created_at.isoformat() if task.created_at else "",
         task.updated_at.isoformat() if task.updated_at else "",
         _task_doc(task)),
    )


class TaskStore:
    """SQLite-backed store for Task objects.

    Every task row keeps the full task document as JSON (``doc``) plus the
    columns used for lookups (id, key, parent_id, status, ...). Reads and
    read-modify-write updates run in single transactions, so concurrent
    writers (backend, agent subprocesses, node workers) can never drop each
    other's changes — the failure mode of the old whole-file JSON store.

    The ``path`` argument is accepted for backward compatibility with the old
    file-based constructor and ignored: all state lives in the shared database
    (see ``common.db``; legacy tasks.json is migrated in on first open).
    """

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else Path(DEFAULT_TASKS_FILE)
        self._backfill_keys()

    def _backfill_keys(self, timeout: float = 10.0) -> None:
        """Assign Jira-style keys to legacy tasks that predate the key field."""
        try:
            from tasks.keys import next_key
            with db.transaction() as conn:
                tasks = self._load_all(conn)
                missing = [t for t in tasks if not getattr(t, "key", None)]
                if not missing:
                    return
                # Oldest first so numbering follows creation order
                missing.sort(key=lambda t: (t.created_at.isoformat() if t.created_at else ""))
                for t in missing:
                    t.key = next_key(
                        tasks,
                        project_id=str(t.project_id) if t.project_id else None,
                        workspace=t.workspace,
                    )
                    _write_task_row(conn, t)
        except Exception:
            # Never break store initialization over key backfill
            pass

    # ------------- internals -------------
    @staticmethod
    def _load_all(conn) -> List[Task]:
        rows = conn.execute("SELECT doc FROM tasks ORDER BY rowid").fetchall()
        out: List[Task] = []
        for r in rows:
            t = _doc_to_task(r["doc"])
            if t is not None:
                out.append(t)
        return out

    # ------------- public API -------------
    def load(self, timeout: float = 10.0) -> List[Task]:
        return self._load_all(db.get_conn())

    def save(self, tasks: Sequence[Task], timeout: float = 10.0) -> None:
        """Replace the entire task list (bulk mutations, e.g. create_sequence)."""
        with db.transaction() as conn:
            conn.execute("DELETE FROM tasks")
            for t in tasks:
                _write_task_row(conn, t)

    def list(self, timeout: float = 10.0) -> List[Task]:
        return self.load(timeout=timeout)

    def get(self, task_id: UUID | str, timeout: float = 10.0) -> Optional[Task]:
        row = db.get_conn().execute(
            "SELECT doc FROM tasks WHERE id = ?", (str(task_id),)).fetchone()
        return _doc_to_task(row["doc"]) if row is not None else None

    def get_many(self, task_ids: Sequence[UUID | str], timeout: float = 10.0
                 ) -> Dict[str, Task]:
        """Fetch several tasks by id in one query, keyed by string id.

        List views enrich a *page* of runs with their task titles; loading every
        task to find twenty of them is what this replaces.
        """
        ids = [str(t) for t in task_ids if t]
        if not ids:
            return {}
        out: Dict[str, Task] = {}
        # Chunked to stay under SQLite's variable limit on a large page.
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            rows = db.get_conn().execute(
                f"SELECT id, doc FROM tasks WHERE id IN ({', '.join('?' * len(chunk))})",
                chunk,
            ).fetchall()
            for r in rows:
                task = _doc_to_task(r["doc"])
                if task is not None:
                    out[str(r["id"])] = task
        return out

    def add(self, task: Task, timeout: float = 10.0) -> Task:
        with db.transaction() as conn:
            _write_task_row(conn, task)
        return task

    def create(
        self,
        title: str,
        description: str = "",
        *,
        created_by: CreatedBy = CreatedBy.user,
        parent_id: Optional[UUID] = None,
        sequence_id: Optional[str] = None,
        order: Optional[int] = None,
        blocked_reason: Optional[str] = None,
        status: TaskStatus = TaskStatus.todo,
        workspace: Optional[str] = None,
        project: Optional[str] = None,
        project_id: Optional[str] = None,
        should_decompose: bool = False,
        external_source: Optional[dict] = None,
        depends: Optional[Sequence[UUID]] = None,
        due_at: Optional[datetime] = None,
        timeout: float = 10.0,
    ) -> Task:
        # Key computation and insert happen in the same transaction so two
        # concurrent creates can never allocate the same key number.
        with db.transaction() as conn:
            tasks = self._load_all(conn)
            try:
                from tasks.keys import next_key
                key = next_key(tasks, project_id=project_id, workspace=workspace)
            except Exception:
                key = None
            task = Task(
                key=key,
                title=title,
                description=description,
                created_by=created_by,
                parent_id=parent_id,
                sequence_id=sequence_id,
                order=order,
                blocked_reason=blocked_reason,
                status=status,
                workspace=workspace,
                project=project,
                project_id=project_id,
                should_decompose=should_decompose,
                external_source=external_source,
                depends=list(depends or []),
                due_at=due_at,
            )
            _write_task_row(conn, task)
        return task

    def update(self, task_id: UUID | str, *, timeout: float = 10.0, **fields) -> Optional[Task]:
        tid_str = str(task_id)
        with db.transaction() as conn:
            row = conn.execute("SELECT doc FROM tasks WHERE id = ?", (tid_str,)).fetchone()
            if row is None:
                return None
            try:
                data = json.loads(row["doc"])
            except Exception:
                return None
            # Merge with JSON-safe values so the stored doc stays serializable.
            safe_fields = json.loads(json.dumps(fields, ensure_ascii=False, default=_json_default))
            data.update(safe_fields)
            data.pop("agent_state", None)
            updated = _parse_task(data)
            updated.touch()
            _write_task_row(conn, updated)
            return updated

    def claim(self, task_id: UUID | str, *, waiting_statuses: Sequence[str], **fields) -> Optional[Task]:
        """Atomically apply ``fields`` only if the task is still unclaimed.

        A task is claimable when it has no ``assigned_agent_type`` and its status
        is one of ``waiting_statuses``. The check and the write happen in one
        transaction, so when several processes race to dispatch the same runnable
        subtask exactly one wins (the others get ``None``). This is what makes
        parallel subtask dispatch safe against double-dispatch.
        """
        tid_str = str(task_id)
        with db.transaction() as conn:
            row = conn.execute("SELECT doc FROM tasks WHERE id = ?", (tid_str,)).fetchone()
            if row is None:
                return None
            try:
                data = json.loads(row["doc"])
            except Exception:
                return None
            if data.get("assigned_agent_type"):
                return None
            if str(data.get("status") or "") not in set(waiting_statuses):
                return None
            safe_fields = json.loads(json.dumps(fields, ensure_ascii=False, default=_json_default))
            data.update(safe_fields)
            data.pop("agent_state", None)
            updated = _parse_task(data)
            updated.touch()
            _write_task_row(conn, updated)
            return updated

    def delete(self, task_id: UUID | str, *, cascade: bool = False, timeout: float = 10.0) -> int:
        """Delete a task by id.

        If cascade=True, also deletes all descendants where parent_id links recursively
        to the given task.
        Returns the number of deleted tasks.
        """
        tid_str = str(task_id)
        with db.transaction() as conn:
            to_delete = {tid_str}
            if cascade:
                # Walk the parent_id column instead of loading full documents.
                rows = conn.execute("SELECT id, parent_id FROM tasks WHERE parent_id IS NOT NULL").fetchall()
                children_by_parent: dict[str, List[str]] = {}
                for r in rows:
                    children_by_parent.setdefault(str(r["parent_id"]), []).append(str(r["id"]))
                stack = [tid_str]
                while stack:
                    parent = stack.pop()
                    for child in children_by_parent.get(parent, []):
                        if child not in to_delete:
                            to_delete.add(child)
                            stack.append(child)
            deleted = 0
            for tid in to_delete:
                cur = conn.execute("DELETE FROM tasks WHERE id = ?", (tid,))
                deleted += cur.rowcount
            return deleted

# ---------------------------------------------------------------------------
# Per-task sidecar helpers — activity log and results.
#
# These moved from per-task JSON files into the task_activity / task_results /
# routing_log tables. The ``tasks_path`` argument is kept in the signatures for
# backward compatibility and ignored.
# ---------------------------------------------------------------------------


def get_activity_log(tasks_path: Path, task_id: str) -> List[dict]:
    """Return activity log entries for a task (empty list if none)."""
    rows = db.get_conn().execute(
        "SELECT entry FROM task_activity WHERE task_id = ? ORDER BY seq",
        (str(task_id),)).fetchall()
    return [e for e in (db.loads(r["entry"]) for r in rows) if isinstance(e, dict)]


def append_activity_log(tasks_path: Path, task_id: str, entry: dict) -> None:
    """Append one entry to a task's activity log."""
    with db.transaction() as conn:
        conn.execute("INSERT INTO task_activity (task_id, entry) VALUES (?, ?)",
                     (str(task_id), db.dumps(entry)))


def delete_activity_log(tasks_path: Path, task_id: str) -> None:
    """Delete the activity log for a task."""
    try:
        with db.transaction() as conn:
            conn.execute("DELETE FROM task_activity WHERE task_id = ?", (str(task_id),))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Global routing log — records every orchestrator assignment decision.
# ---------------------------------------------------------------------------

def get_routing_log(workspace: Optional[str] = None) -> List[dict]:
    """Return all routing log entries, optionally filtered by workspace, newest first."""
    rows = db.get_conn().execute("SELECT entry FROM routing_log ORDER BY seq DESC").fetchall()
    entries = [e for e in (db.loads(r["entry"]) for r in rows) if isinstance(e, dict)]
    if workspace:
        entries = [e for e in entries if e.get("workspace") == workspace]
    return entries


def append_routing_log(entry: dict) -> None:
    """Append one routing event to the global routing log."""
    with db.transaction() as conn:
        conn.execute("INSERT INTO routing_log (entry) VALUES (?)", (db.dumps(entry),))


def get_task_results(tasks_path: Path, task_id: str) -> List[dict]:
    """Return all result entries for a task (newest last)."""
    rows = db.get_conn().execute(
        "SELECT entry FROM task_results WHERE task_id = ? ORDER BY seq",
        (str(task_id),)).fetchall()
    return [e for e in (db.loads(r["entry"]) for r in rows) if isinstance(e, dict)]


def get_task_result(tasks_path: Path, task_id: str) -> Optional[str]:
    """Return the latest result text for a task (None if not set). Backward-compat accessor."""
    entries = get_task_results(tasks_path, task_id)
    return entries[-1].get("result") if entries else None


def get_task_result_files(tasks_path: Path, task_id: str) -> List[str]:
    """Return the files from the latest result entry (empty list if not set)."""
    entries = get_task_results(tasks_path, task_id)
    return (entries[-1].get("files") or []) if entries else []


def set_task_result(
    tasks_path: Path,
    task_id: str,
    result: str,
    files: Optional[List[str]] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> None:
    """Upsert a result entry for a task keyed by run_id.

    If run_id is None or not found, appends a new entry.
    Existing entries from other runs are preserved.
    """
    entry: dict = {
        "task_id": str(task_id),
        "run_id": run_id,
        "agent_id": agent_id,
        "result": result,
        "files": files or [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with db.transaction() as conn:
        if run_id:
            row = conn.execute(
                "SELECT seq FROM task_results WHERE task_id = ? AND run_id = ?",
                (str(task_id), str(run_id))).fetchone()
            if row is not None:
                conn.execute("UPDATE task_results SET entry = ? WHERE seq = ?",
                             (db.dumps(entry), row["seq"]))
                return
        conn.execute(
            "INSERT INTO task_results (task_id, run_id, entry) VALUES (?, ?, ?)",
            (str(task_id), str(run_id) if run_id else None, db.dumps(entry)))


def delete_task_result(tasks_path: Path, task_id: str) -> None:
    """Delete all result entries for a task."""
    try:
        with db.transaction() as conn:
            conn.execute("DELETE FROM task_results WHERE task_id = ?", (str(task_id),))
    except Exception:
        pass
