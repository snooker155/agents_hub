"""
0026: the MCP allowlist catalog (fourth-cycle stage 4, stream B,
docs/registry.md).

``mcp_catalog`` is the hub-wide list of MCP servers an admin has vetted: id,
name, description, the connection shape (``transport`` plus ``command``/
``args`` for stdio or ``url`` for the others — whichever identifies the
server), the operator's capability claim (JSON, the same three flags as a
workspace-attached server), who asked for it (``owner_user``), its review
``status`` (``requested`` | ``approved`` | ``blocked``), an optional note and
who reviewed it. Matching a workspace-attached server to a catalog entry
(same id, same command+args or url) is ``mcp_client.catalog.matches``.

The agent registry itself (owner, review status) needs no table: those fields
live on the existing ``AgentSpec`` record in the ``agents`` document
collection (agents/registry.py), the same place every other agent field does.

Written in SQLite syntax and passed through ``execute_sql``, which rewrites
``INTEGER PRIMARY KEY AUTOINCREMENT`` and ``INTEGER`` for Postgres. ``ah db
migrate`` applies this like any other version.
"""
from __future__ import annotations

from typing import Any

from common.migrations import execute_sql

_DDL = """
CREATE TABLE IF NOT EXISTS mcp_catalog (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    transport     TEXT NOT NULL DEFAULT 'stdio',
    command       TEXT NOT NULL DEFAULT '',
    args          TEXT NOT NULL DEFAULT '[]',
    url           TEXT NOT NULL DEFAULT '',
    capabilities  TEXT NOT NULL DEFAULT '{}',
    owner_user    TEXT,
    status        TEXT NOT NULL DEFAULT 'requested',
    note          TEXT,
    reviewed_by   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    reviewed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_mcp_catalog_status ON mcp_catalog(status);
"""


def upgrade(conn: Any, dialect: str) -> None:
    execute_sql(conn, dialect, _DDL)
