from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterable, List, Optional, Type, TypeVar
from uuid import UUID

from filelock import FileLock
from pydantic import BaseModel

from common.paths import PLANS_FILE as DEFAULT_PLANS_FILE
from common.paths import NOTIFICATIONS_FILE as DEFAULT_NOTIFICATIONS_FILE
from .models import Notification, ScheduledJob

M = TypeVar("M", bound=BaseModel)


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


class _JsonStore:
    """File-based store for a list of pydantic models with OS-level file locking.

    Same pattern as tasks.storage.TaskStore (atomic tmp-file writes guarded by
    a FileLock), shared by the scheduler in the backend process and any agent
    subprocess that creates jobs via tools.
    """

    model: Type[BaseModel]

    def __init__(self, path: Path | str, model: Type[M]):
        self.path = Path(path)
        self.model = model
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write([])

    # ------------- public API -------------
    def load(self, timeout: float = 10.0) -> List[M]:
        with FileLock(str(self.lock_path), timeout=timeout):
            return self._load_unlocked()

    def list(self, timeout: float = 10.0) -> List[M]:
        return self.load(timeout=timeout)

    def get(self, item_id: UUID | str, timeout: float = 10.0) -> Optional[M]:
        iid = str(item_id)
        for item in self.load(timeout=timeout):
            if str(item.id) == iid:
                return item
        return None

    def add(self, item: M, timeout: float = 10.0) -> M:
        with FileLock(str(self.lock_path), timeout=timeout):
            items = self._load_unlocked()
            items.append(item)
            self._atomic_write([_model_to_dict(i) for i in items])
        return item

    def update(self, item_id: UUID | str, *, timeout: float = 10.0, **fields) -> Optional[M]:
        iid = str(item_id)
        with FileLock(str(self.lock_path), timeout=timeout):
            items = self._load_unlocked()
            updated: Optional[M] = None
            new_list: List[M] = []
            for item in items:
                if str(item.id) == iid:
                    data = _model_to_dict(item)
                    data.update(fields)
                    updated = self._parse(data)
                    if hasattr(updated, "touch"):
                        updated.touch()
                    new_list.append(updated)
                else:
                    new_list.append(item)
            if updated is None:
                return None
            self._atomic_write([_model_to_dict(i) for i in new_list])
            return updated

    def delete(self, item_id: UUID | str, timeout: float = 10.0) -> int:
        iid = str(item_id)
        with FileLock(str(self.lock_path), timeout=timeout):
            items = self._load_unlocked()
            new_list = [i for i in items if str(i.id) != iid]
            deleted = len(items) - len(new_list)
            if deleted:
                self._atomic_write([_model_to_dict(i) for i in new_list])
            return deleted

    def save(self, items: Iterable[M], timeout: float = 10.0) -> None:
        with FileLock(str(self.lock_path), timeout=timeout):
            self._atomic_write([_model_to_dict(i) for i in items])

    # ------------- internals -------------
    def _parse(self, data: dict) -> M:
        return self.model.model_validate(data)

    def _load_unlocked(self) -> List[M]:
        try:
            text = self.path.read_text(encoding="utf-8")
            if not text.strip():
                return []
            data = json.loads(text)
            if not isinstance(data, list):
                return []
        except Exception:
            return []
        items: List[M] = []
        for obj in data:
            try:
                items.append(self._parse(obj))
            except Exception:
                continue
        return items

    def _atomic_write(self, payload: Iterable[dict]) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        text = json.dumps(list(payload), ensure_ascii=False, indent=2, default=_json_default)
        tmp_path.write_text(text + "\n", encoding="utf-8")
        tmp_path.replace(self.path)


class PlanStore(_JsonStore):
    def __init__(self, path: Path | str | None = None):
        super().__init__(path or DEFAULT_PLANS_FILE, ScheduledJob)


class NotificationStore(_JsonStore):
    # Inbox cap — oldest entries are dropped when exceeded.
    MAX_ENTRIES = 500

    def __init__(self, path: Path | str | None = None):
        super().__init__(path or DEFAULT_NOTIFICATIONS_FILE, Notification)

    def add(self, item: Notification, timeout: float = 10.0) -> Notification:
        with FileLock(str(self.lock_path), timeout=timeout):
            items = self._load_unlocked()
            items.append(item)
            if len(items) > self.MAX_ENTRIES:
                items = items[-self.MAX_ENTRIES:]
            self._atomic_write([_model_to_dict(i) for i in items])
        return item

    def mark_all_read(self, workspace: Optional[str] = None, timeout: float = 10.0) -> int:
        with FileLock(str(self.lock_path), timeout=timeout):
            items = self._load_unlocked()
            changed = 0
            for n in items:
                if n.read:
                    continue
                if workspace and (n.workspace or "") != workspace:
                    continue
                n.read = True
                changed += 1
            if changed:
                self._atomic_write([_model_to_dict(i) for i in items])
            return changed


__all__ = ["PlanStore", "NotificationStore"]
