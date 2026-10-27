"""
0050: a tool call that waits in a team run names its team run
(common/tool_approvals.py, docs/hooks.md, "In a background flow, loop or team").

The same link as ``flow_run_id`` (migration 0049), for a member's turn: the
team page and the chat that started the run know the team run, not the
member's own run, and read the waiting calls by it.
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing, execute_sql, table_exists


def upgrade(conn: Any, dialect: str) -> None:
    if not table_exists(conn, dialect, "tool_approvals"):
        return
    add_column_if_missing(conn, dialect, "tool_approvals", "team_run_id", "TEXT")
    execute_sql(conn, dialect, "CREATE INDEX IF NOT EXISTS idx_tool_approvals_team_run "
                               "ON tool_approvals(team_run_id, status);")
