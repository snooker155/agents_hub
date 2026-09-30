"""
0022: version history for skills (fourth-cycle stage 3, skills with versions).

Every content change to a skill (``memory.procedural.Procedure``: name,
description, steps, body, tags, resources) lands one row here, written by
``memory/skill_versions.py`` from the procedure store's own ``add`` and
``update``, so a skill edited on the Skills page, by an agent's
``create_skill``, by an install or by a sync from a project repository all
leave the same trail. A use-count bump changes nothing here: the row is keyed
on a hash of the content, and an unchanged hash writes no row.

``version`` counts up from 1 per skill. ``snapshot`` is the JSON of the
content fields at that version; ``content_hash`` is its SHA-256, so the store
can tell a real change from a re-save. ``op`` says what produced the row
(create, update, restore, import, sync, install, origin). An agent pinned to a
version (``Procedure.pinned_version``) reads its skill from this table.

Written in SQLite syntax and passed through ``execute_sql``, which rewrites
``INTEGER PRIMARY KEY AUTOINCREMENT`` for Postgres.
"""
from __future__ import annotations

from typing import Any

from common.migrations import execute_sql

_DDL = """
CREATE TABLE IF NOT EXISTS skill_versions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id      TEXT NOT NULL,
    version       INTEGER NOT NULL,
    op            TEXT NOT NULL,
    snapshot      TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    actor_kind    TEXT,
    actor_id      TEXT,
    note          TEXT,
    at            TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_versions_skill
    ON skill_versions(skill_id, version);
"""


def upgrade(conn: Any, dialect: str) -> None:
    execute_sql(conn, dialect, _DDL)
