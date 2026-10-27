"""
0049: a tool call that waits in a background flow or loop names its flow run
(common/tool_approvals.py, docs/hooks.md, "In a background flow, loop or team").

A call held in a chat turn is found by its run: the chat knows the turn. A
call held in a flow node that runs in the background is answered from the
flow run's page, the loop's page or the chat that started the run, and those
know the flow run, not the node's own run. ``flow_run_id`` is that link, and
its index serves the "what is this run waiting on" read.
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing, execute_sql, table_exists


def upgrade(conn: Any, dialect: str) -> None:
    if not table_exists(conn, dialect, "tool_approvals"):
        return
    add_column_if_missing(conn, dialect, "tool_approvals", "flow_run_id", "TEXT")
    execute_sql(conn, dialect, "CREATE INDEX IF NOT EXISTS idx_tool_approvals_flow_run "
                               "ON tool_approvals(flow_run_id, status);")
