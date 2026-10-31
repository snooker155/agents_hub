"""
0052: a request that a worker finish what it runs and exit (the Cluster page's
Stop, or the autoscaler taking one away).

Adds ``members.drain_requested_at``, nullable. The worker reads it on its
beat, stops claiming, and goes once nothing of its own is left running. See
``common/members.py`` ``request_drain`` and ``runtime/worker.py``.
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing


def upgrade(conn: Any, dialect: str) -> None:
    add_column_if_missing(conn, dialect, "members", "drain_requested_at", "TEXT")
