"""
0035: tool calls waiting for a person inside a chat turn (common/tool_approvals.py,
docs/hooks.md, "In chat").

A gated call in a task parks the task; a chat turn has no task, so the call
waits in the turn itself and a person answers it from the chat. One row per
call that waited. ``status`` follows it:

- ``pending``: the turn is waiting; ``expires_at`` says until when;
- ``approved`` / ``denied``: a person answered (``decided_by``, ``note``);
- ``expired``: nobody answered in time, so the call was refused;
- ``cancelled``: the run was stopped while the call waited.

``owner`` is the user who may answer besides an admin: the conversation's
owner when the turn started. The index on ``run_id`` serves the chat's
"what is this turn waiting on" read after a reload.

Written in SQLite syntax and passed through ``execute_sql``, which rewrites
what Postgres spells differently.
"""
from __future__ import annotations

from typing import Any

from common.migrations import execute_sql

_DDL = """
CREATE TABLE IF NOT EXISTS tool_approvals (
    approval_id      TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL,
    agent_id         TEXT,
    workspace        TEXT,
    conversation_id  TEXT,
    owner            TEXT,
    tool             TEXT NOT NULL,
    input            TEXT,
    reason           TEXT,
    fingerprint      TEXT,
    decided_by_rule  TEXT,
    hook             TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    note             TEXT,
    decided_by       TEXT,
    decided_by_name  TEXT,
    created_at       TEXT NOT NULL,
    expires_at       TEXT,
    decided_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_tool_approvals_run ON tool_approvals(run_id, status);
"""


def upgrade(conn: Any, dialect: str) -> None:
    execute_sql(conn, dialect, _DDL)
