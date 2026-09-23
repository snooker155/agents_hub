"""
One store for the run records of flows, loops and teams.

A flow run, a loop run and a team run are the same kind of thing: one execution
of an entity, keyed by its own id, carrying a status, timing, an error and a few
counters, read by id, listed newest or oldest first, stopped on request and
announced to the dashboard as ``<resource>.changed`` after every write. Each of
``flow/run_store.py``, ``loops/store.py`` and ``teams/store.py`` used to spell
that out by hand over its own table. :class:`EntityRunStore` is the one
implementation; the three modules are thin adapters over one instance each and
keep their public functions, signatures and return types.

**The tables stay separate.** ``flow_runs``, ``loop_runs`` and ``team_runs``
keep their columns and indexes, and nothing is migrated. Merging them into one
table would buy nothing a caller can see (every caller already asks one kind at
a time, and ``managers/runs/groups.py`` joins the kinds in Python), while it
would cost a data migration on both dialects, a wide sparse table and a risk to
live runs during the upgrade. The duplication was in the code, so the code is
what is unified.

A store describes its table once:

* ``columns``: the plain columns, the key first. Each maps to the record key of
  the same name.
* ``doc_column`` (optional): one JSON column. With ``doc_field=None`` it holds
  the **whole record** (``flow_runs.doc``): the record is a free-form dict,
  callers park keys of their own on it, and the columns are an indexed mirror
  refreshed on every write. With ``doc_field="position"`` it holds **one
  sub-document** that the record exposes under that key (``loop_runs.progress``
  is a loop run's ``position``).
* ``convert``: turns the stored dict into what callers get back (a plain dict
  for flows, ``LoopRun`` and ``TeamRun`` for loops and teams).

Every write goes through one merge: read the row inside ``db.transaction()``,
lay the changes over it, write every column (and the document) back with one
upsert. Write transactions are serialised (``BEGIN IMMEDIATE`` on SQLite, the
advisory write lock on Postgres), so two processes changing different fields of
one run cannot drop each other's change, and in whole-record mode a key the
table has no column for survives a later write of an indexed field.
"""
from __future__ import annotations

import logging
from typing import (
    Any, Callable, Dict, Generic, Iterable, List, Mapping, Optional, Sequence,
    Tuple, TypeVar,
)

from common import db

T = TypeVar("T")

Notify = Callable[..., None]


log = logging.getLogger(__name__)


def _default_notify(resource: str, **meta: Any) -> None:
    """Publish ``<resource>.changed``. Imported at call time so a test that
    patches ``common.session_broker.notify_change`` is honoured."""
    from common.session_broker import notify_change
    notify_change(resource, **meta)


def _cell(row: Any, column: str) -> Any:
    """A row's value for ``column``, or None when the row lacks the column."""
    try:
        return row[column]
    except (KeyError, IndexError):
        return None


class EntityRunStore(Generic[T]):
    """Run records of one entity kind over one table (see the module docstring).

    ``resource`` is the name published as ``<resource>.changed``;
    ``parent_key`` (``flow_id``, ``loop_id``, ``team_id``) rides along in that
    event with the run's own id. ``order_by`` is the default list order and
    ``live_statuses`` the statuses :meth:`active` returns. ``stopping_status``
    and ``stoppable_from`` enable :meth:`request_stop`; ``stopped_statuses`` is
    what :meth:`stop_requested` treats as "asked to stop".
    """

    def __init__(
        self,
        *,
        table: str,
        key: str,
        columns: Sequence[str],
        resource: str,
        parent_key: Optional[str] = None,
        doc_column: Optional[str] = None,
        doc_field: Optional[str] = None,
        convert: Optional[Callable[[Dict[str, Any]], T]] = None,
        order_by: str = "",
        live_statuses: Iterable[str] = ("running",),
        stopping_status: Optional[str] = None,
        stoppable_from: Iterable[str] = ("running",),
        stopped_statuses: Iterable[str] = ("stopping", "stopped"),
        notify: Optional[Notify] = None,
    ) -> None:
        cols = tuple(columns)
        if not cols or cols[0] != key:
            raise ValueError("columns must start with the key column")
        if doc_field is not None and doc_column is None:
            raise ValueError("doc_field needs a doc_column")
        self.table = table
        self.key = key
        self.columns: Tuple[str, ...] = cols
        self.resource = resource
        self.parent_key = parent_key
        self.doc_column = doc_column
        self.doc_field = doc_field
        self.convert: Callable[[Dict[str, Any]], T] = convert or (lambda rec: rec)  # type: ignore[assignment]
        self.order_by = order_by or key
        self.live_statuses: Tuple[str, ...] = tuple(live_statuses)
        self.stopping_status = stopping_status
        self.stoppable_from: Tuple[str, ...] = tuple(stoppable_from)
        self.stopped_statuses: Tuple[str, ...] = tuple(stopped_statuses)
        self._notify = notify

    # ── Row and record ──────────────────────────────────────────────────────

    @property
    def whole_record(self) -> bool:
        """True when the document column holds the whole record."""
        return self.doc_column is not None and self.doc_field is None

    def _write_columns(self) -> Tuple[str, ...]:
        return self.columns + ((self.doc_column,) if self.doc_column else ())

    def record_from_row(self, row: Any) -> Dict[str, Any]:
        """The stored dict of one row, before ``convert``."""
        plain = {c: _cell(row, c) for c in self.columns}
        if self.doc_column is None:
            return plain
        doc = db.loads(_cell(row, self.doc_column), None)
        if self.doc_field is None:
            # The document is the record; the columns are only a fallback for
            # a row written by something other than this store.
            return doc if isinstance(doc, dict) else plain
        plain[self.doc_field] = doc if isinstance(doc, dict) else {}
        return plain

    def _values(self, rec: Mapping[str, Any]) -> List[Any]:
        values = [rec.get(c) for c in self.columns]
        if self.doc_column is None:
            return values
        if self.doc_field is None:
            return values + [db.dumps(dict(rec))]
        return values + [db.dumps(rec.get(self.doc_field) or {})]

    def _write(self, conn: Any, rec: Mapping[str, Any]) -> None:
        """Persist one whole record, refreshing every column from it."""
        conn.execute(db.upsert_sql(self.table, self._write_columns(), (self.key,)),
                     self._values(rec))

    def _read(self, conn: Any, run_id: str) -> Optional[Dict[str, Any]]:
        row = conn.execute(f"SELECT * FROM {self.table} WHERE {self.key} = ?",
                           (str(run_id),)).fetchone()
        return self.record_from_row(row) if row is not None else None

    # ── Notification ───────────────────────────────────────────────────────

    def notify(self, **meta: Any) -> None:
        """Publish ``<resource>.changed`` with ``meta``. Best-effort: a store
        write that has committed never fails because the dashboard could not
        be told."""
        try:
            (self._notify or _default_notify)(self.resource, **meta)
        except Exception:  # noqa: BLE001 - notification is best-effort, the write has committed
            log.debug("%s.changed not published", self.resource, exc_info=True)

    def _notify_record(self, rec: Mapping[str, Any]) -> None:
        meta = {self.key: rec.get(self.key)}
        if self.parent_key:
            meta[self.parent_key] = rec.get(self.parent_key)
        self.notify(**meta)

    # ── Writes ─────────────────────────────────────────────────────────────

    def upsert(self, record: Mapping[str, Any], *, merge: bool = True,
               notify: bool = True) -> Optional[Dict[str, Any]]:
        """Insert or update a record by its key and return what was stored.

        With ``merge`` an existing record is merged into, so a field this
        caller did not mention survives. ``merge=False`` writes the record as
        given (every column, and the document) without reading first; that is
        a full save. A record without a key is ignored and returns None.
        """
        run_id = str(record.get(self.key) or "")
        if not run_id:
            return None
        with db.transaction() as conn:
            existing = self._read(conn, run_id) if merge else None
            rec = {**existing, **record} if existing else dict(record)
            rec[self.key] = record.get(self.key)
            self._write(conn, rec)
        if notify:
            self._notify_record(rec)
        return rec

    def update(self, run_id: str, updates: Mapping[str, Any], *,
               notify: bool = True,
               only_if_status: Optional[Iterable[str]] = None) -> Optional[Dict[str, Any]]:
        """Merge ``updates`` into a stored record and return the merged record.

        None when there is no such record, or when ``only_if_status`` is given
        and the record's status is not in it (nothing is written then). In
        column-only mode a key that is neither a column nor the document field
        is not stored, though it appears in the returned dict.
        """
        allowed = tuple(only_if_status) if only_if_status is not None else None
        with db.transaction() as conn:
            existing = self._read(conn, run_id)
            if existing is None:
                return None
            if allowed is not None and existing.get("status") not in allowed:
                return None
            rec = {**existing, **updates}
            rec[self.key] = str(run_id)
            self._write(conn, rec)
        if notify:
            self._notify_record(rec)
        return rec

    def set_status(self, run_id: str, status: str, *,
                   from_statuses: Optional[Iterable[str]] = None,
                   **fields: Any) -> bool:
        """Move a run to ``status`` (with any other ``fields``), optionally only
        from one of ``from_statuses``. True when the run was changed."""
        return self.update(run_id, {**fields, "status": status},
                           only_if_status=from_statuses) is not None

    # ── Reads ──────────────────────────────────────────────────────────────

    def read(self, run_id: str) -> Optional[Dict[str, Any]]:
        """The stored dict of one run (before ``convert``), or None."""
        return self._read(db.get_conn(), run_id)

    def get(self, run_id: str) -> Optional[T]:
        """One run, converted, or None."""
        rec = self.read(run_id)
        return self.convert(rec) if rec is not None else None

    def list(self, where: Optional[Mapping[str, Any]] = None, *,
             statuses: Optional[Iterable[str]] = None,
             order_by: Optional[str] = None,
             limit: Optional[int] = None) -> List[T]:
        """Runs matching ``where`` (column equals value) and, when given, one
        of ``statuses``. ``order_by`` is SQL from the adapter,
        never from a request. ``limit=None`` is no limit."""
        clauses: List[str] = []
        params: List[Any] = []
        for col, value in (where or {}).items():
            if col not in self.columns:
                raise ValueError(f"{self.table} has no column {col!r}")
            clauses.append(f"{col} = ?")
            params.append(value)
        if statuses is not None:
            wanted = tuple(statuses)
            if not wanted:
                return []
            clauses.append(f"status IN ({', '.join('?' * len(wanted))})")
            params.extend(wanted)
        sql = f"SELECT * FROM {self.table}"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY {order_by or self.order_by}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = db.get_conn().execute(sql, params).fetchall()
        return [self.convert(self.record_from_row(r)) for r in rows]

    def active(self, where: Optional[Mapping[str, Any]] = None, *,
               order_by: Optional[str] = None) -> List[T]:
        """The runs in a live status (``live_statuses``) matching ``where``."""
        return self.list(where, statuses=self.live_statuses, order_by=order_by)

    # ── Stop requests ──────────────────────────────────────────────────────

    def request_stop(self, run_id: str) -> bool:
        """Mark a live run as stopping. True only when a run in one of
        ``stoppable_from`` was moved; a finished or unknown run is left alone.
        The runner polls :meth:`stop_requested` at its next safe point."""
        if not self.stopping_status:
            raise NotImplementedError(f"{self.table} has no stopping status")
        return self.set_status(run_id, self.stopping_status,
                               from_statuses=self.stoppable_from)

    def stop_requested(self, run_id: str) -> bool:
        """True when the run was asked to stop (or already has)."""
        row = db.get_conn().execute(
            f"SELECT status FROM {self.table} WHERE {self.key} = ?", (str(run_id),)
        ).fetchone()
        return bool(row and row["status"] in self.stopped_statuses)


__all__ = ["EntityRunStore"]
