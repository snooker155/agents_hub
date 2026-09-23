"""
Database core for Agents Hub state: SQLite by default, Postgres by URL.

One database holds the stores that several processes mutate concurrently:
agent runs, tasks, sessions, nodes, flows, identity and the rest. With
``AGENTS_HUB_DATABASE_URL`` empty (the default) that is the SQLite file
``.agents_hub/agents_hub.db`` in WAL mode, which the backend, agent
subprocesses and node workers on one host share safely. Set it to a
``postgresql://`` URL and the same schema lives in Postgres, which is what
lets backend replicas and workers run on different hosts.

Usage is the same on both::

    from common.db import get_conn, transaction

    with transaction() as conn:            # atomic read-modify-write
        row = conn.execute("SELECT ... WHERE id = ?", (id,)).fetchone()
        conn.execute("UPDATE ...")

    conn = get_conn()                       # plain reads (autocommit)
    rows = conn.execute("SELECT ...").fetchall()

Callers write SQL with ``?`` placeholders and read rows by column name or
index (``row["status"]``, ``row[0]``, ``dict(row)``, ``row.keys()``). The
Postgres driver converts placeholders, escapes ``%``, turns Python bools into
integers (Postgres will not put a boolean into an INTEGER column) and returns
:class:`Row` objects that behave like ``sqlite3.Row``. Where the two SQL
dialects differ the helpers at the bottom of this module produce the right
text: :func:`upsert_sql`, :func:`json_text`, :func:`json_truthy`,
:func:`group_concat`, :func:`sum_if`, :data:`NO_LIMIT`. Portable forms are
used at call sites wherever one exists (``ON CONFLICT ... DO UPDATE``,
``RETURNING``, ``CASE WHEN``), so the helpers are few.

Transactions: SQLite uses ``BEGIN IMMEDIATE``, one writer at a time across
every process on the host. Postgres reproduces that discipline with a
transaction-scoped advisory lock (``pg_advisory_xact_lock``) taken at
``BEGIN``: a ``with transaction()`` block that reads then writes never loses
an update to a concurrent block, exactly as under SQLite, without every
read-modify-write in the codebase needing ``SELECT ... FOR UPDATE``. Reads
outside a transaction are unaffected (MVCC). Hot paths that want row-level
locking can take it explicitly later; the global lock is the safe default.

Connections: SQLite keeps one connection per thread. Postgres keeps one
connection *pool* per process (``psycopg_pool``; size ``AGENTS_HUB_DB_POOL_SIZE``,
default 10) and a lightweight per-thread handle that borrows a pooled
connection for the duration of a transaction, or for one statement when
outside one. Both wait up to ten seconds on a lock or a full pool, matching
the old FileLock timeout.

The first connection in a process makes the schema current: numbered
migrations from :mod:`common.migrations` and the one-time legacy JSON import
(:mod:`common.db_migrate`) run under one transaction, so any entrypoint
(backend, ``runtime/agent_run.py``, ``runtime/node_run.py``, the CLI) may
touch the stores first, and two processes racing to open the same fresh or
stale database serialize instead of both trying to ``ALTER`` a column in.
"""
from __future__ import annotations

import atexit
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, List, Optional, Sequence

from common.paths import AGENTS_HUB_ROOT, DB_FILE

DATABASE_URL_ENV = "AGENTS_HUB_DATABASE_URL"
POOL_SIZE_ENV = "AGENTS_HUB_DB_POOL_SIZE"

# Wait up to 10s on a locked database / a full pool — the old FileLock timeout.
_BUSY_TIMEOUT_MS = 10_000

# The one advisory lock every Postgres write transaction takes (see module
# docstring). Any constant works; this one spells "AHUB" in ASCII.
_PG_WRITE_LOCK_KEY = 0x41485542

_local = threading.local()
# RLock (not Lock): the one-time schema/migration runs while the lock is held,
# and an RLock keeps a same-thread re-entry from ever dead-locking.
_schema_lock = threading.RLock()
_schema_ready = False

# Resolved once per process on first use; None until then. Tests that switch
# databases reset it through reset_connections().
_dialect: Optional[str] = None
_pool: Any = None
_pool_lock = threading.Lock()
# Counts completed startup sequences in this process. Changes whenever the
# process (re)opens a database, which is what per-process caches keyed on
# "which database am I looking at" compare against (common/docstore.py).
_generation = 0


# ── Configuration ────────────────────────────────────────────────────────────

def database_url() -> str:
    """The configured database URL: the environment first (an explicitly empty
    value pins SQLite, which is how the test suite stays off a developer's
    ``.env``), then ``Settings`` (``.env``)."""
    if DATABASE_URL_ENV in os.environ:
        return os.environ[DATABASE_URL_ENV].strip()
    try:
        from common.config import settings
        return (getattr(settings, "database_url", "") or "").strip()
    except Exception:
        return ""


def dialect() -> str:
    """``"sqlite"`` or ``"postgres"``, fixed for the life of the process on
    first use."""
    global _dialect
    if _dialect is None:
        url = database_url()
        if url.startswith(("postgresql://", "postgres://")):
            _dialect = "postgres"
        elif url:
            raise RuntimeError(
                f"{DATABASE_URL_ENV} must be empty (SQLite) or a postgresql:// URL, got {url!r}")
        else:
            _dialect = "sqlite"
    return _dialect


def is_postgres() -> bool:
    return dialect() == "postgres"


def pool_size() -> int:
    raw = os.environ.get(POOL_SIZE_ENV, "").strip()
    if not raw:
        try:
            from common.config import settings
            raw = str(getattr(settings, "db_pool_size", "") or "")
        except Exception:
            raw = ""
    try:
        return max(1, int(raw)) if raw else 10
    except ValueError:
        return 10


# ── Rows ─────────────────────────────────────────────────────────────────────

class RowKeyError(KeyError, IndexError):
    """A column name the row does not have. Subclasses both, because
    ``sqlite3.Row`` raises IndexError for that and callers may catch either."""


class Row(tuple):
    """A result row: a tuple that also answers to column names, like
    ``sqlite3.Row``. ``dict(row)`` works (through ``keys()``), ``row[0]`` and
    ``row["name"]`` both work, iteration yields the values."""

    __slots__ = ()

    _columns: tuple  # set per class, see make_row_class

    def __getitem__(self, key):  # type: ignore[override]
        if isinstance(key, str):
            try:
                return tuple.__getitem__(self, self._columns.index(key))
            except ValueError:
                raise RowKeyError(key) from None
        return tuple.__getitem__(self, key)

    def keys(self) -> List[str]:
        return list(self._columns)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except (KeyError, IndexError):
            return default


_row_class_cache: dict = {}


def make_row_class(columns: Sequence[str]) -> type:
    """A Row subclass bound to one column list. Cached per column tuple so
    the per-row cost is one tuple construction."""
    key = tuple(columns)
    cls = _row_class_cache.get(key)
    if cls is None:
        cls = type("Row", (Row,), {"__slots__": (), "_columns": key})
        if len(_row_class_cache) > 4096:
            _row_class_cache.clear()
        _row_class_cache[key] = cls
    return cls


# ── Postgres: SQL adaptation ─────────────────────────────────────────────────

_sql_cache: dict = {}


def _adapt_sql_for_pg(sql: str, has_params: bool) -> str:
    """``?`` → ``%s`` outside string literals; ``%`` → ``%%`` everywhere when
    parameters are passed (psycopg scans the whole string, literals
    included). Cached: the SQL text is nearly always a constant."""
    key = (sql, has_params)
    out = _sql_cache.get(key)
    if out is not None:
        return out
    parts: List[str] = []
    in_quote = False
    for ch in sql:
        if ch == "'":
            in_quote = not in_quote
            parts.append(ch)
        elif ch == "?" and not in_quote:
            parts.append("%s")
        elif ch == "%" and has_params:
            parts.append("%%")
        else:
            parts.append(ch)
    out = "".join(parts)
    if len(_sql_cache) > 8192:
        _sql_cache.clear()
    _sql_cache[key] = out
    return out


def _adapt_params_for_pg(params: Any) -> Any:
    if params is None:
        return None
    if isinstance(params, dict):
        return {k: (int(v) if isinstance(v, bool) else v) for k, v in params.items()}
    return [int(v) if isinstance(v, bool) else v for v in params]


class _Result:
    """What ``execute`` returns on Postgres: rows fetched eagerly (the pooled
    connection may be returned right after the statement), with the cursor
    methods callers use."""

    __slots__ = ("_rows", "_pos", "rowcount", "description", "lastrowid")

    def __init__(self, cur: Any) -> None:
        self.rowcount = cur.rowcount
        self.lastrowid = None
        self.description = cur.description
        if cur.description:
            row_cls = make_row_class([d.name for d in cur.description])
            self._rows = [row_cls(r) for r in cur.fetchall()]
        else:
            self._rows = []
        self._pos = 0

    def fetchone(self) -> Optional[Row]:
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchmany(self, size: int = 1) -> List[Row]:
        rows = self._rows[self._pos:self._pos + size]
        self._pos += len(rows)
        return rows

    def fetchall(self) -> List[Row]:
        rows = self._rows[self._pos:]
        self._pos = len(self._rows)
        return rows

    def __iter__(self) -> Iterator[Row]:
        while self._pos < len(self._rows):
            yield self.fetchone()  # type: ignore[misc]

    def close(self) -> None:
        self._rows = []


class PgConnection:
    """The per-thread Postgres handle behind ``get_conn()``.

    Outside a transaction every ``execute`` borrows a pooled connection for
    that one statement (autocommit). ``begin()`` pins one until ``commit()``
    or ``rollback()``, so a ``with transaction()`` block, and every
    ``get_conn()`` call made from inside it on the same thread, sees its own
    uncommitted writes, exactly like the per-thread SQLite connection."""

    def __init__(self, pool: Any, raw: Any = None) -> None:
        self._pool = pool
        self._pinned: Any = None
        # A dedicated psycopg connection instead of a pool (from_raw): used by
        # the transfer tool, which talks to a database this process is not
        # configured with.
        self._raw = raw

    @classmethod
    def from_raw(cls, raw: Any) -> "PgConnection":
        """Wrap one autocommit psycopg connection, no pool."""
        return cls(None, raw)

    # -- transactions --------------------------------------------------------
    @property
    def in_transaction(self) -> bool:
        return self._pinned is not None

    def _acquire(self) -> Any:
        if self._raw is not None:
            return self._raw
        return self._pool.getconn(timeout=_BUSY_TIMEOUT_MS / 1000.0)

    def _release(self, raw: Any) -> None:
        if self._raw is None:
            self._pool.putconn(raw)

    def begin(self) -> None:
        if self._pinned is not None:
            raise RuntimeError("transaction already open on this thread")
        raw = self._acquire()
        try:
            raw.execute("BEGIN")
            raw.execute("SELECT pg_advisory_xact_lock(%s)", (_PG_WRITE_LOCK_KEY,))
        except Exception:
            try:
                raw.execute("ROLLBACK")
            except Exception:
                pass
            self._release(raw)
            raise
        self._pinned = raw

    def _finish(self, verb: str) -> None:
        raw, self._pinned = self._pinned, None
        if raw is None:
            return
        try:
            raw.execute(verb)
        finally:
            self._release(raw)

    def commit(self) -> None:
        self._finish("COMMIT")

    def rollback(self) -> None:
        self._finish("ROLLBACK")

    # -- statements ----------------------------------------------------------
    def execute(self, sql: str, params: Any = None) -> _Result:
        verb = sql.strip().upper()
        if verb in ("BEGIN", "BEGIN IMMEDIATE", "BEGIN EXCLUSIVE", "BEGIN DEFERRED"):
            self.begin()
            return _Result(_NoCursor)
        if verb == "COMMIT":
            self.commit()
            return _Result(_NoCursor)
        if verb == "ROLLBACK":
            self.rollback()
            return _Result(_NoCursor)
        has_params = params is not None and (not hasattr(params, "__len__") or len(params) > 0)
        text = _adapt_sql_for_pg(sql, has_params)
        args = _adapt_params_for_pg(params) if has_params else None
        if self._pinned is not None:
            return _Result(self._pinned.execute(text, args))
        if self._raw is not None:
            return _Result(self._raw.execute(text, args))
        with self._pool.connection(timeout=_BUSY_TIMEOUT_MS / 1000.0) as raw:
            return _Result(raw.execute(text, args))

    def executemany(self, sql: str, seq_of_params: Iterable[Any]) -> _Result:
        text = _adapt_sql_for_pg(sql, True)
        batches = [_adapt_params_for_pg(p) for p in seq_of_params]
        if not batches:
            return _Result(_NoCursor)

        def run(raw: Any) -> _Result:
            with raw.cursor() as cur:
                cur.executemany(text, batches)
                return _Result(cur)

        if self._pinned is not None:
            return run(self._pinned)
        if self._raw is not None:
            return run(self._raw)
        with self._pool.connection(timeout=_BUSY_TIMEOUT_MS / 1000.0) as raw:
            return run(raw)

    def close(self) -> None:
        if self._pinned is not None:
            self.rollback()
        if self._raw is not None:
            try:
                self._raw.close()
            except Exception:
                pass


class _NoCursor:
    """Stands in for a cursor when a statement produced none."""
    rowcount = -1
    description = None

    @staticmethod
    def fetchall() -> list:
        return []


def _get_pool() -> Any:
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise RuntimeError(
                f"{DATABASE_URL_ENV} is set but the Postgres driver is not installed: "
                "pip install 'psycopg[binary]' psycopg_pool"
            ) from exc
        _pool = ConnectionPool(
            database_url(),
            min_size=1,
            max_size=pool_size(),
            open=True,
            timeout=_BUSY_TIMEOUT_MS / 1000.0,
            kwargs={
                "autocommit": True,
                # Applies to the advisory lock too: the Postgres counterpart of
                # SQLite's busy_timeout.
                "options": f"-c lock_timeout={_BUSY_TIMEOUT_MS}",
            },
        )
        atexit.register(close_pool)
    return _pool


# ── Connections ──────────────────────────────────────────────────────────────

def _connect_sqlite() -> sqlite3.Connection:
    AGENTS_HUB_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), timeout=_BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    # Autocommit mode; transactions are explicit via transaction().
    conn.isolation_level = None
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    return conn


def _connect() -> Any:
    if dialect() == "postgres":
        return PgConnection(_get_pool())
    return _connect_sqlite()


def _begin(conn: Any) -> None:
    if isinstance(conn, PgConnection):
        conn.begin()
    else:
        conn.execute("BEGIN IMMEDIATE")


def _commit(conn: Any) -> None:
    if isinstance(conn, PgConnection):
        conn.commit()
    else:
        conn.execute("COMMIT")


def _rollback(conn: Any) -> None:
    if isinstance(conn, PgConnection):
        conn.rollback()
    else:
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


def _ensure_ready(conn: Any) -> None:
    """Make the schema current and run the one-time JSON migration, exactly
    once per process, all inside one write transaction (see module
    docstring)."""
    global _schema_ready, SCHEMA_VERSION, _generation
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        from common import migrations
        from common import db_migrate

        SCHEMA_VERSION = migrations.latest_version(dialect())
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
                    "upgrade before opening it, or point AGENTS_HUB_ROOT / "
                    f"{DATABASE_URL_ENV} at a different database."
                )

            applied = migrations.apply_pending(conn, dialect())

            if stored_version != SCHEMA_VERSION:
                conn.execute(
                    upsert_sql("meta", ("key", "value"), ("key",)),
                    ("schema_version", str(SCHEMA_VERSION)),
                )

            # Legacy JSON migration — guarded by a meta marker so concurrent
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
            print(f"[db] applied schema migration(s) {applied} ({dialect()})")

        if migrated is not None:
            db_migrate.rename_migrated_sources()
            if any(migrated.values()):
                print(f"[db] migrated legacy JSON state into the database: {migrated}")

        if flow_runs_migrated is not None:
            db_migrate.rename_flow_runs_source()
            if flow_runs_migrated:
                print(f"[db] migrated {flow_runs_migrated} flow run(s) into the database")

        _schema_ready = True
        _generation += 1


def get_conn() -> Any:
    """Return this thread's connection, creating it (and the schema) if needed.

    A ``sqlite3.Connection`` or a :class:`PgConnection`; both take
    ``execute(sql, params)`` with ``?`` placeholders and return rows that
    answer to column names."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    _ensure_ready(conn)
    return conn


@contextmanager
def transaction() -> Iterator[Any]:
    """Run a block inside a single write transaction.

    Re-entrant within a thread: a nested ``with transaction()`` joins the
    outer transaction instead of starting a new one, so helper functions can
    use it freely. SQLite: ``BEGIN IMMEDIATE``. Postgres: ``BEGIN`` plus the
    process-wide advisory write lock (see module docstring)."""
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
    """Forget every per-thread connection, the resolved dialect and the pool,
    so the next ``get_conn()`` starts over. For tests and for a process that
    re-points ``DB_FILE`` / the URL; never needed in normal operation."""
    global _local, _schema_ready, _dialect, _pool
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    pool, _pool = _pool, None
    if pool is not None:
        try:
            pool.close()
        except Exception:
            pass
    _local = threading.local()
    _schema_ready = False
    _dialect = None


def close_pool() -> None:
    """Close the Postgres pool at process shutdown (no-op on SQLite)."""
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        try:
            pool.close()
        except Exception:
            pass


def truncate_all_tables(conn: Optional[Any] = None) -> List[str]:
    """Empty every table in the schema (the test suite's per-test reset on
    Postgres, where a fresh file is not an option). Returns the table names.
    Sequences restart, so autoincrement columns count from 1 again like in a
    fresh database. Never called by the application."""
    conn = conn or get_conn()
    if dialect() == "postgres":
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
        ).fetchall()
        names = [str(r[0]) for r in rows]
        if names:
            conn.execute("TRUNCATE " + ", ".join(names) + " RESTART IDENTITY")
        return names
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    names = [str(r[0]) for r in rows]
    for name in names:
        conn.execute(f"DELETE FROM {name}")
    return names


# ── Dialect helpers ──────────────────────────────────────────────────────────
# Each returns SQL text for the dialect in use. Use them only where the two
# dialects genuinely differ; portable SQL needs no helper.

def upsert_sql(table: str, columns: Sequence[str], key: Sequence[str]) -> str:
    """``INSERT ... ON CONFLICT (key) DO UPDATE SET ...`` with ``?`` placeholders
    in ``columns`` order, updating every non-key column. Valid on both
    dialects (SQLite 3.24+), and the replacement for ``INSERT OR REPLACE``,
    which Postgres lacks and which SQLite implements as delete-then-insert
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
    text (a string value compares equal to a ``?`` string parameter on both
    dialects; NULL when the key is absent)."""
    if dialect() == "postgres":
        return f"(({column})::jsonb ->> '{key}')"
    return f"json_extract({column}, '$.{key}')"


def json_truthy(column: str, key: str) -> str:
    """A boolean SQL expression: the JSON value at ``key`` is ``true`` or
    ``1`` (the two spellings a boolean flag has been stored with)."""
    if dialect() == "postgres":
        return f"(COALESCE(({column})::jsonb ->> '{key}', '') IN ('true', '1'))"
    return f"(COALESCE(json_extract({column}, '$.{key}'), 0) IN (1, 'true'))"


def group_concat(expr: str, separator: str = ",") -> str:
    """Comma-joined aggregate: ``GROUP_CONCAT`` / ``string_agg``. ``expr`` may
    start with ``DISTINCT``."""
    if dialect() == "postgres":
        return f"string_agg({expr}, '{separator}')"
    if separator == ",":
        return f"GROUP_CONCAT({expr})"
    return f"GROUP_CONCAT({expr}, '{separator}')"


def sum_if(condition: str) -> str:
    """Count the rows where ``condition`` holds. Portable; SQLite lets
    ``SUM(status = 'x')`` through, Postgres has no ``SUM(boolean)``."""
    return f"SUM(CASE WHEN {condition} THEN 1 ELSE 0 END)"


class _NoLimit:
    """The parameter value for ``LIMIT ?`` meaning "no limit": ``-1`` on SQLite,
    NULL on Postgres. Resolved at use so the dialect is read lazily."""

    def __call__(self) -> Any:
        return None if dialect() == "postgres" else -1


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
    except Exception:
        return default
