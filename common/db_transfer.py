"""
Moving the whole database between backends: ``ah db migrate --to <target>``.

The source is the database this process is configured with
(``AGENTS_HUB_DATABASE_URL``, or the SQLite file); the target is a
``postgresql://`` URL or a path to a SQLite file. The target gets the current
schema (the numbered migrations, exactly as a fresh database would), then
every table is copied row by row in one transaction, the row counts are
compared, and the legacy-JSON markers are carried over so the target never
tries to import ``agent_runs.json`` and friends a second time. Both
directions work, so the way back from Postgres to SQLite is the same command.

What it does not move: the files beside the database (``run_logs/``,
``workspaces/``, view assets, agent definitions). They stay in
``AGENTS_HUB_ROOT`` and are shared, copied or mounted separately; on a
multi-host deployment they move to the blob store (stage 2 of the plan).

The target must be empty (every table, ``meta`` and the ledger excepted), or
the caller passes ``force=True`` to have it emptied first. A mismatch in any
table's count aborts before commit, so a target is either fully loaded or
untouched.

``transfer`` also takes an optional ``source``: a URL or SQLite path to copy
from instead of the database this process is configured with. Nothing in
``ah db migrate`` uses it (the CLI command always copies from "here"), but
``common.db_backup`` does: a restore loads an archived SQLite file into the
configured database, which is the same operation with the two sides of the
usual direction swapped.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from common import db
from common import migrations

# Rows per INSERT batch. The whole copy is one transaction on the target
# anyway; this only bounds memory per statement.
BATCH = 500

# Never copied: the migration ledger is the target's own (written when its
# schema was created), and sqlite_sequence is SQLite's internal table.
SKIP_TABLES = {migrations.LEDGER_TABLE, "sqlite_sequence"}


def target_dialect(target: str) -> str:
    return "postgres" if target.startswith(("postgresql://", "postgres://")) else "sqlite"


def describe(target: str) -> str:
    """The target for messages, password hidden."""
    if target_dialect(target) == "sqlite":
        return str(Path(target).expanduser().resolve())
    scheme, _, rest = target.partition("://")
    creds, at, host = rest.rpartition("@")
    if at and ":" in creds:
        creds = creds.split(":", 1)[0] + ":***"
    return f"{scheme}://{creds}{at}{host}"


def open_target(target: str) -> Any:
    """A connection to ``target`` with the same ``execute`` surface as
    ``db.get_conn()`` (``?`` placeholders, name-addressable rows)."""
    if target_dialect(target) == "postgres":
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - environment
            raise RuntimeError(
                "the Postgres driver is not installed: pip install -r requirements-postgres.txt"
            ) from exc
        raw = psycopg.connect(target, autocommit=True,
                              options=f"-c lock_timeout={db._BUSY_TIMEOUT_MS}")
        return db.PgConnection.from_raw(raw)
    path = Path(target).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=db._BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _begin(conn: Any) -> None:
    conn.execute("BEGIN IMMEDIATE") if isinstance(conn, sqlite3.Connection) else conn.begin()


def _commit(conn: Any) -> None:
    conn.execute("COMMIT") if isinstance(conn, sqlite3.Connection) else conn.commit()


def _rollback(conn: Any) -> None:
    conn.execute("ROLLBACK") if isinstance(conn, sqlite3.Connection) else conn.rollback()


def prepare_target(conn: Any, dialect: str) -> List[int]:
    """Create or upgrade the target's schema, stamp ``meta.schema_version``
    and the legacy-import markers. Returns the migrations applied."""
    _begin(conn)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        applied = migrations.apply_pending(conn, dialect)
        stamp = datetime.now(timezone.utc).isoformat()
        for key, value in (
            ("schema_version", str(migrations.latest_version(dialect))),
            ("json_migrated", db.dumps({"at": stamp, "transferred": True})),
            ("flow_runs_migrated", db.dumps({"at": stamp, "transferred": True})),
        ):
            conn.execute(db.upsert_sql("meta", ("key", "value"), ("key",)), (key, value))
    except Exception:
        _rollback(conn)
        raise
    _commit(conn)
    return applied


def _table_names(conn: Any, dialect: str) -> List[str]:
    if dialect == "postgres":
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE' "
            "ORDER BY table_name").fetchall()
    else:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return [str(r[0]) for r in rows if str(r[0]) not in SKIP_TABLES]


def _count(conn: Any, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _batched(rows: Iterable[Any], size: int) -> Iterable[List[Any]]:
    batch: List[Any] = []
    for row in rows:
        batch.append(tuple(row))
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _reset_sequences(conn: Any, tables: List[str]) -> None:
    """After inserting rows with explicit ids into BIGSERIAL columns, move
    each sequence past the highest id so the next insert does not collide."""
    rows = conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND column_default LIKE 'nextval(%'"
    ).fetchall()
    for r in rows:
        table, column = str(r[0]), str(r[1])
        if table not in tables:
            continue
        conn.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', '{column}'), "
            f"COALESCE((SELECT MAX({column}) FROM {table}), 0) + 1, false)")


def transfer(target: str, *, source: Optional[str] = None, force: bool = False,
             batch: int = BATCH, log: Any = None) -> Dict[str, Any]:
    """Copy every table of ``source`` into ``target``.

    ``source`` defaults to the database this process is configured with
    (``AGENTS_HUB_DATABASE_URL``, or the SQLite file); passing a URL or a
    SQLite path instead copies from there, without this process needing to be
    configured to open it first.

    Returns ``{"target": ..., "applied": [...], "tables": {name: {"source":
    n, "target": n}}}``. Raises ``RuntimeError`` when the target is not empty
    (without ``force``) or a count disagrees; the target is then rolled back
    to its pre-copy state (schema only).
    """
    say = log or (lambda *_: None)
    own_source = source is None
    if own_source:
        src_conn = db.get_conn()
        src_dialect = db.dialect()
    else:
        src_dialect = target_dialect(source)
        src_conn = open_target(source)

    tgt_dialect = target_dialect(target)

    if own_source:
        source_ref = str(db.DB_FILE) if src_dialect == "sqlite" else db.database_url()
    else:
        source_ref = source
    if tgt_dialect == src_dialect:
        same = (Path(target).expanduser().resolve() == Path(source_ref).expanduser().resolve()
                if tgt_dialect == "sqlite" else target.strip() == source_ref.strip())
        if same:
            raise RuntimeError("the target is the source database itself")

    tconn = open_target(target)
    try:
        applied = prepare_target(tconn, tgt_dialect)
        if applied:
            say(f"target schema created: migrations {applied}")

        tables = [t for t in _table_names(src_conn, src_dialect)
                  if migrations.table_exists(tconn, tgt_dialect, t)]
        missing = [t for t in _table_names(src_conn, src_dialect) if t not in tables]
        if missing:
            raise RuntimeError(f"the target schema has no table(s) {missing}; "
                               "is the source from a newer build?")

        _begin(tconn)
        try:
            if not force:
                busy = [t for t in tables if t != "meta" and _count(tconn, t)]
                if busy:
                    raise RuntimeError(
                        f"the target already holds rows in {busy}; pass --force to empty it first")
            for table in tables:
                tconn.execute(f"DELETE FROM {table}")

            report: Dict[str, Dict[str, int]] = {}
            for table in tables:
                columns = sorted(migrations.table_columns(src_conn, src_dialect, table))
                tcolumns = migrations.table_columns(tconn, tgt_dialect, table)
                columns = [c for c in columns if c in tcolumns]
                if not columns:
                    continue
                cur = src_conn.execute(f"SELECT {', '.join(columns)} FROM {table}")
                sql = (f"INSERT INTO {table} ({', '.join(columns)}) "
                       f"VALUES ({', '.join('?' * len(columns))})")
                n = 0
                for chunk in _batched(cur, batch):
                    tconn.executemany(sql, chunk)
                    n += len(chunk)
                copied = _count(tconn, table)
                report[table] = {"source": n, "target": copied}
                say(f"{table}: {n} row(s)")
                if copied != n:
                    raise RuntimeError(f"{table}: copied {n} rows but the target counts {copied}")

            if tgt_dialect == "postgres":
                _reset_sequences(tconn, tables)
        except Exception:
            _rollback(tconn)
            raise
        _commit(tconn)
    finally:
        try:
            tconn.close()
        except Exception:
            pass
        if not own_source:
            try:
                src_conn.close()
            except Exception:
                pass

    return {"target": describe(target), "dialect": tgt_dialect,
            "applied": applied, "tables": report}


def status() -> Dict[str, Any]:
    """What this process is configured with, for ``ah db status``."""
    conn = db.get_conn()
    d = db.dialect()
    counts: Dict[str, int] = {}
    for table in _table_names(conn, d):
        try:
            counts[table] = _count(conn, table)
        except Exception:
            counts[table] = -1
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return {
        "dialect": d,
        "location": describe(db.database_url()) if d == "postgres" else str(db.DB_FILE),
        "schema_version": int(row[0]) if row and row[0] is not None else None,
        "migrations": migrations.applied_versions(conn, d),
        "counts": counts,
    }
