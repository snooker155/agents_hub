from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterable, List, Optional, Sequence
from uuid import UUID

from filelock import FileLock

from .models import SharedMemory
from common.paths import SHARED_MEMORY_FILE

try:  # pydantic v1
    from pydantic.json import pydantic_encoder as _pydantic_encoder  # type: ignore
except Exception:  # pydantic v2 or other
    _pydantic_encoder = None  # type: ignore


def _model_to_dict(obj) -> dict:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj.dict()  # type: ignore[attr-defined]


def _json_default(o):
    if _pydantic_encoder is not None:
        try:
            return _pydantic_encoder(o)
        except Exception:
            pass
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, UUID):
        return str(o)
    raise TypeError(f"Object of type {type(o)!r} is not JSON serializable")


class MemoryStore:
    """File-based store for SharedMemory objects."""

    def __init__(self, path: Path | str | None = None):
        if path is None:
            path = SHARED_MEMORY_FILE
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
            if not text.strip():
                return []
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
