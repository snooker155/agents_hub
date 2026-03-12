from __future__ import annotations

import json
import os
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence
from uuid import UUID

import yaml
from filelock import FileLock
from pydantic import BaseModel
from .models import Task, TaskStatus, AgentState, CreatedBy, SharedMemory

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
    # Autoupgrade old records without agent fields
    if "agent_state" not in data:
        if any(
            k in data and data.get(k) is not None
            for k in ("assigned_agent_type", "assigned_agent_params", "assigned_agent_run_id")
        ):
            data["agent_state"] = AgentState.assigned.value
        else:
            data["agent_state"] = AgentState.none.value
    # Ensure keys exist with defaults
    data.setdefault("assigned_agent_type", None)
    data.setdefault("assigned_agent_params", None)
    data.setdefault("assigned_agent_run_id", None)
    # Backward compatibility: migrate legacy 'project_folder' to 'workspace'
    if "workspace" not in data and "project_folder" in data:
        data["workspace"] = data.get("project_folder")
    # Ensure workspace key exists
    data.setdefault("workspace", None)

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
                    data.update(fields)
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

        if self._is_yaml():
            # Convert to JSON-compatible types for YAML dumper
            clean_data = json.loads(json.dumps(data_list, default=_json_default))
            text = yaml.dump(clean_data, allow_unicode=True, sort_keys=False)
        else:
            text = json.dumps(data_list, ensure_ascii=False, indent=2, default=_json_default)

        tmp_path.write_text(text + "\n", encoding="utf-8")
        tmp_path.replace(self.path)

class MemoryStore:
    """File-based store for SharedMemory objects."""

    def __init__(self, path: Path | str | None = None):
        if path is None:
            path = "shared_memory.json"
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write([])

    def load(self, timeout: float = 10.0) -> List[SharedMemory]:
        with FileLock(str(self.lock_path), timeout=timeout):
            return self._load_unlocked()

    def _load_unlocked(self) -> List[SharedMemory]:
        try:
            text = self.path.read_text(encoding="utf-8")
            if not text.strip(): return []
            data = json.loads(text)
            return [SharedMemory(**obj) for obj in data]
        except Exception:
            return []

    def save(self, memories: Sequence[SharedMemory], timeout: float = 10.0) -> None:
        with FileLock(str(self.lock_path), timeout=timeout):
            payload = [_model_to_dict(m) for m in memories]
            self._atomic_write(payload)

    def get(self, memory_id: UUID | str, timeout: float = 10.0) -> Optional[SharedMemory]:
        mid_str = str(memory_id)
        for m in self.load(timeout=timeout):
            if str(m.id) == mid_str:
                return m
        return None

    def add(self, memory: SharedMemory, timeout: float = 10.0) -> SharedMemory:
        with FileLock(str(self.lock_path), timeout=timeout):
            memories = self._load_unlocked()
            memories.append(memory)
            self._atomic_write([_model_to_dict(m) for m in memories])
        return memory

    def delete(self, memory_id: UUID | str, timeout: float = 10.0) -> bool:
        mid_str = str(memory_id)
        with FileLock(str(self.lock_path), timeout=timeout):
            memories = self._load_unlocked()
            new_list = [m for m in memories if str(m.id) != mid_str]
            if len(new_list) == len(memories):
                return False
            self._atomic_write([_model_to_dict(m) for m in new_list])
            return True

    def _atomic_write(self, payload: Iterable[dict]) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        text = json.dumps(list(payload), ensure_ascii=False, indent=2, default=_json_default)
        tmp_path.write_text(text + "\n", encoding="utf-8")
        tmp_path.replace(self.path)
