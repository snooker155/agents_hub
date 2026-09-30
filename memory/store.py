from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional, Sequence
from uuid import UUID

from common.docstore import DocStore
from common.paths import SHARED_MEMORY_FILE

from .models import SharedMemory

log = logging.getLogger(__name__)


def _model_to_dict(obj: SharedMemory) -> dict:
    # JSON mode: UUIDs and datetimes as strings, exactly what the JSON file
    # used to hold.
    return obj.model_dump(mode="json")


def _record_key(rec: Any) -> Optional[str]:
    return str(rec.get("id")) if isinstance(rec, dict) and rec.get("id") else None


def clear_cache() -> None:
    """No-op kept for callers (tests, out-of-band writers): the database is
    the cache now, so there is nothing to invalidate."""
    return None


def _record_history(before: Optional[SharedMemory], after: Optional[SharedMemory]) -> None:
    """Best-effort hook into memory/versions.py's :func:`record_changes`.

    Imported lazily (versions.py imports :class:`MemoryStore` back for
    ``restore``/``redact``, so a module-level import here would cycle) and
    wrapped so a broken history never blocks the memory write it describes.
    """
    try:
        from memory.versions import record_changes
        record_changes(before, after)
    except Exception:  # noqa: BLE001 - version history must never block a memory write
        log.warning("memory store: version history hook failed", exc_info=True)


class MemoryStore:
    """Store for SharedMemory objects, kept in the ``documents`` table through
    :class:`common.docstore.DocStore` (one row per pool). ``path`` names the
    legacy ``shared_memory.json`` the collection is imported from on first
    use; the data itself lives in the database."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else SHARED_MEMORY_FILE
        self.docs = DocStore("shared_memory", legacy_file=self.path, legacy_key=_record_key)

    def load(self, timeout: float = 10.0) -> List[SharedMemory]:
        return self._build(self.docs.values())

    @staticmethod
    def _build(raw: Sequence[Any]) -> List[SharedMemory]:
        out: List[SharedMemory] = []
        for obj in raw:
            try:
                out.append(SharedMemory(**obj))
            except Exception:
                continue
        return out

    def save(self, memories: Sequence[SharedMemory], timeout: float = 10.0, *,
             record_history: bool = True) -> None:
        """Replace the whole collection with *memories*.

        ``record_history=False`` skips the version-history hook for this
        write: :mod:`memory.versions`'s ``restore`` and ``redact`` pass it so
        their own explicit row is the only one the write produces, rather
        than an extra generic ``update`` on top of it.
        """
        if record_history:
            for mem in memories:
                _record_history(self.get(mem.id), mem)
        self.docs.replace_all({str(m.id): _model_to_dict(m) for m in memories})

    def get(self, memory_id: UUID | str, timeout: float = 10.0) -> Optional[SharedMemory]:
        doc = self.docs.get(str(memory_id))
        if doc is None:
            return None
        try:
            return SharedMemory(**doc)
        except Exception:
            return None

    def add(self, memory: SharedMemory, timeout: float = 10.0, *,
            record_history: bool = True) -> SharedMemory:
        if record_history:
            _record_history(self.get(memory.id), memory)
        self.docs.put(str(memory.id), _model_to_dict(memory))
        return memory

    def delete(self, memory_id: UUID | str, timeout: float = 10.0, *,
              record_history: bool = True) -> bool:
        before = self.get(memory_id) if record_history else None
        deleted = self.docs.delete(str(memory_id))
        if record_history and deleted and before is not None:
            _record_history(before, None)
        return deleted
