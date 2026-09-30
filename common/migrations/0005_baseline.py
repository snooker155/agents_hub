"""
0005: the baseline. Every table as of the move to numbered migrations.

Runs in two steps: a pre-ledger database (one that ``common.db`` stamped by
hand, ``meta.schema_version`` 0 to 4) first gets the columns that used to be
added on startup (``legacy.upgrade_pre_ledger``), then ``baseline_schema.sql``
creates whatever is missing with ``CREATE TABLE IF NOT EXISTS``. A fresh
database skips straight to the second step.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from common.migrations import execute_sql
from common.migrations.legacy import upgrade_pre_ledger

SCHEMA_FILE = Path(__file__).resolve().with_name("baseline_schema.sql")


def upgrade(conn: Any, dialect: str) -> None:
    upgrade_pre_ledger(conn, dialect)
    execute_sql(conn, dialect, SCHEMA_FILE.read_text(encoding="utf-8"))
