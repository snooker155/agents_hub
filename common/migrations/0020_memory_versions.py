"""
0020: version history for a memory pool's blocks, notes and structured slots
(fourth-cycle stage 2, feature G: memory with versions).

Every change record.py writes to a pool's blocks, notes or structured slots
through :mod:`memory.store` also lands one row here per changed item (create,
update, delete), plus a row for a restore or a redact. ``memory/versions.py``
is the only module that reads or writes this table.

``item_key`` is the block name, the note id, or the slot name; the version
number counts up from 1 per ``(memory_id, kind, item_key)``, independently of
every other item. ``value`` is the JSON of the item after the change (``null``
on a delete). ``redacted`` marks a row whose ``value`` has been overwritten by
:func:`memory.versions.redact`, so the UI can show a marker instead of content
that no longer exists.

Written in SQLite syntax and passed through ``execute_sql``, which rewrites
``INTEGER PRIMARY KEY AUTOINCREMENT`` for Postgres. ``ah db migrate`` applies
this like any other version.
"""
from __future__ import annotations

from typing import Any

from common.migrations import execute_sql

_DDL = """
CREATE TABLE IF NOT EXISTS memory_versions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id    TEXT NOT NULL,
    kind         TEXT NOT NULL,
    item_key     TEXT NOT NULL,
    version      INTEGER NOT NULL,
    op           TEXT NOT NULL,
    value        TEXT,
    actor_kind   TEXT,
    actor_id     TEXT,
    run_id       TEXT,
    at           TEXT NOT NULL,
    redacted     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memory_versions_item
    ON memory_versions(memory_id, kind, item_key, version);
CREATE INDEX IF NOT EXISTS idx_memory_versions_at
    ON memory_versions(memory_id, at);
"""


def upgrade(conn: Any, dialect: str) -> None:
    execute_sql(conn, dialect, _DDL)
