from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterable, List, Optional
from uuid import UUID

from filelock import FileLock
from pydantic import BaseModel

from .models import Project
from common.paths import PROJECTS_FILE


def _model_to_dict(obj: BaseModel) -> dict:
    return obj.model_dump()


def _json_default(o):
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, UUID):
        return str(o)
    raise TypeError(f"Object of type {type(o)!r} is not JSON serializable")


def _parse_project(data: dict) -> Project:
    data.setdefault("repo", {})
    data.setdefault("frontend", {})
    data.setdefault("backend", {})
    data.setdefault("tags", [])
    return Project.model_validate(data)


class ProjectStore:
    """File-based store for Project objects with OS-level file locking."""

    def __init__(self, path: Path | str | None = None):
        if path is None:
            path = PROJECTS_FILE
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write([])

    def load(self, timeout: float = 10.0) -> List[Project]:
        with FileLock(str(self.lock_path), timeout=timeout):
            return self._load_unlocked()

    def list(self, timeout: float = 10.0) -> List[Project]:
        return self.load(timeout=timeout)

    def get(self, project_id: str, timeout: float = 10.0) -> Optional[Project]:
        for p in self.load(timeout=timeout):
            if p.id == project_id:
                return p
        return None

    def add(self, project: Project, timeout: float = 10.0) -> Project:
        with FileLock(str(self.lock_path), timeout=timeout):
            projects = self._load_unlocked()
            projects.append(project)
            self._atomic_write([_model_to_dict(p) for p in projects])
        return project

    def update(self, project_id: str, *, timeout: float = 10.0, **fields) -> Optional[Project]:
        with FileLock(str(self.lock_path), timeout=timeout):
            projects = self._load_unlocked()
            updated: Optional[Project] = None
            new_list: List[Project] = []
            for p in projects:
                if p.id == project_id:
                    data = _model_to_dict(p)
                    # Handle nested config updates
                    for key, val in fields.items():
                        if key in ("repo", "frontend", "backend") and isinstance(val, dict):
                            existing = data.get(key, {})
                            existing.update(val)
                            data[key] = existing
                        else:
                            data[key] = val
                    updated = _parse_project(data)
                    updated.touch()
                    new_list.append(updated)
                else:
                    new_list.append(p)
            if updated is None:
                return None
            self._atomic_write([_model_to_dict(p) for p in new_list])
            return updated

    def delete(self, project_id: str, timeout: float = 10.0) -> bool:
        with FileLock(str(self.lock_path), timeout=timeout):
            projects = self._load_unlocked()
            new_list = [p for p in projects if p.id != project_id]
            if len(new_list) == len(projects):
                return False
            self._atomic_write([_model_to_dict(p) for p in new_list])
            return True

    def _load_unlocked(self) -> List[Project]:
        try:
            text = self.path.read_text(encoding="utf-8")
            if not text.strip():
                return []
            data = json.loads(text)
            if not isinstance(data, list):
                return []
        except (FileNotFoundError, Exception):
            return []

        items: List[Project] = []
        for obj in data:
            try:
                items.append(_parse_project(obj))
            except Exception:
                continue
        return items

    def _atomic_write(self, payload: Iterable[dict]) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        text = json.dumps(list(payload), ensure_ascii=False, indent=2, default=_json_default)
        tmp_path.write_text(text + "\n", encoding="utf-8")
        tmp_path.replace(self.path)
