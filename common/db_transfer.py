"""
Copying the whole database into another SQLite file, table by table.

The source is the database this process is configured with (the SQLite file
under ``AGENTS_HUB_ROOT``) unless ``source`` names another file; the target is
a path to a SQLite file. The target gets the current schema (the numbered
migrations, exactly as a fresh database would), then every table is copied
row by row in one transaction, the row counts are compared, and the
legacy-JSON markers are carried over so the target never tries to import
``agent_runs.json`` and friends a second time.

What it does not move: the files beside the database (``run_logs/``,
``workspaces/``, view assets, agent definitions). They stay in
``AGENTS_HUB_ROOT``.

The target must be empty (every table, ``meta`` and the ledger excepted), or
the caller passes ``force=True`` to have it emptied first. A mismatch in any
table's count aborts before commit, so a target is either fully loaded or
untouched.

The one caller is ``common.db_backup``: a restore loads an archived SQLite
file into the configured database, and ``status`` feeds ``ah db status``.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from common import db
from common import migrations

log = logging.getLogger(__name__)

# Rows per INSERT batch. The whole copy is one transaction on the target
# anyway; this only bounds memory per statement.
BATCH = 500

# Never copied: the migration ledger is the target's own (written when its
# schema was created), and sqlite_sequence is SQLite's internal table.
SKIP_TABLES = {migrations.LEDGER_TABLE, "sqlite_sequence"}


def describe(target: str) -> str:
    """The target for messages: the resolved path."""
    return str(Path(target).expanduser().resolve())


def open_target(target: str) -> sqlite3.Connection:
    """A connection to the SQLite file at ``target`` with the same ``execute``
    surface as ``db.get_conn()`` (``?`` placeholders, name-addressable rows)."""
    path = Path(target).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=db._BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def prepare_target(conn: Any, dialect: str = "sqlite") -> List[int]:
    """Create or upgrade the target's schema, stamp ``meta.schema_version``
    and the legacy-import markers. Returns the migrations applied."""
    conn.execute("BEGIN IMMEDIATE")
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
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return applied


def _table_names(conn: Any, dialect: str = "sqlite") -> List[str]:
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


def transfer(target: str, *, source: Optional[str] = None, force: bool = False,
             batch: int = BATCH, log: Any = None) -> Dict[str, Any]:
    """Copy every table of ``source`` into ``target``.

    ``source`` defaults to the database this process is configured with;
    passing a SQLite path instead copies from there, without this process
    needing to be configured to open it first.

    Returns ``{"target": ..., "applied": [...], "tables": {name: {"source":
    n, "target": n}}}``. Raises ``RuntimeError`` when the target is not empty
    (without ``force``) or a count disagrees; the target is then rolled back
    to its pre-copy state (schema only).
    """
    say = log or (lambda *_: None)
    own_source = source is None
    src_conn = db.get_conn() if own_source else open_target(source)
    source_ref = str(db.DB_FILE) if own_source else str(source)
    if Path(target).expanduser().resolve() == Path(source_ref).expanduser().resolve():
        raise RuntimeError("the target is the source database itself")

    tconn = open_target(target)
    try:
        applied = prepare_target(tconn)
        if applied:
            say(f"target schema created: migrations {applied}")

        tables = [t for t in _table_names(src_conn)
                  if migrations.table_exists(tconn, "sqlite", t)]
        missing = [t for t in _table_names(src_conn) if t not in tables]
        if missing:
            raise RuntimeError(f"the target schema has no table(s) {missing}; "
                               "is the source from a newer build?")

        tconn.execute("BEGIN IMMEDIATE")
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
                columns = sorted(migrations.table_columns(src_conn, "sqlite", table))
                tcolumns = migrations.table_columns(tconn, "sqlite", table)
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
        except Exception:
            tconn.execute("ROLLBACK")
            raise
        tconn.execute("COMMIT")
    finally:
        try:
            tconn.close()
        except Exception:  # noqa: BLE001 - best-effort close, connection is being discarded anyway
            log.debug("target connection close failed", exc_info=True)
        if not own_source:
            try:
                src_conn.close()
            except Exception:  # noqa: BLE001 - best-effort close, connection is being discarded anyway
                log.debug("source connection close failed", exc_info=True)

    return {"target": describe(target), "dialect": "sqlite",
            "applied": applied, "tables": report}


def status() -> Dict[str, Any]:
    """What this process is configured with, for ``ah db status``."""
    conn = db.get_conn()
    counts: Dict[str, int] = {}
    for table in _table_names(conn):
        try:
            counts[table] = _count(conn, table)
        except Exception:  # noqa: BLE001 - one table's count failing must not break the whole status report
            log.debug("row count failed for table %s", table, exc_info=True)
            counts[table] = -1
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return {
        "dialect": "sqlite",
        "location": str(db.DB_FILE),
        "schema_version": int(row[0]) if row and row[0] is not None else None,
        "migrations": migrations.applied_versions(conn, "sqlite"),
        "counts": counts,
    }
