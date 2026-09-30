"""
0018: a fencing token on every service lease (docs/scaling.md, "Leases").

A lease holder that stalls past its TTL (a long GC pause, a slow tick) can be
superseded by another replica and then wake up and keep writing as if it
still held the role: nothing it wrote said which incarnation of the lease it
was acting under. ``version`` is that incarnation. ``common/leases.py`` bumps
it on every insert and every takeover (never on a renewal), the holder
remembers the value it got, and the loops check the row's owner, version and
expiry inside the same exclusive transaction as their write
(``leases.fenced``), so a stale holder finds out before it writes, not after.

Existing rows start at 0; the next takeover moves them to 1.
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing, table_exists


def upgrade(conn: Any, dialect: str) -> None:
    if table_exists(conn, dialect, "service_leases"):
        add_column_if_missing(conn, dialect, "service_leases", "version",
                              "INTEGER NOT NULL DEFAULT 0")
