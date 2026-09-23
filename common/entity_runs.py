"""
One table, ``entity_runs``, for the runs of flows, loops, teams and scenarios.

A flow run, a loop run, a team run and a scenario run are the same kind of
thing: one execution of an entity, with a status, a process (pid, host or
container), a heartbeat, a checkpoint to resume from, a log file, a cost, an
error and a few timestamps, plus a handful of fields that are its own (a
team's rounds and result, a scenario's scores, a loop's position). Until
stage 0 of the September 2026 plan each kind had its own table with its own
spelling of the common part, and only agent runs had a heartbeat or a place
in the launch queue. Now every kind is a process with a lease, a heartbeat
and a checkpoint, and the things that read runs across kinds (the watchdog,
the run groups page, a recursive stop, the deployment map) read one table.

**The record.** A run is a free-form dict. The common fields are columns
(``COLUMNS``), indexed and queryable; the whole record is also kept as JSON
in ``doc``, so a key the table has no column for (a team's ``conversation_id``,
a scenario's ``final_state``) is stored and read back unchanged. The
checkpoint has a column of its own, ``checkpoint``, because it is large and
is written on its own schedule. On read the columns win over the document:
a heartbeat stamped with one UPDATE is visible even though the document was
not rewritten.

**Names.** Each kind kept its own key names (``flow_run_id``/``flow_id``,
``team_run_id``/``team_id``...). A record carries both those and the generic
``run_id``/``entity_id``, so an adapter's callers see what they always saw
and a kind-agnostic reader does not need a translation table
(:data:`KIND_FIELDS`).

**Writes.** Every write goes through one merge: read the row inside
``db.transaction()``, lay the changes over it, write every column and the
document back with one upsert. Write transactions are serialised (``BEGIN
IMMEDIATE`` on SQLite, the advisory write lock on Postgres), so two
processes changing different fields of one run cannot drop each other's
change. A status change is checked against the transition table in
common/run_status.py and refused when the table forbids it.

**Two surfaces.** :class:`EntityRunStore` is the per-kind view the adapters
(flow/run_store.py, loops/store.py, teams/store.py, playground/store.py) are
built on; the module-level functions (:func:`get`, :func:`list_runs`,
:func:`request_stop`, :func:`touch_heartbeat`, :func:`children`...) are the
kind-agnostic surface the watchdog, the worker and the run groups use.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import (
    Any, Callable, Dict, Generic, Iterable, List, Mapping, Optional, Tuple, TypeVar,
)

from common import db
from common import run_status
from common.run_status import RunStatus

T = TypeVar("T")

Notify = Callable[..., None]

log = logging.getLogger(__name__)

TABLE = "entity_runs"

#: The kinds a record may have.
KINDS: Tuple[str, ...] = ("flow", "loop", "team", "scenario")

#: Per kind: the names its adapter's callers use for the run id and the
#: entity id. A record carries these beside ``run_id`` and ``entity_id``.
KIND_FIELDS: Dict[str, Tuple[str, str]] = {
    "flow": ("flow_run_id", "flow_id"),
    "loop": ("loop_run_id", "loop_id"),
    "team": ("team_run_id", "team_id"),
    "scenario": ("sim_run_id", "scenario_id"),
}

#: The ``<resource>.changed`` name each kind publishes. Unchanged from the
#: per-table days, so nothing subscribed to them has to move.
RESOURCES: Dict[str, str] = {
    "flow": "flow_runs",
    "loop": "loop_runs",
    "team": "team_runs",
    "scenario": "sim_runs",
}

#: The plain columns, the key first. ``checkpoint`` and ``doc`` are JSON
#: columns handled apart.
COLUMNS: Tuple[str, ...] = (
    "run_id", "kind", "entity_id", "workspace", "task_id", "session_id",
    "parent_run_id", "title", "status", "pid", "host", "container_name",
    "execution_mode", "heartbeat_at", "resume_attempts", "log_file",
    "total_cost", "stop_reason", "error", "exit_code", "created_at",
    "started_at", "finished_at",
)

_WRITE_COLUMNS: Tuple[str, ...] = COLUMNS + ("checkpoint", "doc")

#: Newest first, a run with no start yet counting as newest.
DEFAULT_ORDER = "COALESCE(started_at, created_at, '') DESC, run_id"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def kind_of(kind: str) -> str:
    if kind not in KIND_FIELDS:
        raise ValueError(f"unknown entity run kind {kind!r} (expected one of {', '.join(KINDS)})")
    return kind


# ── Row and record ───────────────────────────────────────────────────────────

def record_from_row(row: Any) -> Dict[str, Any]:
    """The record of one row: the document, the columns laid over it, the
    checkpoint when there is one, and the kind's own names for its ids."""
    doc = db.loads(_cell(row, "doc"), None)
    rec: Dict[str, Any] = dict(doc) if isinstance(doc, dict) else {}
    rec.pop("checkpoint", None)
    for col in COLUMNS:
        rec[col] = _cell(row, col)
    checkpoint = db.loads(_cell(row, "checkpoint"), None)
    if isinstance(checkpoint, dict) and checkpoint:
        rec["checkpoint"] = checkpoint
    kind = str(rec.get("kind") or "")
    fields = KIND_FIELDS.get(kind)
    if fields:
        rec[fields[0]] = rec["run_id"]
        rec[fields[1]] = rec["entity_id"]
    return rec


def _normalise(rec: Mapping[str, Any], kind: str) -> Dict[str, Any]:
    """A record with the generic keys filled from the kind's own names (and
    the other way round), ready to be written."""
    out = dict(rec)
    id_field, entity_field = KIND_FIELDS[kind]
    run_id = out.get("run_id") or out.get(id_field)
    entity_id = out.get("entity_id") if out.get("entity_id") is not None else out.get(entity_field)
    out["run_id"] = str(run_id) if run_id else None
    out[id_field] = out["run_id"]
    out["entity_id"] = entity_id
    out[entity_field] = entity_id
    out["kind"] = kind
    return out


def _values(rec: Mapping[str, Any]) -> List[Any]:
    doc = {k: v for k, v in rec.items() if k != "checkpoint"}
    checkpoint = rec.get("checkpoint")
    values = [rec.get(c) for c in COLUMNS]
    values.append(db.dumps(checkpoint) if checkpoint else None)
    values.append(db.dumps(doc))
    return values


def _write(conn: Any, rec: Mapping[str, Any]) -> None:
    conn.execute(db.upsert_sql(TABLE, _WRITE_COLUMNS, ("run_id",)), _values(rec))


def _read(conn: Any, run_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(f"SELECT * FROM {TABLE} WHERE run_id = ?", (str(run_id),)).fetchone()
    return record_from_row(row) if row is not None else None


def _check_status(existing: Optional[Mapping[str, Any]], rec: Mapping[str, Any],
                  run_id: str) -> None:
    """Refuse a status move the transition table forbids."""
    if existing is None or "status" not in rec:
        return
    run_status.check_transition(existing.get("status"), rec.get("status"), run_id=run_id)


# ── Notification ─────────────────────────────────────────────────────────────

def notify(kind: str, notify_fn: Optional[Notify] = None, **meta: Any) -> None:
    """Publish ``<resource>.changed`` for ``kind``. Best-effort: a store write
    that has committed never fails because the dashboard could not be told."""
    try:
        (notify_fn or _default_notify)(RESOURCES[kind_of(kind)], **meta)
    except Exception:  # noqa: BLE001 - notification is best-effort, the write has committed
        log.debug("%s.changed not published", RESOURCES.get(kind), exc_info=True)


def _notify_record(rec: Mapping[str, Any], notify_fn: Optional[Notify] = None) -> None:
    kind = str(rec.get("kind") or "")
    if kind not in KIND_FIELDS:
        return
    id_field, entity_field = KIND_FIELDS[kind]
    notify(kind, notify_fn, **{id_field: rec.get("run_id"), entity_field: rec.get("entity_id"),
                               "status": rec.get("status")})


# ── Generic writes ───────────────────────────────────────────────────────────

def upsert(record: Mapping[str, Any], *, kind: Optional[str] = None, merge: bool = True,
           notify_fn: Optional[Notify] = None, notify: bool = True) -> Optional[Dict[str, Any]]:
    """Insert or update a record by its run id and return what was stored.

    With ``merge`` an existing record is merged into, so a field this caller
    did not mention survives. ``merge=False`` writes the record as given
    (every column, and the document) as a full save. ``kind`` may be omitted
    when the record carries it. A record without a run id is ignored and
    returns None.
    """
    kind = kind_of(str(kind or record.get("kind") or ""))
    rec = _normalise(record, kind)
    run_id = rec.get("run_id")
    if not run_id:
        return None
    with db.transaction() as conn:
        existing = _read(conn, run_id)
        _check_status(existing, rec, run_id)
        if merge and existing:
            merged = {**existing, **rec}
        else:
            merged = rec
        merged.setdefault("created_at", utc_now_iso())
        _write(conn, merged)
    if notify:
        _notify_record(merged, notify_fn)
    return merged


def update(run_id: str, updates: Mapping[str, Any], *, notify: bool = True,
           notify_fn: Optional[Notify] = None,
           only_if_status: Optional[Iterable[str]] = None) -> Optional[Dict[str, Any]]:
    """Merge ``updates`` into a stored record and return the merged record.

    None when there is no such record, or when ``only_if_status`` is given
    and the record's status is not in it (nothing is written then).
    """
    allowed = tuple(only_if_status) if only_if_status is not None else None
    with db.transaction() as conn:
        existing = _read(conn, run_id)
        if existing is None:
            return None
        if allowed is not None and existing.get("status") not in allowed:
            return None
        _check_status(existing, updates, str(run_id))
        rec = _normalise({**existing, **updates}, str(existing["kind"]))
        rec["run_id"] = str(run_id)
        _write(conn, rec)
    if notify:
        _notify_record(rec, notify_fn)
    return rec


def set_status(run_id: str, status: str, *, from_statuses: Optional[Iterable[str]] = None,
               **fields: Any) -> bool:
    """Move a run to ``status`` (with any other ``fields``), optionally only
    from one of ``from_statuses``. True when the run was changed."""
    return update(run_id, {**fields, "status": run_status.as_status(status)},
                  only_if_status=from_statuses) is not None


def mark_running(run_id: str, *, pid: Optional[int] = None, host: Optional[str] = None,
                 container_name: Optional[str] = None,
                 execution_mode: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The launcher recorded a process for the run: ``running``, with where
    it lives and its first heartbeat, so it is judged by the beat from now."""
    now = utc_now_iso()
    fields: Dict[str, Any] = {
        "status": RunStatus.running.value, "started_at": now, "heartbeat_at": now,
        "finished_at": None, "exit_code": None, "error": None,
    }
    if pid is not None:
        fields["pid"] = int(pid)
    if host is not None:
        fields["host"] = host
    if container_name is not None:
        fields["container_name"] = container_name
    if execution_mode is not None:
        fields["execution_mode"] = execution_mode
    return update(run_id, fields)


def close(run_id: str, *, status: str, exit_code: Optional[int] = None,
          error: Optional[str] = None, **fields: Any) -> Optional[Dict[str, Any]]:
    """Finish a run: a terminal status, the exit code, the error and the time."""
    return update(run_id, {
        **fields, "status": run_status.as_status(status), "exit_code": exit_code,
        "error": error, "finished_at": utc_now_iso(),
    })


def request_stop(run_id: str) -> bool:
    """Mark a live run as stopping. True only when a run in a stoppable
    status was moved; a finished or unknown run is left alone. The process
    reads the status back on its next heartbeat (or at its next safe point)
    and ends itself."""
    return set_status(run_id, RunStatus.stopping.value,
                      from_statuses=run_status.STOPPABLE_STATUSES)


def stop_requested(run_id: str) -> bool:
    """True when the run was asked to stop (or already has)."""
    row = db.get_conn().execute(
        f"SELECT status FROM {TABLE} WHERE run_id = ?", (str(run_id),)).fetchone()
    return bool(row and row["status"] in (RunStatus.stopping.value, RunStatus.stopped.value))


def touch_heartbeat(run_id: str, when: Optional[str] = None) -> Optional[str]:
    """Stamp ``heartbeat_at`` and return the run's current status, or None
    when there is no such run. One UPDATE, no merge, no notification: this
    runs every few seconds for every live run."""
    with db.transaction() as conn:
        conn.execute(f"UPDATE {TABLE} SET heartbeat_at = ? WHERE run_id = ?",
                     (when or utc_now_iso(), str(run_id)))
        row = conn.execute(f"SELECT status FROM {TABLE} WHERE run_id = ?",
                           (str(run_id),)).fetchone()
    return str(row["status"]) if row is not None and row["status"] is not None else None


def save_checkpoint(run_id: str, checkpoint: Mapping[str, Any], *,
                    heartbeat: bool = True) -> None:
    """Write what a resume starts from. One UPDATE of the checkpoint column
    (and the heartbeat, since writing a checkpoint is a sign of life); the
    document is not rewritten."""
    with db.transaction() as conn:
        if heartbeat:
            conn.execute(f"UPDATE {TABLE} SET checkpoint = ?, heartbeat_at = ? WHERE run_id = ?",
                         (db.dumps(dict(checkpoint)), utc_now_iso(), str(run_id)))
        else:
            conn.execute(f"UPDATE {TABLE} SET checkpoint = ? WHERE run_id = ?",
                         (db.dumps(dict(checkpoint)), str(run_id)))


def load_checkpoint(run_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        f"SELECT checkpoint FROM {TABLE} WHERE run_id = ?", (str(run_id),)).fetchone()
    if row is None:
        return None
    data = db.loads(row["checkpoint"], None)
    return data if isinstance(data, dict) else None


def clear_checkpoint(run_id: str) -> None:
    with db.transaction() as conn:
        conn.execute(f"UPDATE {TABLE} SET checkpoint = NULL WHERE run_id = ?", (str(run_id),))


def delete(run_id: str) -> bool:
    with db.transaction() as conn:
        cur = conn.execute(f"DELETE FROM {TABLE} WHERE run_id = ?", (str(run_id),))
        return int(getattr(cur, "rowcount", 0) or 0) > 0


def delete_where(*, kind: str, entity_id: str) -> List[str]:
    """Delete every run of one entity; returns the ids removed."""
    with db.transaction() as conn:
        rows = conn.execute(
            f"SELECT run_id FROM {TABLE} WHERE kind = ? AND entity_id = ?",
            (kind_of(kind), str(entity_id))).fetchall()
        ids = [str(r["run_id"]) for r in rows]
        conn.execute(f"DELETE FROM {TABLE} WHERE kind = ? AND entity_id = ?",
                     (kind, str(entity_id)))
    return ids


# ── Generic reads ────────────────────────────────────────────────────────────

def get(run_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """One run's record, whatever its kind, or None."""
    if not run_id:
        return None
    return _read(db.get_conn(), str(run_id))


def _alias_column(column: str) -> str:
    """A kind's own id names filter the generic columns they stand for."""
    for id_field, entity_field in KIND_FIELDS.values():
        if column == id_field:
            return "run_id"
        if column == entity_field:
            return "entity_id"
    return column


def list_runs(*, kind: Optional[str] = None, kinds: Optional[Iterable[str]] = None,
              statuses: Optional[Iterable[str]] = None, active: bool = False,
              workspace: Optional[str] = None, entity_id: Optional[str] = None,
              task_id: Optional[str] = None, parent_run_id: Optional[str] = None,
              where: Optional[Mapping[str, Any]] = None,
              order_by: Optional[str] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Runs matching every given filter. ``active`` selects the statuses in
    :data:`run_status.ACTIVE_STATUSES`. ``where`` is extra ``column = value``
    pairs (a kind's own id names are accepted). ``order_by`` is SQL from a
    caller in this codebase, never from a request. ``limit=None`` is no limit."""
    clauses: List[str] = []
    params: List[Any] = []
    wanted_kinds = [kind_of(kind)] if kind else [kind_of(k) for k in (kinds or ())]
    if wanted_kinds:
        clauses.append(f"kind IN ({', '.join('?' * len(wanted_kinds))})")
        params.extend(wanted_kinds)
    if active:
        statuses = run_status.ACTIVE_STATUSES
    if statuses is not None:
        wanted = tuple(statuses)
        if not wanted:
            return []
        clauses.append(f"status IN ({', '.join('?' * len(wanted))})")
        params.extend(wanted)
    for col, value in (("workspace", workspace), ("entity_id", entity_id),
                       ("task_id", task_id), ("parent_run_id", parent_run_id)):
        if value is not None:
            clauses.append(f"{col} = ?")
            params.append(str(value))
    for col, value in (where or {}).items():
        column = _alias_column(str(col))
        if column not in COLUMNS:
            raise ValueError(f"{TABLE} has no column {col!r}")
        clauses.append(f"{column} = ?")
        params.append(value)
    sql = f"SELECT * FROM {TABLE}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += f" ORDER BY {order_by or DEFAULT_ORDER}"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    rows = db.get_conn().execute(sql, params).fetchall()
    return [record_from_row(r) for r in rows]


def active_runs(kind: Optional[str] = None, **filters: Any) -> List[Dict[str, Any]]:
    """The runs still going (pending, running or stopping)."""
    return list_runs(kind=kind, active=True, **filters)


def children(run_id: str) -> List[Dict[str, Any]]:
    """The entity runs nested inside this one (a flow node that is a team,
    a loop's iterations once they are flows in this table...)."""
    return list_runs(parent_run_id=str(run_id), order_by="COALESCE(created_at, ''), run_id")


def leaf_children(run_id: str) -> List[str]:
    """Run ids of the agent runs (``runs`` table) this run owns directly:
    node runs, member turns, role decisions."""
    rows = db.get_conn().execute(
        "SELECT run_id FROM runs WHERE parent_run_id = ? "
        "ORDER BY COALESCE(created_at, started_at, ''), run_id", (str(run_id),)).fetchall()
    return [str(r["run_id"]) for r in rows]


def counts_by_kind(statuses: Optional[Iterable[str]] = None) -> Dict[str, int]:
    """How many runs of each kind, optionally only in ``statuses``; for the
    health snapshot and the deployment map."""
    sql = f"SELECT kind, COUNT(*) AS n FROM {TABLE}"
    params: List[Any] = []
    if statuses is not None:
        wanted = tuple(statuses)
        if not wanted:
            return {}
        sql += f" WHERE status IN ({', '.join('?' * len(wanted))})"
        params.extend(wanted)
    sql += " GROUP BY kind"
    rows = db.get_conn().execute(sql, params).fetchall()
    return {str(r["kind"]): int(r["n"]) for r in rows}


# ── The per-kind view ────────────────────────────────────────────────────────

class EntityRunStore(Generic[T]):
    """Run records of one kind over the shared table.

    ``convert`` turns a stored record into what callers get back (a plain
    dict for flows, ``LoopRun``, ``TeamRun`` and ``SimRun`` for the others).
    ``order_by`` is the default list order and ``live_statuses`` the statuses
    :meth:`active` returns. ``notify`` replaces the session-broker publish,
    for tests.
    """

    def __init__(
        self,
        kind: str,
        *,
        convert: Optional[Callable[[Dict[str, Any]], T]] = None,
        order_by: str = "",
        live_statuses: Iterable[str] = run_status.ACTIVE_STATUSES,
        notify: Optional[Notify] = None,
    ) -> None:
        self.kind = kind_of(kind)
        self.id_field, self.entity_field = KIND_FIELDS[self.kind]
        self.resource = RESOURCES[self.kind]
        self.convert: Callable[[Dict[str, Any]], T] = convert or (lambda rec: rec)  # type: ignore[assignment]
        self.order_by = order_by or DEFAULT_ORDER
        self.live_statuses: Tuple[str, ...] = tuple(live_statuses)
        self._notify = notify

    # Kept for callers that introspect the store.
    table = TABLE
    key = "run_id"
    columns = COLUMNS

    # ── Notification ───────────────────────────────────────────────────────

    def notify(self, **meta: Any) -> None:
        """Publish this kind's ``<resource>.changed`` with ``meta``."""
        notify(self.kind, self._notify, **meta)

    # ── Writes ─────────────────────────────────────────────────────────────

    def upsert(self, record: Mapping[str, Any], *, merge: bool = True,
               notify: bool = True) -> Optional[Dict[str, Any]]:
        """Insert or update a record by its key and return what was stored
        (see the module-level :func:`upsert`)."""
        return upsert(record, kind=self.kind, merge=merge, notify=notify, notify_fn=self._notify)

    def update(self, run_id: str, updates: Mapping[str, Any], *, notify: bool = True,
               only_if_status: Optional[Iterable[str]] = None) -> Optional[Dict[str, Any]]:
        """Merge ``updates`` into a stored record of this kind and return the
        merged record; None when there is no such record of this kind."""
        rec = self.read(run_id)
        if rec is None:
            return None
        return update(run_id, updates, notify=notify, notify_fn=self._notify,
                      only_if_status=only_if_status)

    def set_status(self, run_id: str, status: str, *,
                   from_statuses: Optional[Iterable[str]] = None, **fields: Any) -> bool:
        return self.update(run_id, {**fields, "status": run_status.as_status(status)},
                           only_if_status=from_statuses) is not None

    def delete(self, run_id: str) -> bool:
        return self.read(run_id) is not None and delete(run_id)

    def delete_for_entity(self, entity_id: str) -> List[str]:
        return delete_where(kind=self.kind, entity_id=entity_id)

    # ── Reads ──────────────────────────────────────────────────────────────

    def read(self, run_id: str) -> Optional[Dict[str, Any]]:
        """The stored record of one run of this kind (before ``convert``)."""
        rec = get(run_id)
        return rec if rec is not None and rec.get("kind") == self.kind else None

    def get(self, run_id: str) -> Optional[T]:
        rec = self.read(run_id)
        return self.convert(rec) if rec is not None else None

    def list(self, where: Optional[Mapping[str, Any]] = None, *,
             statuses: Optional[Iterable[str]] = None,
             order_by: Optional[str] = None,
             limit: Optional[int] = None, **filters: Any) -> List[T]:
        """Runs of this kind matching ``where`` (column equals value; the
        kind's own id names are accepted) and the other filters of
        :func:`list_runs`."""
        recs = list_runs(kind=self.kind, where=where, statuses=statuses,
                         order_by=order_by or self.order_by, limit=limit, **filters)
        return [self.convert(r) for r in recs]

    def active(self, where: Optional[Mapping[str, Any]] = None, *,
               order_by: Optional[str] = None, **filters: Any) -> List[T]:
        """The runs in a live status (``live_statuses``) matching ``where``."""
        return self.list(where, statuses=self.live_statuses, order_by=order_by, **filters)

    # ── Stop requests ──────────────────────────────────────────────────────

    def request_stop(self, run_id: str) -> bool:
        """Mark a live run of this kind as stopping (see :func:`request_stop`)."""
        if self.read(run_id) is None:
            return False
        return request_stop(run_id)

    def stop_requested(self, run_id: str) -> bool:
        return stop_requested(run_id)


# ── Registry ─────────────────────────────────────────────────────────────────

_ADAPTERS: Dict[str, str] = {
    "flow": "flow.run_store",
    "loop": "loops.store",
    "team": "teams.store",
    "scenario": "playground.store",
}


def store_for(kind: str) -> EntityRunStore:
    """The adapter store of one kind (each adapter module exposes ``RUNS``)."""
    import importlib
    module = importlib.import_module(_ADAPTERS[kind_of(kind)])
    return getattr(module, "RUNS")


def convert(rec: Dict[str, Any]) -> Any:
    """A generic record in its kind's own shape (a ``TeamRun``, a ``SimRun``,
    a ``LoopRun``, or the flow's dict)."""
    return store_for(str(rec.get("kind") or "")).convert(rec)


__all__ = [
    "TABLE", "KINDS", "KIND_FIELDS", "RESOURCES", "COLUMNS", "DEFAULT_ORDER",
    "EntityRunStore", "record_from_row", "utc_now_iso",
    "upsert", "update", "set_status", "mark_running", "close", "request_stop",
    "stop_requested", "touch_heartbeat", "save_checkpoint", "load_checkpoint",
    "clear_checkpoint", "delete", "delete_where",
    "get", "list_runs", "active_runs", "children", "leaf_children", "counts_by_kind",
    "store_for", "convert", "notify",
]
