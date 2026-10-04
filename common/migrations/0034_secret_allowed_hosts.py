"""
0034: hosts a secret may be sent to (common/secrets.py, environments/secret_egress.py).

A secret with ``allowed_hosts`` reaches a run as a placeholder; the egress
proxy puts the real value into a request only on its way to one of those
hosts. The column holds the hosts as a comma list, '' for a secret with no
restriction, which is every secret written before this migration.
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing


def upgrade(conn: Any, dialect: str) -> None:
    add_column_if_missing(conn, dialect, "secrets", "allowed_hosts", "TEXT NOT NULL DEFAULT ''")
