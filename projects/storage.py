"""Project records, as a document collection.

A list of :class:`Project` models keyed by ``id``, kept in the ``documents``
table through :class:`common.docstore.DocStore` (one row per project). An
existing ``projects.json`` is imported once on first use and renamed
``.migrated`` (see ``common/docstore.py``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from pydantic import BaseModel

from common.docstore import DocStore
from common.paths import PROJECTS_FILE as DEFAULT_PROJECTS_FILE
from .models import Project


def _model_to_dict(obj: BaseModel) -> dict:
    # JSON mode: enums as their values, datetimes as ISO strings, UUIDs as
    # strings, exactly what the JSON file used to hold.
    return obj.model_dump(mode="json")


def _parse_project(data: dict) -> Project:
    data.setdefault("repo", {})
    data.setdefault("frontend", {})
    data.setdefault("backend", {})
    data.setdefault("tags", [])
    return Project.model_validate(data)


def _record_key(rec: Any) -> Optional[str]:
    return str(rec["id"]) if isinstance(rec, dict) and rec.get("id") else None


class ProjectStore:
    """A list of :class:`Project` models keyed by ``id`` over a :class:`DocStore`.

    ``path`` names the legacy JSON file the collection is imported from on
    first use (the constructor argument tests and callers already pass); the
    data itself lives in the database.
    """

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else Path(DEFAULT_PROJECTS_FILE)
        self.docs = DocStore("projects", legacy_file=self.path, legacy_key=_record_key)

    def load(self, timeout: float = 10.0) -> List[Project]:
        items: List[Project] = []
        for obj in self.docs.all().values():
            parsed = self._parse_or_none(obj)
            if parsed is not None:
                items.append(parsed)
        return items

    def list(self, timeout: float = 10.0) -> List[Project]:
        return self.load(timeout=timeout)

    def get(self, project_id: str, timeout: float = 10.0) -> Optional[Project]:
        doc = self.docs.get(str(project_id))
        return self._parse_or_none(doc) if doc is not None else None

    def add(self, project: Project, timeout: float = 10.0) -> Project:
        self.docs.put(str(project.id), _model_to_dict(project))
        return project

    def update(self, project_id: str, *, timeout: float = 10.0, **fields) -> Optional[Project]:
        pid = str(project_id)
        with self.docs.transaction():
            doc = self.docs.get(pid)
            if not isinstance(doc, dict):
                return None
            data = dict(doc)
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
            self.docs.put(pid, _model_to_dict(updated))
            return updated

    def delete(self, project_id: str, timeout: float = 10.0) -> bool:
        return self.docs.delete(str(project_id))

    def _parse_or_none(self, data: Any) -> Optional[Project]:
        try:
            return _parse_project(dict(data))
        except Exception:
            return None
