"""
Version history for a memory pool's blocks, notes and structured slots
(fourth-cycle stage 2, feature G: memory with versions).

:func:`record_changes` is the hook :mod:`memory.store` calls on every write: it
diffs a pool's blocks (by name), notes (by id) and structured slots (by name)
between the value before and after the write, and writes one row per changed
item to the ``memory_versions`` table (``common/migrations/0020_memory_versions.py``).
Nothing else writes that table directly, so this module is the one place that
knows its shape.

:func:`restore` and :func:`redact` are the two actions a person takes on a
past version: putting an item back to what it held then, or scrubbing a row
that should not have kept its content. Both go through the store for the
current-item write (so every other reader of a pool sees the same result they
would from an ordinary edit), but with the store's history hook turned off for
that one write (``record_history=False``): the row this module then inserts
itself, tagged ``restore`` or ``redact``, is the only row that action produces,
rather than an extra generic ``update`` row on top of it.

Best-effort throughout: every public function that runs inside a store write
(:func:`record_changes`) swallows its own errors, because a broken history
must never take an agent's or a person's memory write down with it. The two
actions a person asks for on purpose (:func:`restore`, :func:`redact`) raise
instead, because there a silent no-op would be the wrong failure.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from common import db

from .models import MemoryBlock, SharedMemory

log = logging.getLogger(__name__)

KIND_BLOCK = "block"
KIND_NOTE = "note"
KIND_SLOT = "slot"

OP_CREATE = "create"
OP_UPDATE = "update"
OP_DELETE = "delete"
OP_RESTORE = "restore"
OP_REDACT = "redact"

#: How many versions of one item (a pool, a kind and an item key) to keep.
#: Older rows past this count are dropped as new ones are written.
KEEP_ENV = "AGENTS_HUB_MEMORY_VERSIONS_KEEP"
DEFAULT_KEEP = 50

#: What a redacted row's value becomes. A marker, not an empty string, so a
#: reader can tell "redacted" apart from "the item was empty".
REDACTION_MARKER = "[redacted]"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _keep_n() -> int:
    try:
        return max(1, int(os.environ.get(KEEP_ENV, DEFAULT_KEEP)))
    except (TypeError, ValueError):
        return DEFAULT_KEEP


# ── who did it ────────────────────────────────────────────────────────────────

def _resolve_actor() -> Dict[str, Optional[str]]:
    """The actor and run for a memory write happening right now.

    An agent running a task carries its id on ``common.agent_context``'s
    ``current_agent_id`` (set for the whole call stack of one run by
    ``agents/agent_invoke.py``) or, in a run subprocess that started before
    that context existed, on the ``AGENT_ID``/``AGENT_RUN_ID`` environment
    variables. Anything else is a request carrying a signed-in user, which
    ``common.identity.current_user_id`` already resolves for every other
    store that stamps an owner. Neither present is the system acting on its
    own, which happens outside a run and outside a request alike.
    """
    run_id = os.environ.get("AGENT_RUN_ID") or None
    agent_id = None
    try:
        from common.agent_context import current_agent_id
        agent_id = current_agent_id.get()
    except Exception:  # noqa: BLE001 - actor resolution must not block a memory write
        agent_id = None
    if not agent_id:
        agent_id = os.environ.get("AGENT_ID") or None
    if agent_id:
        return {"actor_kind": "agent", "actor_id": str(agent_id), "run_id": run_id}
    user_id = None
    try:
        from common.identity import current_user_id
        user_id = current_user_id()
    except Exception:  # noqa: BLE001 - actor resolution must not block a memory write
        user_id = None
    if user_id:
        return {"actor_kind": "user", "actor_id": str(user_id), "run_id": run_id}
    return {"actor_kind": "system", "actor_id": None, "run_id": run_id}


# ── diffing a pool by item ───────────────────────────────────────────────────

def _block_items(mem: Optional[SharedMemory]) -> Dict[str, dict]:
    if mem is None:
        return {}
    return {b.name: b.model_dump(mode="json") for b in (mem.blocks or [])}


def _note_items(mem: Optional[SharedMemory]) -> Dict[str, dict]:
    if mem is None:
        return {}
    out: Dict[str, dict] = {}
    for note in (mem.notes or []):
        key = note.get("id")
        if key:
            out[str(key)] = dict(note)
    return out


def _slot_items(mem: Optional[SharedMemory]) -> Dict[str, Any]:
    if mem is None:
        return {}
    return {str(k): v for k, v in (mem.structured_data or {}).items()}


def _diff_kind(before_items: Dict[str, Any], after_items: Dict[str, Any]) -> List[tuple]:
    """``[(item_key, op, value)]`` for every item that changed between the two."""
    ordered: List[str] = []
    seen = set()
    for key in list(before_items.keys()) + [k for k in after_items if k not in before_items]:
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    out: List[tuple] = []
    for key in ordered:
        before = before_items.get(key)
        after = after_items.get(key)
        if before is None and after is not None:
            out.append((key, OP_CREATE, after))
        elif before is not None and after is None:
            out.append((key, OP_DELETE, None))
        elif before is not None and after is not None and before != after:
            out.append((key, OP_UPDATE, after))
    return out


def record_changes(before: Optional[SharedMemory], after: Optional[SharedMemory]) -> None:
    """Diff *before* and *after* by block, note and slot; write one row per
    changed item. Called by :mod:`memory.store` on every ``add``, ``save``
    and ``delete``; never raises, so a history problem never blocks the
    memory write it is describing.
    """
    memory_id = str(after.id) if after is not None else (str(before.id) if before is not None else None)
    if memory_id is None:
        return
    try:
        changes: List[tuple] = []
        for kind, before_items, after_items in (
            (KIND_BLOCK, _block_items(before), _block_items(after)),
            (KIND_NOTE, _note_items(before), _note_items(after)),
            (KIND_SLOT, _slot_items(before), _slot_items(after)),
        ):
            for item_key, op, value in _diff_kind(before_items, after_items):
                changes.append((kind, item_key, op, value))
        if not changes:
            return
        actor = _resolve_actor()
        with db.transaction() as conn:
            for kind, item_key, op, value in changes:
                _insert_version(conn, memory_id, kind, item_key, op, value, actor)
    except Exception:  # noqa: BLE001 - version history must never block a memory write
        log.warning("memory_versions: could not record history for pool %s", memory_id, exc_info=True)


# ── storage ───────────────────────────────────────────────────────────────────

def _insert_version(conn: Any, memory_id: str, kind: str, item_key: str, op: str,
                    value: Any, actor: Dict[str, Optional[str]], *,
                    redacted: bool = False) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM memory_versions "
        "WHERE memory_id = ? AND kind = ? AND item_key = ?",
        (memory_id, kind, item_key)).fetchone()
    next_version = int((row[0] if row else 0) or 0) + 1
    value_text = json.dumps(value, ensure_ascii=False, default=str) if value is not None else None
    conn.execute(
        "INSERT INTO memory_versions (memory_id, kind, item_key, version, op, value, "
        "actor_kind, actor_id, run_id, at, redacted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (memory_id, kind, item_key, next_version, op, value_text,
         actor.get("actor_kind"), actor.get("actor_id"), actor.get("run_id"), _now(),
         1 if redacted else 0),
    )
    _prune_item(conn, memory_id, kind, item_key)
    return next_version


def _prune_item(conn: Any, memory_id: str, kind: str, item_key: str) -> None:
    """Keep only the newest :data:`KEEP_ENV` versions of one item."""
    keep = _keep_n()
    rows = conn.execute(
        "SELECT id FROM memory_versions WHERE memory_id = ? AND kind = ? AND item_key = ? "
        "ORDER BY version DESC", (memory_id, kind, item_key)).fetchall()
    doomed = [r["id"] for r in rows[keep:]]
    if not doomed:
        return
    placeholders = ",".join("?" for _ in doomed)
    conn.execute(f"DELETE FROM memory_versions WHERE id IN ({placeholders})", tuple(doomed))


def _row_to_dict(row: Any) -> Dict[str, Any]:
    try:
        value = json.loads(row["value"]) if row["value"] is not None else None
    except (TypeError, ValueError):
        value = row["value"]
    return {
        "id": row["id"], "memory_id": row["memory_id"], "kind": row["kind"],
        "item_key": row["item_key"], "version": row["version"], "op": row["op"],
        "value": value, "actor_kind": row["actor_kind"], "actor_id": row["actor_id"],
        "run_id": row["run_id"], "at": row["at"], "redacted": bool(row["redacted"]),
    }


def list_versions(memory_id: Any, kind: Optional[str] = None, item_key: Optional[str] = None,
                  limit: int = 100) -> List[Dict[str, Any]]:
    """A pool's version rows, newest first, optionally narrowed to one kind
    and/or one item."""
    where = ["memory_id = ?"]
    args: List[Any] = [str(memory_id)]
    if kind:
        where.append("kind = ?")
        args.append(str(kind))
    if item_key:
        where.append("item_key = ?")
        args.append(str(item_key))
    clause = " AND ".join(where)
    limit = max(1, min(int(limit or 100), 1000))
    rows = db.get_conn().execute(
        f"SELECT * FROM memory_versions WHERE {clause} ORDER BY id DESC LIMIT ?",
        tuple(args) + (limit,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_version(version_id: Any) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM memory_versions WHERE id = ?", (int(version_id),)).fetchone()
    return _row_to_dict(row) if row else None


# ── applying a version back to the pool ──────────────────────────────────────

def _set_item(mem: SharedMemory, kind: str, item_key: str, value: Any) -> None:
    if kind == KIND_BLOCK:
        data = dict(value) if isinstance(value, dict) else {}
        data["name"] = item_key
        mem.blocks = [b for b in mem.blocks if b.name != item_key]
        mem.blocks.append(MemoryBlock(**data))
    elif kind == KIND_NOTE:
        data = dict(value) if isinstance(value, dict) else {}
        data["id"] = item_key
        mem.notes = [n for n in mem.notes if str(n.get("id")) != str(item_key)]
        mem.notes.append(data)
    elif kind == KIND_SLOT:
        mem.structured_data[item_key] = value if isinstance(value, dict) else {"value": value}
    else:
        raise ValueError(f"unknown kind {kind!r}")


def _remove_item(mem: SharedMemory, kind: str, item_key: str) -> None:
    if kind == KIND_BLOCK:
        mem.blocks = [b for b in mem.blocks if b.name != item_key]
    elif kind == KIND_NOTE:
        mem.notes = [n for n in mem.notes if str(n.get("id")) != str(item_key)]
    elif kind == KIND_SLOT:
        mem.structured_data.pop(item_key, None)
    else:
        raise ValueError(f"unknown kind {kind!r}")


def _load_pool_for_write(store: Any, memory_id: Any):
    """The full pool list plus the index of *memory_id* within it, the same
    load-all-save-all shape every other memory writer uses (``_persist_mem``
    in ``routes/memory.py``, ``_persist`` in ``memory/tool.py``): saving a
    shorter list than the store holds would drop every other pool."""
    memories = store.load()
    idx = next((i for i, m in enumerate(memories) if str(m.id) == str(memory_id)), None)
    return memories, idx


def restore(memory_id: Any, version_id: Any,
           actor: Optional[Dict[str, Optional[str]]] = None) -> Dict[str, Any]:
    """Set an item's current value back to what *version_id* held.

    Restoring a row whose value is ``None`` (a delete) deletes the item
    again. Writes exactly one new row, tagged ``restore``, continuing the
    same per-item version sequence; the store's own history hook is off for
    this write so it does not also log a generic ``update``.
    """
    version = get_version(version_id)
    if version is None:
        raise ValueError(f"version {version_id} not found")
    if str(version["memory_id"]) != str(memory_id):
        raise ValueError("that version does not belong to this memory pool")

    from .store import MemoryStore
    store = MemoryStore()
    memories, idx = _load_pool_for_write(store, memory_id)
    if idx is None:
        raise ValueError(f"memory pool {memory_id} not found")
    mem = memories[idx]

    kind, item_key, value = version["kind"], version["item_key"], version["value"]
    if value is None:
        _remove_item(mem, kind, item_key)
    else:
        _set_item(mem, kind, item_key, value)
    mem.touch()
    memories[idx] = mem
    store.save(memories, record_history=False)

    resolved_actor = actor or _resolve_actor()
    with db.transaction() as conn:
        new_version = _insert_version(conn, str(memory_id), kind, item_key, OP_RESTORE,
                                      value, resolved_actor)
    return {"memory_id": str(memory_id), "kind": kind, "item_key": item_key,
            "version": new_version, "op": OP_RESTORE, "value": value}


def _current_matches(mem: SharedMemory, kind: str, item_key: str, original_value: Any) -> bool:
    """Whether the pool's live item still holds exactly what *original_value*
    recorded, the bar for :func:`redact`'s ``also_current`` to touch it: a
    later, unrelated edit is not this row's content to remove."""
    if kind == KIND_BLOCK:
        block = mem.get_block(item_key)
        return block is not None and block.model_dump(mode="json") == original_value
    if kind == KIND_NOTE:
        note = next((n for n in mem.notes if str(n.get("id")) == str(item_key)), None)
        return note is not None and dict(note) == original_value
    if kind == KIND_SLOT:
        return mem.structured_data.get(item_key) == original_value
    return False


def _redact_item(mem: SharedMemory, kind: str, item_key: str) -> None:
    if kind == KIND_BLOCK:
        block = mem.get_block(item_key)
        if block is not None:
            block.value = REDACTION_MARKER
    elif kind == KIND_NOTE:
        for note in mem.notes:
            if str(note.get("id")) == str(item_key):
                note["content"] = REDACTION_MARKER
                break
    elif kind == KIND_SLOT:
        if item_key in mem.structured_data:
            mem.structured_data[item_key] = {"redacted": True}


def redact(memory_id: Any, version_id: Any, actor: Optional[Dict[str, Optional[str]]] = None,
          *, also_current: bool = False) -> Dict[str, Any]:
    """Scrub a past version's content: its ``value`` becomes a marker and it
    is flagged ``redacted``, and one further row, also ``redacted`` and with
    no ``value``, records that the scrub happened.

    With ``also_current=True``, the pool's live item is scrubbed the same way
    too, but only when it still holds exactly what this version recorded
    (:func:`_current_matches`); an item edited since is left alone, because
    the newer content is not what this row is being redacted for.
    """
    version = get_version(version_id)
    if version is None:
        raise ValueError(f"version {version_id} not found")
    if str(version["memory_id"]) != str(memory_id):
        raise ValueError("that version does not belong to this memory pool")

    kind, item_key = version["kind"], version["item_key"]
    original_value = version["value"]
    resolved_actor = actor or _resolve_actor()

    if not version.get("redacted"):
        with db.transaction() as conn:
            conn.execute(
                "UPDATE memory_versions SET value = ?, redacted = 1 WHERE id = ?",
                (json.dumps(REDACTION_MARKER), int(version_id)),
            )

    current_scrubbed = False
    if also_current and original_value is not None:
        from .store import MemoryStore
        store = MemoryStore()
        memories, idx = _load_pool_for_write(store, memory_id)
        if idx is not None:
            mem = memories[idx]
            if _current_matches(mem, kind, item_key, original_value):
                _redact_item(mem, kind, item_key)
                mem.touch()
                memories[idx] = mem
                store.save(memories, record_history=False)
                current_scrubbed = True

    with db.transaction() as conn:
        new_version = _insert_version(conn, str(memory_id), kind, item_key, OP_REDACT, None,
                                      resolved_actor, redacted=True)

    return {"memory_id": str(memory_id), "kind": kind, "item_key": item_key,
            "version": new_version, "op": OP_REDACT, "current_scrubbed": current_scrubbed}


__all__ = [
    "DEFAULT_KEEP", "KEEP_ENV", "KIND_BLOCK", "KIND_NOTE", "KIND_SLOT",
    "OP_CREATE", "OP_DELETE", "OP_REDACT", "OP_RESTORE", "OP_UPDATE",
    "REDACTION_MARKER", "get_version", "list_versions", "record_changes",
    "redact", "restore",
]
