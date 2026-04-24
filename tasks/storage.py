from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence
from uuid import UUID

import yaml
from filelock import FileLock
from pydantic import BaseModel
from .models import Task, TaskStatus, CreatedBy
from memory.models import SharedMemory
from memory.store import MemoryStore  # noqa: F401 — re-exported for backward compatibility

# Try to import pydantic v1 encoder; provide fallback for v2 or missing
try:  # pydantic v1
    from pydantic.json import pydantic_encoder as _pydantic_encoder  # type: ignore
except Exception:  # pydantic v2 or other
    _pydantic_encoder = None  # type: ignore

def _model_to_dict(obj: BaseModel) -> dict:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj.dict()  # type: ignore[attr-defined]

def _parse_task(data: dict) -> Task:
    # Remove legacy persisted field; agent_state is now derived at runtime
    data.pop("agent_state", None)
    # Remove fields that have been moved to per-task sidecar files
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

    if hasattr(Task, "model_validate"):
        return Task.model_validate(data)  # type: ignore[attr-defined]
    return Task.parse_obj(data)  # type: ignore[attr-defined]

def _json_default(o):
    # Prefer pydantic v1 encoder if available
    if _pydantic_encoder is not None:
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

class TaskStore:
    """File-based store for Task objects with OS-level file locking.
    Supports both JSON and YAML formats based on file extension.
    """

    def __init__(self, path: Path | str | None = None):
        if path is None:
            path = os.environ.get("TASKS_FILE", "tasks.yaml")
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        # Ensure directory exists
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Initialize file if missing
        if not self.path.exists():
            self._atomic_write([])

    def _is_yaml(self) -> bool:
        return self.path.suffix.lower() in (".yaml", ".yml")

    # ------------- public API -------------
    def load(self, timeout: float = 10.0) -> List[Task]:
        with FileLock(str(self.lock_path), timeout=timeout):
            return self._load_unlocked()

    def save(self, tasks: Sequence[Task], timeout: float = 10.0) -> None:
        with FileLock(str(self.lock_path), timeout=timeout):
            payload = [_model_to_dict(t) for t in tasks]
            self._atomic_write(payload)

    def list(self, timeout: float = 10.0) -> List[Task]:
        return self.load(timeout=timeout)

    def get(self, task_id: UUID | str, timeout: float = 10.0) -> Optional[Task]:
        tid_str = str(task_id)
        for t in self.load(timeout=timeout):
            if str(t.id) == tid_str:
                return t
        return None

    def add(self, task: Task, timeout: float = 10.0) -> Task:
        with FileLock(str(self.lock_path), timeout=timeout):
            tasks = self._load_unlocked()
            tasks.append(task)
            self._atomic_write([_model_to_dict(t) for t in tasks])
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
        timeout: float = 10.0,
    ) -> Task:
        task = Task(
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
        )
        return self.add(task, timeout=timeout)

    def update(self, task_id: UUID | str, *, timeout: float = 10.0, **fields) -> Optional[Task]:
        tid_str = str(task_id)
        with FileLock(str(self.lock_path), timeout=timeout):
            tasks = self._load_unlocked()
            updated: Optional[Task] = None
            new_list: List[Task] = []
            for t in tasks:
                if str(t.id) == tid_str:
                    data = _model_to_dict(t)
                    old_status = str(data.get("status", ""))
                    old_agent_type = data.get("assigned_agent_type")
                    data.update(fields)
                    data.pop("agent_state", None)
                    new_status = str(data.get("status", ""))
                    new_agent_type = data.get("assigned_agent_type")
                    updated = _parse_task(data)
                    updated.touch()
                    new_list.append(updated)
                else:
                    new_list.append(t)
            if updated is None:
                return None
            self._atomic_write([_model_to_dict(t) for t in new_list])
            return updated

    def delete(self, task_id: UUID | str, *, cascade: bool = False, timeout: float = 10.0) -> int:
        """Delete a task by id.

        If cascade=True, also deletes all descendants where parent_id links recursively
        to the given task.
        Returns the number of deleted tasks.
        """
        tid_str = str(task_id)
        with FileLock(str(self.lock_path), timeout=timeout):
            tasks = self._load_unlocked()
            to_delete = {tid_str}

            if cascade:
                stack = [tid_str]
                while stack:
                    parent = stack.pop()
                    for t in tasks:
                        if t.parent_id and str(t.parent_id) == parent:
                            child_id = str(t.id)
                            if child_id not in to_delete:
                                to_delete.add(child_id)
                                stack.append(child_id)

            new_list = [t for t in tasks if str(t.id) not in to_delete]
            deleted = len(tasks) - len(new_list)
            if deleted == 0:
                return 0
            self._atomic_write([_model_to_dict(t) for t in new_list])
            return deleted

    # ------------- internals -------------
    def _load_unlocked(self) -> List[Task]:
        try:
            text = self.path.read_text(encoding="utf-8")
            if not text.strip():
                return []
            if self._is_yaml():
                data = yaml.safe_load(text)
            else:
                data = json.loads(text)
            if not isinstance(data, list):
                return []
        except FileNotFoundError:
            return []
        except Exception:
            return []

        items: List[Task] = []
        for obj in data:
            try:
                items.append(_parse_task(obj))
            except Exception:
                continue
        return items

    def _atomic_write(self, payload: Iterable[dict]) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        data_list = list(payload)
        # Never persist legacy runtime-only field.
        for item in data_list:
            if isinstance(item, dict):
                item.pop("agent_state", None)

        if self._is_yaml():
            # Convert to JSON-compatible types for YAML dumper
            clean_data = json.loads(json.dumps(data_list, default=_json_default))
            text = yaml.dump(clean_data, allow_unicode=True, sort_keys=False)
        else:
            text = json.dumps(data_list, ensure_ascii=False, indent=2, default=_json_default)

        tmp_path.write_text(text + "\n", encoding="utf-8")
        tmp_path.replace(self.path)

# ---------------------------------------------------------------------------
# Per-task sidecar file helpers — activity log and result
# ---------------------------------------------------------------------------
# Files live alongside the tasks file:
#   <tasks_dir>/activity_logs/<task_id>.json  — list of activity entries
#   <tasks_dir>/results/<task_id>.json        — {"result": "...", "updated_at": "..."}
# ---------------------------------------------------------------------------

def _sidecar_dir(tasks_path: Path, kind: str) -> Path:
    d = tasks_path.parent / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_activity_log(tasks_path: Path, task_id: str) -> List[dict]:
    """Return activity log entries for a task (empty list if none)."""
    p = _sidecar_dir(tasks_path, "activity_logs") / f"{task_id}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except Exception:
        return []


def append_activity_log(tasks_path: Path, task_id: str, entry: dict) -> None:
    """Append one entry to a task's activity log file."""
    d = _sidecar_dir(tasks_path, "activity_logs")
    p = d / f"{task_id}.json"
    lock_path = d / f"{task_id}.json.lock"
    with FileLock(str(lock_path), timeout=10):
        try:
            entries: List[dict] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        except Exception:
            entries = []
        entries.append(entry)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")
        tmp.replace(p)


def delete_activity_log(tasks_path: Path, task_id: str) -> None:
    """Delete the activity log sidecar file for a task."""
    p = _sidecar_dir(tasks_path, "activity_logs") / f"{task_id}.json"
    try:
        p.unlink(missing_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Global routing log — stored in agents/state/routing_logs/, records every
# orchestrator assignment decision across all tasks.
# ---------------------------------------------------------------------------

def _routing_log_path() -> Path:
    # Locate project root two levels up from this file (tasks/storage.py → project root)
    project_root = Path(__file__).resolve().parents[1]
    d = project_root / "agents" / "state" / "routing_logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "routing_log.json"


def get_routing_log(workspace: Optional[str] = None) -> List[dict]:
    """Return all routing log entries, optionally filtered by workspace."""
    p = _routing_log_path()
    try:
        entries: List[dict] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except Exception:
        entries = []
    if workspace:
        entries = [e for e in entries if e.get("workspace") == workspace]
    return list(reversed(entries))


def append_routing_log(entry: dict) -> None:
    """Append one routing event to the global routing log."""
    p = _routing_log_path()
    lock_path = p.with_suffix(".json.lock")
    with FileLock(str(lock_path), timeout=10):
        try:
            entries: List[dict] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        except Exception:
            entries = []
        entries.append(entry)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")
        tmp.replace(p)


def get_task_execution_log(tasks_path: Path, task_id: str) -> List[dict]:
    """Return execution log entries for a task (empty list if none)."""
    p = _sidecar_dir(tasks_path, "execution_logs") / f"{task_id}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except Exception:
        return []


def upsert_task_execution_log_entry(tasks_path: Path, task_id: str, run_id: str, **fields) -> None:
    """Insert or update an execution log entry keyed by run_id."""
    d = _sidecar_dir(tasks_path, "execution_logs")
    p = d / f"{task_id}.json"
    lock_path = d / f"{task_id}.json.lock"
    with FileLock(str(lock_path), timeout=10):
        try:
            entries: List[dict] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        except Exception:
            entries = []
        for i, e in enumerate(entries):
            if e.get("run_id") == run_id:
                entries[i] = {**e, **fields, "run_id": run_id}
                break
        else:
            entries.append({"run_id": run_id, **fields})
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")
        tmp.replace(p)


def delete_task_execution_log(tasks_path: Path, task_id: str) -> None:
    """Delete the execution log sidecar file for a task."""
    p = _sidecar_dir(tasks_path, "execution_logs") / f"{task_id}.json"
    try:
        p.unlink(missing_ok=True)
    except Exception:
        pass


def _load_result_entries(p: Path) -> List[dict]:
    """Load result entries from sidecar file, handling both old (dict) and new (list) formats."""
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        # Migrate old single-dict format to list
        if isinstance(data, dict) and ("result" in data or "files" in data):
            return [{"run_id": None, "agent_id": None, **data}]
    except Exception:
        pass
    return []


def get_task_results(tasks_path: Path, task_id: str) -> List[dict]:
    """Return all result entries for a task (newest last)."""
    p = _sidecar_dir(tasks_path, "results") / f"{task_id}.json"
    return _load_result_entries(p)


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
    d = _sidecar_dir(tasks_path, "results")
    p = d / f"{task_id}.json"
    lock_path = d / f"{task_id}.json.lock"
    entry: dict = {
        "run_id": run_id,
        "agent_id": agent_id,
        "result": result,
        "files": files or [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with FileLock(str(lock_path), timeout=10):
        entries = _load_result_entries(p)
        if run_id:
            for i, e in enumerate(entries):
                if e.get("run_id") == run_id:
                    entries[i] = entry
                    break
            else:
                entries.append(entry)
        else:
            entries.append(entry)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")
        tmp.replace(p)


def delete_task_result(tasks_path: Path, task_id: str) -> None:
    """Delete the result sidecar file for a task."""
    p = _sidecar_dir(tasks_path, "results") / f"{task_id}.json"
    try:
        p.unlink(missing_ok=True)
    except Exception:
        pass


