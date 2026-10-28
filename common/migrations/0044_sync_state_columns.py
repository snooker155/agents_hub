"""
0044: the columns sync_state gained while 0043 was still unreleased.

0043 (hubsync/, docs/hub-sync.md) was changed in place before any release to
add ``failed_version``, ``changed_hash`` and ``changed_at`` (hubsync/state.py
says what each is for) and an index by local id. A database that ran the first
draft of 0043, a development state directory, has the table without them, and
a migration that already ran is never run again; this one adds whatever is
missing and is a no-op everywhere else.
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing


def upgrade(conn: Any, dialect: str) -> None:
    add_column_if_missing(conn, dialect, "sync_state", "failed_version", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(conn, dialect, "sync_state", "changed_hash", "TEXT")
    add_column_if_missing(conn, dialect, "sync_state", "changed_at", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_state_local "
                 "ON sync_state(owner_user, space, kind, local_id)")
