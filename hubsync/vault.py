"""
The remote's side of a sync: the vault every client of one person pushes to
and pulls from.

A record is (owner, space, kind, record_id) with a ``version`` that only goes
up and a ``seq`` from one counter shared by all spaces. A client pulls
"everything after seq N" and pushes "this record, based on version V": the
push lands only when V is still current, otherwise the client gets the
current record back and resolves the conflict itself. The server never
merges, because with encryption on it cannot read what it would merge.

Deletions are records too (``deleted``, no payload), so a client that was
away still learns that something went.

The clear columns are chosen to say nothing a person would call content:
kind, id, size, a modification time and which key sealed it. A title, a
message, a file name all travel inside the payload, encrypted when the space
is.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from common import db

#: One push carries at most this many records; a client splits larger sets.
MAX_PUSH = 200
#: And at most this much payload in total, so one request stays bounded.
MAX_PUSH_BYTES = 64 * 1024 * 1024
MAX_PULL = 200
PAYLOAD_LIMIT = 48 * 1024 * 1024


class VaultError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(row: Any, *, with_payload: bool = True) -> Dict[str, Any]:
    rec = dict(row)
    rec["encrypted"] = bool(rec.get("encrypted"))
    rec["deleted"] = bool(rec.get("deleted"))
    if not with_payload:
        rec.pop("payload", None)
    return rec


def _next_seq(conn: Any) -> int:
    return int(conn.execute("INSERT INTO sync_seq (at) VALUES (?) RETURNING seq", (_now(),)).fetchone()["seq"])


# ── spaces and keys ──────────────────────────────────────────────────────────

def ensure_space(owner: str, space: str, *, encrypted: bool, key_id: Optional[str], origin: str = "") -> Dict:
    """Register (or reconfigure) what a person syncs into one space. Turning
    encryption on or off for a space that already holds records of the other
    kind is refused: the records would be half readable, half not."""
    if encrypted and not key_id:
        raise VaultError("an encrypted space needs a key")
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM sync_spaces WHERE owner_user = ? AND space = ?",
                           (owner, space)).fetchone()
        mixed = conn.execute(
            "SELECT COUNT(*) AS n FROM sync_records WHERE owner_user = ? AND space = ? AND deleted = 0 "
            "AND (encrypted != ? OR (encrypted = 1 AND key_id != ?))",
            (owner, space, 1 if encrypted else 0, key_id or "")).fetchone()["n"]
        if mixed:
            raise VaultError("this space already holds records sealed differently; clear it first", status=409)
        if row is None:
            conn.execute("INSERT INTO sync_spaces (owner_user, space, encrypted, key_id, origin, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (owner, space, 1 if encrypted else 0, key_id, origin, _now()))
        else:
            conn.execute("UPDATE sync_spaces SET encrypted = ?, key_id = ?, origin = ? "
                         "WHERE owner_user = ? AND space = ?",
                         (1 if encrypted else 0, key_id, origin or row["origin"], owner, space))
    return get_space(owner, space) or {}


def get_space(owner: str, space: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute("SELECT * FROM sync_spaces WHERE owner_user = ? AND space = ?",
                                (owner, space)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["encrypted"] = bool(out.get("encrypted"))
    return out


def list_spaces(owner: str) -> List[Dict[str, Any]]:
    conn = db.get_conn()
    spaces = [dict(r) for r in conn.execute(
        "SELECT * FROM sync_spaces WHERE owner_user = ? ORDER BY space", (owner,)).fetchall()]
    for space in spaces:
        space["encrypted"] = bool(space.get("encrypted"))
        space["counts"] = counts(owner, space["space"])
    return spaces


def spaces_holding(space: str) -> List[Dict[str, Any]]:
    """Every person syncing into a workspace, for the banner that workspace
    shows: who has encrypted records here and how many."""
    rows = db.get_conn().execute("SELECT * FROM sync_spaces WHERE space = ?", (space,)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["encrypted"] = bool(item.get("encrypted"))
        item["counts"] = counts(item["owner_user"], space)
        out.append(item)
    return out


def counts(owner: str, space: str) -> Dict[str, Any]:
    rows = db.get_conn().execute(
        "SELECT kind, COUNT(*) AS n, SUM(CASE WHEN encrypted = 1 THEN 1 ELSE 0 END) AS enc, "
        "SUM(COALESCE(size, 0)) AS bytes FROM sync_records "
        "WHERE owner_user = ? AND space = ? AND deleted = 0 GROUP BY kind", (owner, space)).fetchall()
    by_kind = {r["kind"]: int(r["n"]) for r in rows}
    return {"total": sum(by_kind.values()), "encrypted": sum(int(r["enc"] or 0) for r in rows),
            "bytes": sum(int(r["bytes"] or 0) for r in rows), "by_kind": by_kind}


def delete_space(owner: str, space: str) -> int:
    with db.transaction() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM sync_records WHERE owner_user = ? AND space = ?",
                         (owner, space)).fetchone()["n"]
        conn.execute("DELETE FROM sync_records WHERE owner_user = ? AND space = ?", (owner, space))
        conn.execute("DELETE FROM sync_spaces WHERE owner_user = ? AND space = ?", (owner, space))
    return int(n)


def put_key(owner: str, *, key_id: str, kdf: str, salt: str, iterations: int, verifier: str) -> Dict:
    if not (key_id and salt and verifier) or int(iterations) < 100_000:
        raise VaultError("a key needs an id, a salt, a verifier and at least 100000 iterations")
    with db.transaction() as conn:
        conn.execute(db.upsert_sql("sync_keys", ["owner_user", "key_id", "kdf", "salt", "iterations",
                                                 "verifier", "created_at"], ["owner_user", "key_id"]),
                     (owner, key_id, kdf, salt, int(iterations), verifier, _now()))
    return get_key(owner, key_id) or {}


def get_key(owner: str, key_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute("SELECT * FROM sync_keys WHERE owner_user = ? AND key_id = ?",
                                (owner, key_id)).fetchone()
    return dict(row) if row else None


def delete_key(owner: str, key_id: str) -> bool:
    """Remove a key nothing is sealed with: the way back from a generated
    passphrase nobody wrote down. A key that sealed even one record stays,
    since without it those records never open again. An encrypted space that
    names the key and holds no records goes with it; the client registers its
    spaces again with the next key."""
    with db.transaction() as conn:
        sealed = conn.execute(
            "SELECT COUNT(*) AS n FROM sync_records WHERE owner_user = ? AND key_id = ? AND deleted = 0",
            (owner, key_id)).fetchone()["n"]
        if sealed:
            raise VaultError(f"{sealed} records are sealed with this key; it stays", status=409)
        found = conn.execute("SELECT COUNT(*) AS n FROM sync_keys WHERE owner_user = ? AND key_id = ?",
                             (owner, key_id)).fetchone()["n"]
        conn.execute("DELETE FROM sync_spaces WHERE owner_user = ? AND key_id = ? AND encrypted = 1 "
                     "AND NOT EXISTS (SELECT 1 FROM sync_records r WHERE r.owner_user = sync_spaces.owner_user "
                     "AND r.space = sync_spaces.space AND r.deleted = 0)", (owner, key_id))
        conn.execute("DELETE FROM sync_keys WHERE owner_user = ? AND key_id = ?", (owner, key_id))
    return bool(found)


def list_keys(owner: str) -> List[Dict[str, Any]]:
    """The owner's keys, each with ``records``: how many live records it
    sealed. A client asks before offering to replace a key, so a person who
    lost a generated passphrase is offered the way out only when it loses
    nothing; :func:`delete_key` still refuses on its own if that changed."""
    conn = db.get_conn()
    keys = [dict(r) for r in conn.execute(
        "SELECT * FROM sync_keys WHERE owner_user = ? ORDER BY created_at", (owner,)).fetchall()]
    sealed = {r["key_id"]: int(r["n"]) for r in conn.execute(
        "SELECT key_id, COUNT(*) AS n FROM sync_records WHERE owner_user = ? AND deleted = 0 "
        "AND key_id IS NOT NULL GROUP BY key_id", (owner,)).fetchall()}
    for key in keys:
        key["records"] = sealed.get(key["key_id"], 0)
    return keys


# ── records ──────────────────────────────────────────────────────────────────

def push(owner: str, space: str, records: Iterable[Dict[str, Any]], *, origin: str = "") -> List[Dict]:
    """Apply a batch of records, each against the version its sender based it
    on. Returns one result per record: ``ok`` with the new version and seq,
    or ``conflict`` with the record as it stands."""
    space_row = get_space(owner, space)
    if space_row is None:
        raise VaultError("this space is not set up for sync", status=404)
    items = list(records)
    if len(items) > MAX_PUSH:
        raise VaultError(f"at most {MAX_PUSH} records per push")
    if sum(len(str(r.get("payload") or "")) for r in items) > MAX_PUSH_BYTES:
        raise VaultError("the batch is too large; push fewer records at once", status=413)
    results: List[Dict[str, Any]] = []
    with db.transaction() as conn:
        for rec in items:
            kind = str(rec.get("kind") or "")
            record_id = str(rec.get("record_id") or "")
            if not kind or not record_id:
                raise VaultError("every record needs a kind and an id")
            deleted = bool(rec.get("deleted"))
            encrypted = bool(rec.get("encrypted"))
            payload = None if deleted else str(rec.get("payload") or "")
            if payload is not None and len(payload) > PAYLOAD_LIMIT:
                results.append({"kind": kind, "record_id": record_id, "status": "rejected",
                                "error": "the record is too large to sync"})
                continue
            if not deleted and encrypted != space_row["encrypted"]:
                raise VaultError("this space is " + ("encrypted" if space_row["encrypted"] else "not encrypted")
                                 + "; the record is sealed the other way", status=409)
            if not deleted and encrypted and rec.get("key_id") != space_row["key_id"]:
                raise VaultError("the record is sealed with another key than this space's", status=409)
            current = conn.execute(
                "SELECT * FROM sync_records WHERE owner_user = ? AND space = ? AND kind = ? AND record_id = ?",
                (owner, space, kind, record_id)).fetchone()
            base = int(rec.get("base_version") or 0)
            current_version = int(current["version"]) if current is not None else 0
            if base != current_version:
                results.append({"kind": kind, "record_id": record_id, "status": "conflict",
                                "current": _record(current) if current is not None else None})
                continue
            if current is None and deleted:
                # Deleting what the vault never had: nothing to tell anyone.
                results.append({"kind": kind, "record_id": record_id, "status": "ok", "version": 0, "seq": 0})
                continue
            version = current_version + 1
            seq = _next_seq(conn)
            conn.execute(
                db.upsert_sql("sync_records", ["owner_user", "space", "kind", "record_id", "version", "seq",
                                               "encrypted", "key_id", "origin", "modified_at", "size",
                                               "deleted", "payload", "updated_at"],
                              ["owner_user", "space", "kind", "record_id"]),
                (owner, space, kind, record_id, version, seq, 1 if encrypted else 0,
                 rec.get("key_id") if encrypted else None, origin or str(rec.get("origin") or ""),
                 rec.get("modified_at") or _now(), len(payload or ""), 1 if deleted else 0, payload, _now()))
            results.append({"kind": kind, "record_id": record_id, "status": "ok", "version": version, "seq": seq})
        conn.execute("UPDATE sync_spaces SET last_push_at = ? WHERE owner_user = ? AND space = ?",
                     (_now(), owner, space))
    return results


def changes(owner: str, space: str, *, since: int = 0, limit: int = MAX_PULL) -> Dict[str, Any]:
    limit = max(1, min(int(limit or MAX_PULL), MAX_PULL))
    rows = db.get_conn().execute(
        "SELECT * FROM sync_records WHERE owner_user = ? AND space = ? AND seq > ? ORDER BY seq LIMIT ?",
        (owner, space, int(since or 0), limit + 1)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    records = [_record(r) for r in rows]
    return {"records": records, "next": records[-1]["seq"] if records else int(since or 0), "more": more}


def list_records(owner: str, space: str, *, kind: Optional[str] = None,
                 include_deleted: bool = False) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM sync_records WHERE owner_user = ? AND space = ?"
    params: List[Any] = [owner, space]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if not include_deleted:
        sql += " AND deleted = 0"
    sql += " ORDER BY kind, modified_at DESC"
    return [_record(r, with_payload=False) for r in db.get_conn().execute(sql, params).fetchall()]


def get_record(owner: str, space: str, kind: str, record_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM sync_records WHERE owner_user = ? AND space = ? AND kind = ? AND record_id = ?",
        (owner, space, kind, record_id)).fetchone()
    return _record(row) if row is not None else None


__all__ = ["MAX_PULL", "MAX_PUSH", "VaultError", "changes", "counts", "delete_key", "delete_space", "ensure_space",
           "get_key", "get_record", "get_space", "list_keys", "list_records", "list_spaces", "push",
           "put_key", "spaces_holding"]
