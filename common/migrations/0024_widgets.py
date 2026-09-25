"""
0024: the embeddable chat widget (fourth-cycle stage 3, stream A,
docs/widget.md).

Three tables, all owned by ``widgets/store.py``:

``widgets``
    One row per widget: which workspace and agent it talks to, whose
    principal it acts as (``owner_id``), the publishable key a site embeds
    (``public_key``, stored as is: it is public by design and shown in the
    snippet), the exact origins allowed to embed it, the texts and the look
    it shows, and its limits (JSON, see ``widgets.models.WidgetLimits``).

``widget_threads``
    A visitor's conversations. ``visitor_id`` is the anonymous id the server
    minted into the visitor's signed token; ``agent_id`` is the agent that
    answers the thread now (it moves on a handoff). ``preview`` marks a thread
    opened from the dashboard's live preview; ``visitor_deleted_at`` is set
    when the visitor deleted the thread in the widget: it disappears for them
    and stays visible to the owner, marked.

``widget_messages``
    The turns of a thread, kept server side so a visitor's other tab and the
    owner read the same transcript. ``tokens`` is the turn's usage, summed
    per widget per UTC day for the widget's daily token cap.

Written in SQLite syntax and passed through ``execute_sql``, which rewrites
``INTEGER PRIMARY KEY AUTOINCREMENT`` and ``INTEGER`` for Postgres. ``ah db
migrate`` applies this like any other version.
"""
from __future__ import annotations

from typing import Any

from common.migrations import execute_sql

_DDL = """
CREATE TABLE IF NOT EXISTS widgets (
    widget_id        TEXT PRIMARY KEY,
    workspace        TEXT NOT NULL,
    name             TEXT NOT NULL,
    agent_id         TEXT NOT NULL,
    owner_id         TEXT NOT NULL,
    public_key       TEXT NOT NULL,
    allowed_origins  TEXT NOT NULL DEFAULT '[]',
    enabled          INTEGER NOT NULL DEFAULT 1,
    title            TEXT NOT NULL DEFAULT '',
    greeting         TEXT NOT NULL DEFAULT '',
    placeholder      TEXT NOT NULL DEFAULT '',
    accent           TEXT NOT NULL DEFAULT 'navy',
    language         TEXT NOT NULL DEFAULT 'auto',
    limits           TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_widgets_workspace ON widgets(workspace);
CREATE UNIQUE INDEX IF NOT EXISTS idx_widgets_public_key ON widgets(public_key);

CREATE TABLE IF NOT EXISTS widget_threads (
    thread_id           TEXT PRIMARY KEY,
    widget_id           TEXT NOT NULL,
    visitor_id          TEXT NOT NULL,
    title               TEXT NOT NULL DEFAULT '',
    agent_id            TEXT NOT NULL,
    preview             INTEGER NOT NULL DEFAULT 0,
    visitor_deleted_at  TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_widget_threads_visitor
    ON widget_threads(widget_id, visitor_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_widget_threads_widget
    ON widget_threads(widget_id, updated_at);

CREATE TABLE IF NOT EXISTS widget_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id   TEXT NOT NULL,
    thread_id    TEXT NOT NULL,
    widget_id    TEXT NOT NULL,
    role         TEXT NOT NULL,
    text         TEXT NOT NULL DEFAULT '',
    attachments  TEXT NOT NULL DEFAULT '[]',
    run_id       TEXT,
    agent_id     TEXT,
    citations    TEXT NOT NULL DEFAULT '[]',
    handoff      TEXT,
    status       TEXT NOT NULL DEFAULT 'ok',
    tokens       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_widget_messages_id ON widget_messages(message_id);
CREATE INDEX IF NOT EXISTS idx_widget_messages_thread ON widget_messages(thread_id, id);
CREATE INDEX IF NOT EXISTS idx_widget_messages_usage ON widget_messages(widget_id, created_at);
"""


def upgrade(conn: Any, dialect: str) -> None:
    execute_sql(conn, dialect, _DDL)
