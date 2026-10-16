"""
Memory pools: the person's personal pool and the workspace's shared ones.

A pool is one DocStore record (memory/store.py) holding core blocks, notes
and structured slots. The personal pool's id is derived from (user,
workspace), so the same person has different ids on two hubs; the pool is
therefore found by what it is, not by id: :meth:`MemoryAdapter.apply` hands a
personal doc to ``ensure_pool(owner, workspace)`` and returns that pool's id
for the engine's mapping. Shared pools keep their uuid.

What is not in the document: the vectors and chunks of indexed files (derived
from the files, rebuilt where the file is indexed), the index bookkeeping on
``rag_files`` (``status``, ``indexed_at``, ``chunks``: they change when
something is indexed, which is not an edit), and, for a personal pool, its
name, description and creation time (generated from the local user's display
name on first use, so they would differ for the same content). Episodes,
graph and extractions are derived stores and stay behind.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from hubsync.adapters.base import Adapter, AdapterError, register
from memory import personal
from memory.models import MemoryBlock, SharedMemory
from memory.store import MemoryStore

#: ``rag_files`` entry fields that follow from indexing, not from editing.
_INDEX_FIELDS = ("status", "indexed_at", "chunks", "error")


def _rag_files(pool: SharedMemory) -> List[Dict[str, Any]]:
    return sorted(({k: v for k, v in f.items() if k not in _INDEX_FIELDS}
                   for f in pool.rag_files if isinstance(f, dict)),
                  key=lambda f: str(f.get("filename") or ""))


def _pool_id(record_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(record_id))
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"hubsync:memory:{record_id}")


class MemoryAdapter(Adapter):
    kind = "memory"
    order = 20
    label = "Memory"
    volatile = ("updated_at",)

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        ids = []
        for pool in MemoryStore().load():
            if pool.workspace != workspace:
                continue
            if pool.kind == personal.PERSONAL_KIND and pool.owner_user != owner:
                continue
            ids.append(str(pool.id))
        return sorted(ids)

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        pool = MemoryStore().get(record_id)
        if pool is None or pool.workspace != workspace:
            return None
        doc: Dict[str, Any] = {
            "kind": pool.kind,
            "blocks": [b.model_dump() for b in pool.blocks],
            "notes": [dict(n) for n in pool.notes],
            "structured_data": pool.structured_data,
            "rag_files": _rag_files(pool),
            "updated_at": pool.updated_at.isoformat(),
        }
        if pool.kind != personal.PERSONAL_KIND:
            doc.update(name=pool.name, type=pool.type, description=pool.description,
                       created_at=pool.created_at.isoformat())
        return doc

    def title(self, doc: Dict[str, Any]) -> str:
        return super().title(doc) or ("Personal memory" if doc.get("kind") == personal.PERSONAL_KIND else "")

    def existing_local(self, workspace: str, record_id: str, doc: Dict[str, Any], *,
                       owner: str) -> Optional[str]:
        # The person's personal pool here, if it already exists: their notes
        # on this device meet the incoming ones as a conflict, not an overwrite.
        if doc.get("kind") != personal.PERSONAL_KIND:
            return None
        pool_id = personal.pool_id(owner, workspace)
        return pool_id if MemoryStore().get(pool_id) is not None else None

    def merge(self, local: Dict[str, Any], incoming: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The person's personal pool met on a second device for the first
        time: both hold what that person wrote there, so keep both. Notes and
        files join by id and name, blocks and slots by name. Returns None when
        the two disagree about the same note, block or slot; the engine then
        keeps the incoming pool and records this one as the conflict's loser.
        A shared pool is one record with one id everywhere and never merges."""
        if local.get("kind") != personal.PERSONAL_KIND or incoming.get("kind") != personal.PERSONAL_KIND:
            return None
        notes = _join(incoming.get("notes"), local.get("notes"), key=lambda n: n.get("id"))
        blocks = _join(incoming.get("blocks"), local.get("blocks"), key=lambda b: b.get("name"),
                       empty=lambda b: not str(b.get("value") or "").strip())
        files = _join(incoming.get("rag_files"), local.get("rag_files"), key=lambda f: f.get("filename"))
        if notes is None or blocks is None or files is None:
            return None
        slots = dict(incoming.get("structured_data") or {})
        for name, value in (local.get("structured_data") or {}).items():
            if name in slots and slots[name] != value:
                return None
            slots[name] = value
        times = [t for t in (incoming.get("updated_at"), local.get("updated_at")) if t]
        return {**incoming, "notes": notes, "blocks": blocks, "rag_files": files, "structured_data": slots,
                **({"updated_at": max(times)} if times else {})}

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        store = MemoryStore()
        if doc.get("kind") == personal.PERSONAL_KIND:
            pool = personal.ensure_pool(owner, workspace)
        else:
            pool = store.get(_pool_id(record_id))
            if pool is None:
                pool = SharedMemory(id=_pool_id(record_id), name=str(doc.get("name") or "Memory"),
                                    workspace=workspace)
            elif pool.workspace != workspace or pool.kind == personal.PERSONAL_KIND:
                raise AdapterError(f"memory id {record_id} belongs to another workspace or person here")
            pool.name = str(doc.get("name") or pool.name)
            pool.type = str(doc.get("type") or pool.type)
            pool.description = str(doc.get("description") or "")
            created = _parse_time(doc.get("created_at"))
            if created is not None:
                pool.created_at = created
        try:
            pool.blocks = [MemoryBlock(**b) for b in doc.get("blocks") or []]
        except (TypeError, ValueError) as exc:
            raise AdapterError(f"memory blocks are not valid here: {exc}") from exc
        pool.notes = [dict(n) for n in doc.get("notes") or [] if isinstance(n, dict)]
        pool.structured_data = dict(doc.get("structured_data") or {})
        # Index state stays what this hub knows: an entry already indexed here
        # keeps its status, a new one waits to be indexed.
        local = {f.get("filename"): f for f in pool.rag_files if isinstance(f, dict)}
        pool.rag_files = []
        for entry in doc.get("rag_files") or []:
            kept = {k: v for k, v in (local.get(entry.get("filename")) or {}).items() if k in _INDEX_FIELDS}
            pool.rag_files.append({**entry, **({"status": "pending"} if not kept else kept)})
        pool.touch()
        store.add(pool)
        return str(pool.id)

    def delete(self, workspace: str, record_id: str) -> None:
        store = MemoryStore()
        pool = store.get(record_id)
        if pool is None or pool.workspace != workspace:
            return
        if pool.kind == personal.PERSONAL_KIND:
            # The personal pool is recreated on first use anyway; a deletion
            # from the other end clears what it holds instead of the record.
            pool.blocks, pool.notes, pool.structured_data, pool.rag_files = [], [], {}, []
            pool.touch()
            store.add(pool)
            return
        store.delete(record_id)


def _join(first: Any, second: Any, *, key, empty=lambda item: False) -> Optional[List[Dict[str, Any]]]:
    """``first`` then what only ``second`` has, matched by ``key``. An item
    both hold must be the same, unless one side's is empty (a block the pool
    was created with and nobody filled): then the filled one stays. None when
    they differ, since keeping one would silently drop the other."""
    out: List[Dict[str, Any]] = [dict(i) for i in first or [] if isinstance(i, dict)]
    index = {key(i): n for n, i in enumerate(out) if key(i) is not None}
    for item in second or []:
        if not isinstance(item, dict):
            continue
        k = key(item)
        if k is None or k not in index:
            out.append(dict(item))
            if k is not None:
                index[k] = len(out) - 1
            continue
        mine = out[index[k]]
        if mine == item or empty(item):
            continue
        if empty(mine):
            out[index[k]] = dict(item)
            continue
        return None
    return out


def _parse_time(value: Any):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


register(MemoryAdapter())
