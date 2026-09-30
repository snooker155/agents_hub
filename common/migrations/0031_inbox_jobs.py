"""
0031: jobs in the mailbox (runtime/jobs.py, services/jobs.py, docs/services.md).

A runner replica executes more than chat turns for the backend: an eval
case, a replay, a task decomposition, a project graph, a playground world or
scenario, the agent part of an entity chat. Each is a ``job`` message in its
mailbox whose answer is not a run's reply but a result document, so the
mailbox row gains ``result`` (JSON) and ``finished_at``.
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing


def upgrade(conn: Any, dialect: str) -> None:
    add_column_if_missing(conn, dialect, "instance_inbox", "result", "TEXT")
    add_column_if_missing(conn, dialect, "instance_inbox", "finished_at", "TEXT")
