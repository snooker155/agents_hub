"""
0053: how busy the machine under each member is and the agent containers on
the Docker daemon it uses (``common/host_metrics.py``).

Adds ``members.metrics``, nullable JSON text, refreshed with every beat: the
Cluster page's Machines card and the container columns read it.
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing


def upgrade(conn: Any, dialect: str) -> None:
    add_column_if_missing(conn, dialect, "members", "metrics", "TEXT")
