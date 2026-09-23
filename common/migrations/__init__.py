"""
Numbered schema migrations, applied in order under one transaction.

A migration is a file in this directory named ``NNNN_<name>.sql`` or
``NNNN_<name>.py``; ``NNNN`` is its version. The ledger table
``schema_migrations`` records which versions a database has, and
``apply_pending`` runs every version above the ledger's maximum, oldest first.
It is called by ``common.db._ensure_ready`` inside the same transaction as the
legacy JSON import, so a migration either lands whole or not at all (both
SQLite and Postgres run DDL transactionally).

Formats:

- ``.sql``: statements separated by ``;`` with ``--`` line comments, written
  in SQLite syntax. :func:`sql_for_dialect` rewrites the few type names that
  differ for Postgres (``INTEGER PRIMARY KEY AUTOINCREMENT`` → ``BIGSERIAL
  PRIMARY KEY``, ``INTEGER`` → ``BIGINT``, ``REAL`` → ``DOUBLE PRECISION``,
  ``BLOB`` → ``BYTEA``). A file named ``NNNN_<name>.postgres.sql`` or
  ``NNNN_<name>.sqlite.sql`` replaces the plain one for that dialect when the
  rewrite is not enough.
- ``.py``: a module with ``upgrade(conn, dialect)``. For the odd migration that
  needs to look at the data, or to branch on the dialect in code.

Numbering starts at 5, not 1: versions 1 to 4 were the pre-ledger era, when
``common.db`` carried the whole schema as one DDL string plus an
``_ADDED_COLUMNS`` map of post-hoc ``ALTER TABLE``s and stamped
``meta.schema_version`` by hand. ``0005_baseline`` is that schema in its final
shape, and it brings a pre-ledger database up to date first (see
``legacy.py``), so a state directory from any earlier build opens cleanly.
``meta.schema_version`` is still written (as the highest applied version) so
an older build that opens a newer database refuses instead of guessing.

Adding a migration: drop ``0006_<what>.sql`` here with the ``CREATE TABLE`` /
``ALTER TABLE ... ADD COLUMN`` statements, and nothing else changes. Keep the
baseline file as it is: it describes version 5, not the current schema.
"""
from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Set

MIGRATIONS_DIR = Path(__file__).resolve().parent

LEDGER_TABLE = "schema_migrations"

_FILE_RE = re.compile(r"^(\d{4})_([A-Za-z0-9_]+?)(?:\.(sqlite|postgres))?\.(sql|py)$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    kind: str            # "sql" | "py"
    dialect: Optional[str]  # None = both


def discover() -> List[Migration]:
    """Every migration file, sorted by version; dialect-specific files included
    (``select_for`` picks between them)."""
    found: List[Migration] = []
    for path in sorted(MIGRATIONS_DIR.iterdir()):
        m = _FILE_RE.match(path.name)
        if not m:
            continue
        found.append(Migration(
            version=int(m.group(1)), name=m.group(2), path=path,
            kind=m.group(4), dialect=m.group(3),
        ))
    found.sort(key=lambda mg: (mg.version, mg.dialect or ""))
    return found


def select_for(dialect: str, migrations: Optional[Iterable[Migration]] = None) -> List[Migration]:
    """One migration per version for ``dialect``: the dialect-specific file
    when there is one, else the shared one."""
    by_version: dict[int, Migration] = {}
    for mg in (migrations if migrations is not None else discover()):
        if mg.dialect not in (None, dialect):
            continue
        current = by_version.get(mg.version)
        if current is None or (current.dialect is None and mg.dialect == dialect):
            by_version[mg.version] = mg
    return [by_version[v] for v in sorted(by_version)]


def latest_version(dialect: str = "sqlite") -> int:
    versions = [mg.version for mg in select_for(dialect)]
    return max(versions) if versions else 0


# ── SQL text handling ────────────────────────────────────────────────────────

def split_statements(text: str) -> List[str]:
    """Split a ``.sql`` file into statements: ``--`` comments stripped, then
    split on ``;``. Exact for our files (no block comments, no ``;`` or
    ``--`` inside string literals)."""
    lines = []
    for line in text.split("\n"):
        idx = line.find("--")
        if idx != -1:
            line = line[:idx]
        lines.append(line)
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


# Case-sensitive on purpose: type names are upper case in our migration files,
# column names lower case, so a column called ``blob`` or ``real`` is left alone.
_PG_REWRITES = (
    (re.compile(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b"), "BIGSERIAL PRIMARY KEY"),
    (re.compile(r"\bINTEGER\b"), "BIGINT"),
    (re.compile(r"\bREAL\b"), "DOUBLE PRECISION"),
    (re.compile(r"\bBLOB\b"), "BYTEA"),
)


def sql_for_dialect(statement: str, dialect: str) -> str:
    """Rewrite one SQLite-syntax DDL statement for ``dialect``."""
    if dialect != "postgres":
        return statement
    for pattern, replacement in _PG_REWRITES:
        statement = pattern.sub(replacement, statement)
    return statement


def execute_sql(conn: Any, dialect: str, text: str) -> None:
    for statement in split_statements(text):
        conn.execute(sql_for_dialect(statement, dialect))


# ── Introspection (dialect-aware, usable from migrations) ────────────────────

def table_exists(conn: Any, dialect: str, table: str) -> bool:
    if dialect == "postgres":
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = ?", (table,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    return row is not None


def table_columns(conn: Any, dialect: str, table: str) -> Set[str]:
    if dialect == "postgres":
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ?", (table,)
        ).fetchall()
        return {str(r[0]) for r in rows}
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r["name"]) for r in rows}


def add_column_if_missing(conn: Any, dialect: str, table: str, column: str, decl: str) -> bool:
    """``ALTER TABLE ADD COLUMN`` unless the column is already there. Returns
    True when it was added. Both dialects accept the same statement."""
    if column in table_columns(conn, dialect, table):
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_for_dialect(decl, dialect)}")
    return True


# ── The ledger and the runner ────────────────────────────────────────────────

def _ensure_ledger(conn: Any, dialect: str) -> None:
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} ("
        "version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)"
        if dialect != "postgres" else
        f"CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} ("
        "version BIGINT PRIMARY KEY, name TEXT, applied_at TEXT)"
    )


def applied_versions(conn: Any, dialect: str) -> List[int]:
    _ensure_ledger(conn, dialect)
    rows = conn.execute(f"SELECT version FROM {LEDGER_TABLE} ORDER BY version").fetchall()
    return [int(r[0]) for r in rows]


def _load_py(mg: Migration) -> Callable[[Any, str], None]:
    spec = importlib.util.spec_from_file_location(f"common.migrations.m{mg.version}_{mg.name}", mg.path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration {mg.path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    upgrade = getattr(module, "upgrade", None)
    if not callable(upgrade):
        raise RuntimeError(f"migration {mg.path.name} has no upgrade(conn, dialect)")
    return upgrade


def apply_one(conn: Any, dialect: str, mg: Migration) -> None:
    if mg.kind == "sql":
        execute_sql(conn, dialect, mg.path.read_text(encoding="utf-8"))
    else:
        _load_py(mg)(conn, dialect)
    conn.execute(
        f"INSERT INTO {LEDGER_TABLE} (version, name, applied_at) VALUES (?, ?, ?)",
        (mg.version, mg.name, datetime.now(timezone.utc).isoformat()),
    )


def apply_pending(conn: Any, dialect: str) -> List[int]:
    """Apply every migration above the ledger's maximum, in order, on the
    caller's already-open transaction. Returns the versions applied.

    Raises ``RuntimeError`` when the database is ahead of this build: its
    ledger names a version this checkout does not have.
    """
    available = select_for(dialect)
    known = {mg.version for mg in available}
    applied = applied_versions(conn, dialect)
    unknown = [v for v in applied if v not in known]
    if unknown:
        raise RuntimeError(
            f"This database has schema migration(s) {unknown} that this build of "
            f"Agents Hub does not know (it has up to {max(known) if known else 0}). "
            "It was written by a newer version of the app: upgrade before opening "
            "it, or point AGENTS_HUB_ROOT / AGENTS_HUB_DATABASE_URL elsewhere."
        )
    current = max(applied) if applied else 0
    done: List[int] = []
    for mg in available:
        if mg.version <= current:
            continue
        apply_one(conn, dialect, mg)
        done.append(mg.version)
    return done
