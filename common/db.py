"""
Database core for Agents Hub state: one SQLite file.

One database holds the stores that several processes on this host mutate
concurrently: agent runs, tasks, sessions, flows and the rest. It is the
SQLite file ``.agents_hub/agents_hub.db`` in WAL mode, which the backend,
agent subprocesses and the CLI share safely.

Usage::

    from common.db import get_conn, transaction

    with transaction() as conn:            # atomic read-modify-write
        row = conn.execute("SELECT ... WHERE id = ?", (id,)).fetchone()
        conn.execute("UPDATE ...")

    conn = get_conn()                       # plain reads (autocommit)
    rows = conn.execute("SELECT ...").fetchall()

Callers write SQL with ``?`` placeholders and read rows by column name or
index (``row["status"]``, ``row[0]``, ``dict(row)``, ``row.keys()``), which
is what ``sqlite3.Row`` provides. The helpers at the bottom of this module
build the few SQL fragments call sites share (:func:`upsert_sql`,
:func:`json_text`, :func:`json_truthy`, :func:`group_concat`, :func:`sum_if`,
:data:`NO_LIMIT`).

Transactions use ``BEGIN IMMEDIATE``: one writer at a time across every
process on the host, so a ``with transaction()`` block that reads then
writes never loses an update to a concurrent block.

Connections: one per thread, each waiting up to ten seconds on a lock.

The first connection in a process makes the schema current: numbered
migrations from :mod:`common.migrations` and the one-time legacy JSON import
(:mod:`common.db_migrate`) run under one transaction, so any entrypoint
(backend, ``runtime/agent_run.py``, the CLI) may touch the stores first, and
two processes racing to open the same fresh or stale database serialize
instead of both trying to ``ALTER`` a column in.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from common.paths import AGENTS_HUB_ROOT, DB_FILE

log = logging.getLogger(__name__)

# Wait up to 10s on a locked database, the old FileLock timeout.
_BUSY_TIMEOUT_MS = 10_000

_local = threading.local()
# RLock (not Lock): the one-time schema/migration runs while the lock is held,
# and an RLock keeps a same-thread re-entry from ever dead-locking.
_schema_lock = threading.RLock()
_schema_ready = False
# The database file the flag above was set for (see _database_key). A startup
# that finishes for a file the process has since moved away from (a thread a
# test left behind, still migrating the previous test's file) must not mark
# the new one ready.
_ready_key: Optional[str] = None
# Counts completed startup sequences in this process. Changes whenever the
# process (re)opens a database, which is what per-process caches keyed on
# "which database am I looking at" compare against (common/docstore.py).
_generation = 0


# ── Configuration ────────────────────────────────────────────────────────────

def dialect() -> str:
    """Always ``"sqlite"``. Kept as a function because the migrations take
    the dialect as a parameter and a few callers log it."""
    return "sqlite"


# ── Connections ──────────────────────────────────────────────────────────────

def _connect_sqlite(path: Optional[Path] = None) -> sqlite3.Connection:
    AGENTS_HUB_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path or DB_FILE), timeout=_BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    # Autocommit mode; transactions are explicit via transaction().
    conn.isolation_level = None
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    return conn


def _database_key() -> str:
    """Which database file this process is pointed at right now."""
    return str(DB_FILE)


def _connect() -> sqlite3.Connection:
    return _connect_keyed()[0]


def _connect_keyed() -> Tuple[sqlite3.Connection, str]:
    """A new connection and the key of the database it is open on, both from
    one read of the target, so a switch in between cannot pair them wrong."""
    path = DB_FILE
    return _connect_sqlite(path), str(path)


def _begin(conn: Any) -> None:
    conn.execute("BEGIN IMMEDIATE")


def _commit(conn: Any) -> None:
    conn.execute("COMMIT")


def _rollback(conn: Any) -> None:
    conn.execute("ROLLBACK")


# ── Schema readiness ─────────────────────────────────────────────────────────

def _latest_schema_version() -> int:
    from common import migrations
    return migrations.latest_version(dialect())


# The highest migration this build knows. ``meta.schema_version`` is stamped
# with it so an older build (which compares against its own constant) refuses
# a database written by a newer one. Kept as a module attribute for callers
# and tests; the value comes from the migrations directory.
SCHEMA_VERSION = 5


def _snapshot_before_migrate(conn: Any) -> None:
    """Archive a database that is about to take new migrations, so the
    previous release can be restored (common/db_backup.pre_migrate_snapshot).

    Only an existing database with versions still to apply: a fresh file has
    nothing to go back to. A failed snapshot is logged and the upgrade goes
    on, since refusing to start over a backup would turn a disk hiccup into an
    outage; ``AGENTS_HUB_BACKUP_BEFORE_MIGRATE=0`` turns it off.
    """
    from common import db_backup, migrations
    if not db_backup.pre_migrate_enabled():
        return
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "meta" not in tables:
            return
        applied: set = set()
        if migrations.LEDGER_TABLE in tables:
            applied = {int(r[0]) for r in conn.execute(
                f"SELECT version FROM {migrations.LEDGER_TABLE}").fetchall()}
        pending = [mg.version for mg in migrations.select_for("sqlite")
                   if mg.version not in applied]
        if not pending:
            return
        meta = {r[0]: r[1] for r in conn.execute("SELECT key, value FROM meta").fetchall()}
        archive = db_backup.pre_migrate_snapshot(
            conn, pending=pending, schema_version=max(applied) if applied else meta.get("schema_version"),
            app_version=meta.get("app_version") or "")
        log.info("database archived before migration(s) %s: %s", pending, archive)
    except Exception as exc:  # noqa: BLE001 - an upgrade must not fail over its safety copy
        log.warning("could not archive the database before migrating: %s", exc)


def _ensure_ready(conn: Any, key: Optional[str] = None) -> None:
    """Make the schema current and run the one-time JSON migration, exactly
    once per process and database, all inside one write transaction (see
    module docstring). ``key`` is the database ``conn`` was opened on."""
    global _schema_ready, _ready_key, SCHEMA_VERSION, _generation
    key = key or _database_key()
    if _schema_ready and _ready_key == key:
        return
    with _schema_lock:
        if _schema_ready and _ready_key == key:
            return
        from common import migrations
        from common import db_migrate

        SCHEMA_VERSION = migrations.latest_version(dialect())
        _snapshot_before_migrate(conn)
        _begin(conn)
        migrated: Optional[dict] = None
        flow_runs_migrated: Optional[int] = None
        try:
            # Needed before the version read below; also in the baseline
            # (harmless to create twice, both are IF NOT EXISTS).
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")

            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            try:
                stored_version = int(row["value"]) if row and row["value"] is not None else 0
            except (TypeError, ValueError):
                stored_version = 0

            if stored_version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"This database's schema_version ({stored_version}) is newer than "
                    f"what this build of Agents Hub understands (SCHEMA_VERSION="
                    f"{SCHEMA_VERSION}). It was written by a newer version of the app: "
                    "upgrade before opening it, or point AGENTS_HUB_ROOT at a different "
                    "database."
                )

            applied = migrations.apply_pending(conn, dialect())

            # The build that last opened this database, which is the one a
            # rollback returns to (common/version.py).
            from common.version import app_version
            conn.execute(upsert_sql("meta", ("key", "value"), ("key",)),
                         ("app_version", app_version()))

            if stored_version != SCHEMA_VERSION:
                conn.execute(
                    upsert_sql("meta", ("key", "value"), ("key",)),
                    ("schema_version", str(SCHEMA_VERSION)),
                )

            # Legacy JSON migration, guarded by a meta marker so concurrent
            # processes and restarts never import twice. Same transaction:
            # either the schema and the import land together, or neither does.
            row = conn.execute("SELECT value FROM meta WHERE key='json_migrated'").fetchone()
            if row is None:
                migrated = db_migrate.migrate_legacy_json(conn)

            # flow_runs.json moved into the database after the first migration
            # shipped, so it carries its own marker.
            row = conn.execute("SELECT value FROM meta WHERE key='flow_runs_migrated'").fetchone()
            if row is None:
                flow_runs_migrated = db_migrate.migrate_flow_runs(conn)
        except Exception:
            _rollback(conn)
            raise
        else:
            _commit(conn)

        if applied:
            log.info("applied schema migration(s) %s", applied)

        if migrated is not None:
            db_migrate.rename_migrated_sources()
            if any(migrated.values()):
                log.info("migrated legacy JSON state into the database: %s", migrated)

        if flow_runs_migrated is not None:
            db_migrate.rename_flow_runs_source()
            if flow_runs_migrated:
                log.info("migrated %s flow run(s) into the database", flow_runs_migrated)

        _schema_ready = True
        _ready_key = key
        _generation += 1


def get_conn() -> sqlite3.Connection:
    """Return this thread's connection, creating it (and the schema) if needed."""
    # One read of the thread-local holder: a test swapping ``_local`` between
    # two reads would pair the old database's connection with the new key.
    local = _local
    conn = getattr(local, "conn", None)
    if conn is None:
        conn, local.key = _connect_keyed()
        local.conn = conn
    _ensure_ready(conn, getattr(local, "key", None))
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Run a block inside a single write transaction (``BEGIN IMMEDIATE``).

    Re-entrant within a thread: a nested ``with transaction()`` joins the
    outer transaction instead of starting a new one, so helper functions can
    use it freely."""
    conn = get_conn()
    depth = getattr(_local, "tx_depth", 0)
    if depth > 0:
        _local.tx_depth = depth + 1
        try:
            yield conn
        finally:
            _local.tx_depth -= 1
        return

    _local.tx_depth = 1
    _begin(conn)
    try:
        yield conn
    except BaseException:
        _rollback(conn)
        raise
    else:
        _commit(conn)
    finally:
        _local.tx_depth = 0


def reset_connections() -> None:
    """Forget every per-thread connection, so the next ``get_conn()`` starts
    over. For tests and for a process that re-points ``DB_FILE``; never
    needed in normal operation."""
    global _local, _schema_ready
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - best-effort close, connection is being discarded anyway
            log.debug("connection close failed during reset", exc_info=True)
    _local = threading.local()
    _schema_ready = False


def truncate_all_tables(conn: Optional[Any] = None) -> List[str]:
    """Empty every table in the schema. Returns the table names. Never called
    by the application; a test helper."""
    db = conn if conn is not None else get_conn()
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    names = [str(r[0]) for r in rows]
    for name in names:
        db.execute(f"DELETE FROM {name}")
    return names


# ── SQL helpers ──────────────────────────────────────────────────────────────

def upsert_sql(table: str, columns: Sequence[str], key: Sequence[str]) -> str:
    """``INSERT ... ON CONFLICT (key) DO UPDATE SET ...`` with ``?`` placeholders
    in ``columns`` order, updating every non-key column. The replacement for
    ``INSERT OR REPLACE``, which SQLite implements as delete-then-insert
    (resetting columns not in the list; this form leaves them alone)."""
    cols = list(columns)
    keys = list(key)
    updates = [c for c in cols if c not in keys]
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) "
           f"VALUES ({', '.join('?' * len(cols))}) "
           f"ON CONFLICT ({', '.join(keys)}) ")
    if updates:
        sql += "DO UPDATE SET " + ", ".join(f"{c} = excluded.{c}" for c in updates)
    else:
        sql += "DO NOTHING"
    return sql


def json_text(column: str, key: str) -> str:
    """The value at top-level ``key`` of the JSON document in ``column``, as
    text (NULL when the key is absent)."""
    return f"json_extract({column}, '$.{key}')"


def json_truthy(column: str, key: str) -> str:
    """A boolean SQL expression: the JSON value at ``key`` is ``true`` or
    ``1`` (the two spellings a boolean flag has been stored with)."""
    return f"(COALESCE(json_extract({column}, '$.{key}'), 0) IN (1, 'true'))"


def json_array_len(column: str, key: str) -> str:
    """Length of the JSON array at top-level ``key`` of ``column``; 0 when the
    key is absent or not an array."""
    return f"COALESCE(json_array_length({column}, '$.{key}'), 0)"


def order_by(sorts: Dict[str, str], sort: Optional[str], order: Optional[str],
             default: str, tiebreak: str = "") -> str:
    """An ``ORDER BY`` body from a whitelist of named sort expressions.

    ``sort`` is a key of ``sorts`` (falls back to ``default``), ``order`` is
    ``asc`` or ``desc`` (anything else reads as ``desc``). ``tiebreak`` is
    appended so pages stay stable when many rows share the sort value.
    Raises ``ValueError`` on an unknown key, so a route can answer 400."""
    key = sort or default
    if key not in sorts:
        raise ValueError(f"Unknown sort {key!r} (expected one of {', '.join(sorts)})")
    direction = "ASC" if str(order or "").lower() == "asc" else "DESC"
    body = f"{sorts[key]} {direction}"
    return f"{body}, {tiebreak}" if tiebreak else body


def group_concat(expr: str, separator: str = ",") -> str:
    """Comma-joined aggregate. ``expr`` may start with ``DISTINCT``."""
    if separator == ",":
        return f"GROUP_CONCAT({expr})"
    return f"GROUP_CONCAT({expr}, '{separator}')"


def sum_if(condition: str) -> str:
    """Count the rows where ``condition`` holds."""
    return f"SUM(CASE WHEN {condition} THEN 1 ELSE 0 END)"


class _NoLimit:
    """The parameter value for ``LIMIT ?`` meaning "no limit": ``-1``."""

    def __call__(self) -> Any:
        return -1


NO_LIMIT = _NoLimit()


# ── JSON column helpers ───────────────────────────────────────────────────────

def dumps(value: Any) -> str:
    """JSON-encode a value for a TEXT column (compact, unicode-preserving)."""
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(text: Optional[str], default: Any = None) -> Any:
    """Decode a JSON TEXT column, returning ``default`` for NULL/invalid."""
    if not text:
        return default
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default
