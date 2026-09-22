from __future__ import annotations

import copy
import json
import os
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterable, List, Optional, Sequence
from uuid import UUID

from filelock import FileLock

from .models import SharedMemory
from common.paths import SHARED_MEMORY_FILE

from pydantic_core import to_jsonable_python as _pydantic_encoder


def _model_to_dict(obj) -> dict:
    return obj.model_dump()


def _json_default(o):
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


# ---------------------------------------------------------------------------
# Read cache — same mtime-keyed shape as agents/registry.py's _REGISTRY_CACHE.
#
# Every layer (recall, injection, the routes) re-read the whole JSON file on
# each call, so one request paid the disk read, the lock and the parse a dozen
# times over. The cache holds the RAW dicts per file path and hands out a deep
# copy on each hit: callers mutate the models they get back (a note's content,
# a slot's data) and a shared object would leak those edits into the next
# reader before anything was saved.
# ---------------------------------------------------------------------------

_STORE_CACHE: dict = {}


def _file_stamp(path: Path):
    """(mtime_ns, size) for *path*, or None when it cannot be stat'ed."""
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def clear_cache() -> None:
    """Drop every cached file. Used by tests and after an out-of-band write."""
    _STORE_CACHE.clear()


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
        cached = self._cached_raw()
        if cached is not None:
            return self._build(cached)
        with FileLock(str(self.lock_path), timeout=timeout):
            return self._load_unlocked()

    # ── cache plumbing ──────────────────────────────────────────────────────

    def _cache_key(self) -> str:
        return str(self.path)

    def _cached_raw(self) -> Optional[list]:
        """Cached raw dicts when the file has not changed since they were read."""
        entry = _STORE_CACHE.get(self._cache_key())
        if not entry:
            return None
        if entry.get("stamp") != _file_stamp(self.path):
            return None
        return entry.get("raw")

    def _store_raw(self, raw: list) -> None:
        _STORE_CACHE[self._cache_key()] = {"stamp": _file_stamp(self.path), "raw": raw}

    def _invalidate(self) -> None:
        _STORE_CACHE.pop(self._cache_key(), None)

    @staticmethod
    def _build(raw: list) -> List[SharedMemory]:
        """Models from raw dicts, deep-copied so callers can mutate them freely."""
        out: List[SharedMemory] = []
        for obj in raw:
            try:
                out.append(SharedMemory(**copy.deepcopy(obj)))
            except Exception:
                continue
        return out

    def _load_unlocked(self) -> List[SharedMemory]:
        try:
            text = self.path.read_text(encoding="utf-8")
            if not text.strip():
                self._store_raw([])
                return []
            data = json.loads(text)
            self._store_raw(data)
            return self._build(data)
        except Exception:
            self._invalidate()
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
        # The next read re-parses from disk: another process may have written
        # between our read and this one, and the stamp alone would not say so.
        self._invalidate()
