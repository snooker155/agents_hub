"""0044 adds the sync_state columns 0043 gained before release to a database
that ran 0043's first draft (a development state dir), and does nothing on a
database that already has them."""
from __future__ import annotations

import importlib

from common import db
from common.migrations import table_columns


def test_0044_adds_the_late_columns_to_a_first_draft_table():
    mod = importlib.import_module("common.migrations.0044_sync_state_columns")
    with db.transaction() as conn:
        conn.execute("DROP TABLE sync_state")
        conn.execute("CREATE TABLE sync_state (owner_user TEXT NOT NULL, space TEXT NOT NULL, kind TEXT NOT NULL, "
                     "record_id TEXT NOT NULL, local_id TEXT, hash TEXT, version INTEGER NOT NULL DEFAULT 0, "
                     "modified_at TEXT, synced_at TEXT, PRIMARY KEY (owner_user, space, kind, record_id))")
        mod.upgrade(conn, db.dialect())
        cols = set(table_columns(conn, db.dialect(), "sync_state"))
        assert {"failed_version", "changed_hash", "changed_at"} <= cols
        mod.upgrade(conn, db.dialect())  # a second run changes nothing
