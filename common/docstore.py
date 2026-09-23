"""
Named collections of JSON documents in the database.

Most of the state that used to live in one JSON file per store under a
``FileLock`` (``plans.json``, ``notifications.json``, ``workspaces.json``,
``shared_memory.json``, the per-pool ``episodes/`` and ``graphs/`` files, ...)
has the same shape: a set of documents addressed by a string key, read whole,
mutated and written back. :class:`DocStore` is that shape over the
``documents`` table (``common/migrations/0006_documents.sql``), so every
process and every host sees one copy, and a read-modify-write is atomic under
``db.transaction()`` instead of a lock file that only works on one host.

Usage::

    store = DocStore("plans", legacy_file=PLANS_FILE, legacy_key=lambda d: d["id"])

    with store.transaction():             # atomic read-modify-write
        docs = store.all()                # {key: doc}, insertion order
        ...
        store.put(key, doc)
        store.delete(other_key)

    store.get(key)                        # one document, or None
    store.replace_all({key: doc, ...})    # the whole collection at once

The first access to a store whose legacy JSON file still exists imports the
file once, inside a transaction, and renames it ``<name>.migrated``, the same
way ``flow_runs.json`` moved into its table. A store that already holds rows
never imports (the file is left alone and reported), so a half-upgraded
environment cannot double up. ``legacy_key`` says how to key each record of
a list-shaped file; a dict-shaped file is keyed by its own keys. Anything
richer (a directory of files, one document per pool) is imported by the
store's own module through :meth:`import_legacy`.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from common import db

log = logging.getLogger(__name__)

TABLE = "documents"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class DocStore:
    def __init__(self, name: str, *, legacy_file: Optional[Path] = None,
                 legacy_key: Optional[Callable[[Any], Optional[str]]] = None) -> None:
        self.name = str(name)
        self.legacy_file = Path(legacy_file) if legacy_file else None
        self.legacy_key = legacy_key
        # Checked once per database opening (db._generation: tests swap
        # databases, and a process that re-opens one starts over).
        self._imported_for: Optional[str] = None

    # ── transactions ────────────────────────────────────────────────────────

    @contextmanager
    def transaction(self) -> Iterator["DocStore"]:
        """Atomic read-modify-write over this store (re-entrant, like
        ``db.transaction()``)."""
        with db.transaction():
            self._ensure_imported()
            yield self

    def _conn(self) -> Any:
        conn = db.get_conn()
        self._ensure_imported()
        return conn

    # ── reads ───────────────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[Any]:
        row = self._conn().execute(
            f"SELECT doc FROM {TABLE} WHERE store = ? AND key = ?", (self.name, str(key))
        ).fetchone()
        return db.loads(row["doc"]) if row is not None else None

    def all(self) -> Dict[str, Any]:
        """Every document, keyed, in insertion order."""
        rows = self._conn().execute(
            f"SELECT key, doc FROM {TABLE} WHERE store = ? ORDER BY seq", (self.name,)
        ).fetchall()
        out: Dict[str, Any] = {}
        for r in rows:
            out[str(r["key"])] = db.loads(r["doc"])
        return out

    def values(self) -> List[Any]:
        return list(self.all().values())

    def keys(self) -> List[str]:
        rows = self._conn().execute(
            f"SELECT key FROM {TABLE} WHERE store = ? ORDER BY seq", (self.name,)).fetchall()
        return [str(r["key"]) for r in rows]

    def count(self) -> int:
        row = self._conn().execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE store = ?", (self.name,)).fetchone()
        return int(row[0] or 0)

    def signature(self) -> str:
        """A cheap change marker for the whole collection (row count and the
        latest ``updated_at``): what callers that used to hash a file's mtime
        and size use instead (agents/agent_cache.py)."""
        row = self._conn().execute(
            f"SELECT COUNT(*), MAX(updated_at) FROM {TABLE} WHERE store = ?", (self.name,)
        ).fetchone()
        return f"{int(row[0] or 0)}:{row[1] or ''}"

    def exists(self, key: str) -> bool:
        return self._conn().execute(
            f"SELECT 1 FROM {TABLE} WHERE store = ? AND key = ?", (self.name, str(key))
        ).fetchone() is not None

    # ── writes ──────────────────────────────────────────────────────────────

    def put(self, key: str, doc: Any) -> None:
        """Insert or replace one document (keeps its place in insertion order)."""
        with db.transaction() as conn:
            self._ensure_imported()
            self._put(conn, str(key), doc)

    def _put(self, conn: Any, key: str, doc: Any) -> None:
        now = _now()
        text = dumps(doc)
        cur = conn.execute(
            f"UPDATE {TABLE} SET doc = ?, updated_at = ? WHERE store = ? AND key = ?",
            (text, now, self.name, key))
        if cur.rowcount and cur.rowcount > 0:
            return
        conn.execute(
            f"INSERT INTO {TABLE} (store, key, seq, doc, created_at, updated_at) "
            f"VALUES (?, ?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM {TABLE} WHERE store = ?), "
            "?, ?, ?)",
            (self.name, key, self.name, text, now, now))

    def delete(self, key: str) -> bool:
        with db.transaction() as conn:
            self._ensure_imported()
            cur = conn.execute(
                f"DELETE FROM {TABLE} WHERE store = ? AND key = ?", (self.name, str(key)))
            return bool(cur.rowcount and cur.rowcount > 0)

    def replace_all(self, docs: Dict[str, Any]) -> None:
        """Make the collection exactly ``docs``, in that order. Documents whose
        key already exists keep their place and creation time; new ones are
        appended in the order given; the rest are removed.

        Only the difference is written: a store of two hundred documents
        that gains one costs one read and one insert, not two hundred
        rewrites (this is the write path of every "load, mutate, save whole
        list" store, so it runs on every append)."""
        with db.transaction() as conn:
            self._ensure_imported()
            wanted = {str(k): dumps(v) for k, v in docs.items()}
            rows = conn.execute(
                f"SELECT key, doc FROM {TABLE} WHERE store = ?", (self.name,)).fetchall()
            existing = {str(r["key"]): r["doc"] for r in rows}
            gone = [k for k in existing if k not in wanted]
            if gone:
                conn.executemany(f"DELETE FROM {TABLE} WHERE store = ? AND key = ?",
                                 [(self.name, k) for k in gone])
            now = _now()
            changed = [(text, now, self.name, k) for k, text in wanted.items()
                       if k in existing and existing[k] != text]
            if changed:
                conn.executemany(
                    f"UPDATE {TABLE} SET doc = ?, updated_at = ? WHERE store = ? AND key = ?",
                    changed)
            new = [k for k in wanted if k not in existing]
            if new:
                row = conn.execute(
                    f"SELECT COALESCE(MAX(seq), 0) FROM {TABLE} WHERE store = ?",
                    (self.name,)).fetchone()
                seq = int(row[0] or 0)
                conn.executemany(
                    f"INSERT INTO {TABLE} (store, key, seq, doc, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [(self.name, k, seq + i + 1, wanted[k], now, now)
                     for i, k in enumerate(new)])

    def clear(self) -> int:
        with db.transaction() as conn:
            self._ensure_imported()
            cur = conn.execute(f"DELETE FROM {TABLE} WHERE store = ?", (self.name,))
            return int(cur.rowcount or 0)

    # ── legacy import ───────────────────────────────────────────────────────

    def _ensure_imported(self) -> None:
        if self.legacy_file is None:
            return
        db.get_conn()  # the startup sequence must have run for the marker to mean anything
        marker = f"{db._generation}:{db.dialect()}:{db.DB_FILE}:{db.database_url()}"
        if self._imported_for == marker:
            return
        self._imported_for = marker
        if not self.legacy_file.exists():
            return
        try:
            text = self.legacy_file.read_text(encoding="utf-8")
            data = json.loads(text) if text.strip() else None
        except (OSError, ValueError) as exc:
            log.warning("docstore: %s unreadable, left in place (%s)", self.legacy_file.name, exc)
            return
        docs = self._legacy_docs(data)
        self.import_legacy(docs, self.legacy_file)

    def _legacy_docs(self, data: Any) -> Dict[str, Any]:
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items()}
        if isinstance(data, list):
            out: Dict[str, Any] = {}
            for i, rec in enumerate(data):
                key = None
                if self.legacy_key is not None:
                    try:
                        key = self.legacy_key(rec)
                    except Exception:  # noqa: BLE001 - a caller-supplied key function must not break the import
                        log.debug("docstore: legacy_key failed for a record", exc_info=True)
                        key = None
                if key is None:
                    key = f"{i}"
                out[str(key)] = rec
            return out
        return {}

    def import_legacy(self, docs: Dict[str, Any], source: Optional[Path] = None) -> int:
        """Load ``docs`` into an empty store and rename ``source`` to
        ``.migrated``. A store that already has rows imports nothing (the
        source is left where it is). Returns the number imported."""
        with db.transaction() as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE store = ?",
                               (self.name,)).fetchone()
            if int(row[0] or 0):
                if source is not None and source.exists():
                    log.info("docstore: %s: store '%s' already has rows, file left in place",
                             source.name, self.name)
                return 0
            for key, doc in docs.items():
                self._put(conn, str(key), doc)
        if source is not None:
            try:
                if source.exists():
                    source.rename(source.with_name(source.name + ".migrated"))
            except OSError:
                pass  # rows are in; the rename is hygiene only
            if docs:
                log.info("docstore: imported %d record(s) from %s into '%s'",
                          len(docs), source.name, self.name)
        return len(docs)
