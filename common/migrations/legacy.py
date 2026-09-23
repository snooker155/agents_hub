"""
Bringing a pre-ledger database up to the baseline.

Before the migration ledger existed, ``common.db`` shipped every new column
as an entry in an ``_ADDED_COLUMNS`` map, applied with ``ALTER TABLE`` on
startup, plus a backfill statement to fill it from data already in the row.
A database from that era has any subset of those columns depending on which
build last opened it. ``0005_baseline`` calls :func:`upgrade_pre_ledger`
before creating anything, so every such table ends up with the columns the
baseline expects; the baseline's ``CREATE TABLE IF NOT EXISTS`` then skips the
tables that exist and creates the rest.

This module is frozen: it describes the columns that were ever added that
way, and nothing new belongs here. New columns are numbered migrations.
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing, table_exists

# table -> {column: declaration}, exactly the pre-ledger _ADDED_COLUMNS map,
# plus loop_runs.progress, which loops/store.py used to add on its own.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "runs": {"instance_id": "TEXT", "cached_prompt_tokens": "INTEGER"},
    "sessions": {"workspace": "TEXT", "created_at": "TEXT", "is_flow": "INTEGER"},
    "sim_runs": {"activation": "TEXT", "stop_reason": "TEXT", "config": "TEXT",
                 "story": "TEXT"},
    "sim_ticks": {"idle": "TEXT"},
    "eval_results": {"attempt": "INTEGER"},
    "tasks": {"created_by_user": "TEXT"},
    "chats": {"owner": "TEXT"},
    "loop_runs": {"progress": "TEXT"},
}


def _backfill(table: str, dialect: str) -> list[str]:
    """Statements that fill a freshly added column from data already in the
    row, so an upgraded database is indistinguishable from a fresh one."""
    from common.db import json_text, json_truthy
    if table == "sessions":
        return [
            f"UPDATE sessions SET workspace = {json_text('doc', 'workspace')} "
            "WHERE workspace IS NULL",
            f"UPDATE sessions SET created_at = {json_text('doc', 'created_at')} "
            "WHERE created_at IS NULL",
            f"UPDATE sessions SET is_flow = CASE WHEN {json_truthy('doc', 'is_flow')} "
            "THEN 1 ELSE 0 END WHERE is_flow IS NULL",
        ]
    if table == "eval_results":
        return ["UPDATE eval_results SET attempt = 1 WHERE attempt IS NULL"]
    if table == "tasks":
        return ["UPDATE tasks SET created_by_user = 'local' WHERE created_by_user IS NULL"]
    if table == "chats":
        return ["UPDATE chats SET owner = 'local' WHERE owner IS NULL"]
    return []


def upgrade_pre_ledger(conn: Any, dialect: str) -> list[str]:
    """Add every post-hoc column a pre-ledger table may be missing, then run
    that table's backfill. Tables that do not exist are skipped: the baseline
    creates them in full. Returns ``table.column`` for each column added."""
    added: list[str] = []
    for table, columns in ADDED_COLUMNS.items():
        if not table_exists(conn, dialect, table):
            continue
        table_touched = False
        for column, decl in columns.items():
            if add_column_if_missing(conn, dialect, table, column, decl):
                added.append(f"{table}.{column}")
                table_touched = True
        if table_touched:
            for statement in _backfill(table, dialect):
                conn.execute(statement)
    return added
