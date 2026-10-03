"""
0033: memory consolidation jobs (fifth-cycle stage 2, "dreams").

A consolidation folds a memory pool's own content and a handful of its recent
sessions into a *new* pool, never touching the source (``memory/consolidation.py``).
Each attempt is one row here: what it ran on, what it produced, and whether it
is still going. ``memory.consolidation`` is the only module that reads or
writes this table, the same ownership rule ``memory/versions.py`` and
``memory_versions`` already follow.

``session_ids`` and ``diff`` are JSON; ``diff`` is filled once the job reaches
``done`` and holds the per-block/per-note/per-slot comparison the UI renders
next to the source pool. ``new_memory_id`` is filled on success and is the
pool a person can switch a binding to, or leave alone to discard the result.
"""
from __future__ import annotations

from typing import Any

from common.migrations import execute_sql

_DDL = """
CREATE TABLE IF NOT EXISTS memory_consolidations (
    id              TEXT PRIMARY KEY,
    memory_id       TEXT NOT NULL,
    new_memory_id   TEXT,
    workspace       TEXT,
    status          TEXT NOT NULL DEFAULT 'queued',
    session_limit   INTEGER NOT NULL DEFAULT 10,
    session_ids     TEXT,
    diff            TEXT,
    summary         TEXT,
    error           TEXT,
    actor_kind      TEXT,
    actor_id        TEXT,
    trigger         TEXT NOT NULL DEFAULT 'manual',
    provider        TEXT,
    model           TEXT,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_consolidations_memory
    ON memory_consolidations(memory_id, created_at);
"""


def upgrade(conn: Any, dialect: str) -> None:
    execute_sql(conn, dialect, _DDL)
