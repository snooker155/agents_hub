"""
Each end's memory of what it last exchanged, per record (sync_state).

A row says: this record, in this space, last matched vault ``version`` with
content ``hash``, and here it is called ``local_id``. The engine compares the
hash a record has now with this one to find changes, and rows whose record
is gone to find deletions. ``owner_user`` is '' on a client; on the remote it
keeps two people who sync into one shared workspace apart.

``failed_version`` is a newer vault version this end could not write; it sits
beside the last good exchange instead of replacing it, so a local edit made
after the refusal is still seen as one. ``changed_hash`` and ``changed_at``
record when this end first saw its copy move away from ``hash``: the
modification time of a record whose kind keeps none.

Conflicts that lost (sync_conflicts) are kept here too, so the losing side of
a last-writer-wins decision is never simply gone.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from common import db

_COLUMNS = ["owner_user", "space", "kind", "record_id", "local_id", "hash", "version", "modified_at",
            "synced_at", "failed_version", "changed_hash", "changed_at"]
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rows(owner: str, space: str) -> Dict[tuple, Dict[str, Any]]:
    """{(kind, record_id): row} for one space."""
    out = {}
    for row in db.get_conn().execute("SELECT * FROM sync_state WHERE owner_user = ? AND space = ?",
                                     (owner, space)).fetchall():
        rec = dict(row)
        out[(rec["kind"], rec["record_id"])] = rec
    return out


def get(owner: str, space: str, kind: str, record_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM sync_state WHERE owner_user = ? AND space = ? AND kind = ? AND record_id = ?",
        (owner, space, kind, record_id)).fetchone()
    return dict(row) if row is not None else None


def records_for_local(owner: str, space: str, kind: str, local_id: str) -> List[str]:
    """Every sync id mapped to one local record, the one it travels under
    first: the id equal to the local id, then the lowest. The engine keeps a
    local record to one sync id, but a database from before that rule may
    hold two, and a pass must see both or it pushes the other as deleted."""
    found = [r["record_id"] for r in db.get_conn().execute(
        "SELECT record_id FROM sync_state WHERE owner_user = ? AND space = ? AND kind = ? AND local_id = ? "
        "ORDER BY record_id", (owner, space, kind, local_id)).fetchall()]
    return sorted(found, key=lambda rid: (rid != local_id, rid))


def record_for_local(owner: str, space: str, kind: str, local_id: str) -> Optional[str]:
    """The sync id a local record travels under, when it differs from its id
    here (a record created on the other end and given a local id on apply)."""
    found = records_for_local(owner, space, kind, local_id)
    return found[0] if found else None


def put(owner: str, space: str, kind: str, record_id: str, *, local_id: str, hash_: str,
        version: int, modified_at: Optional[str] = None, failed_version: int = 0,
        changed_hash: Optional[str] = None, changed_at: Optional[str] = None) -> None:
    with db.transaction() as conn:
        conn.execute(db.upsert_sql("sync_state", _COLUMNS, ["owner_user", "space", "kind", "record_id"]),
                     (owner, space, kind, record_id, local_id, hash_, int(version), modified_at or _now(), _now(),
                      int(failed_version or 0), changed_hash, changed_at))


def note_change(owner: str, space: str, kind: str, record_id: str, *, hash_: str, at: str) -> None:
    """Remember that the local copy now hashes to ``hash_`` and since when."""
    with db.transaction() as conn:
        conn.execute("UPDATE sync_state SET changed_hash = ?, changed_at = ? "
                     "WHERE owner_user = ? AND space = ? AND kind = ? AND record_id = ?",
                     (hash_, at, owner, space, kind, record_id))


def forget(owner: str, space: str, kind: str, record_id: str) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM sync_state WHERE owner_user = ? AND space = ? AND kind = ? AND record_id = ?",
                     (owner, space, kind, record_id))


def forget_space(owner: str, space: str) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM sync_state WHERE owner_user = ? AND space = ?", (owner, space))


def add_conflict(owner: str, space: str, kind: str, record_id: str, *, kept: str,
                 lost_doc: Optional[Dict[str, Any]]) -> None:
    with db.transaction() as conn:
        conn.execute("INSERT INTO sync_conflicts (owner_user, space, kind, record_id, kept, lost_doc, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (owner, space, kind, record_id, kept,
                      json.dumps(lost_doc, ensure_ascii=False) if lost_doc is not None else None, _now()))


def conflicts(owner: str, space: Optional[str] = None, *, open_only: bool = True) -> List[Dict[str, Any]]:
    sql = "SELECT conflict_id, owner_user, space, kind, record_id, kept, created_at, resolved_at " \
          "FROM sync_conflicts WHERE owner_user = ?"
    params: List[Any] = [owner]
    if space:
        sql += " AND space = ?"
        params.append(space)
    if open_only:
        sql += " AND resolved_at IS NULL"
    sql += " ORDER BY conflict_id DESC LIMIT 200"
    return [dict(r) for r in db.get_conn().execute(sql, params).fetchall()]


def conflict_doc(conflict_id: int) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute("SELECT * FROM sync_conflicts WHERE conflict_id = ?", (int(conflict_id),)).fetchone()
    if row is None:
        return None
    rec = dict(row)
    rec["lost_doc"] = json.loads(rec["lost_doc"]) if rec.get("lost_doc") else None
    return rec


def resolve_conflict(conflict_id: int) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE sync_conflicts SET resolved_at = ? WHERE conflict_id = ?", (_now(), int(conflict_id)))


__all__ = ["add_conflict", "conflict_doc", "conflicts", "forget", "forget_space", "get", "note_change", "put",
           "record_for_local", "records_for_local", "resolve_conflict", "rows"]
