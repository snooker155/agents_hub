"""
0021: workspace files, a file uploaded once and referenced by id everywhere
(fourth-cycle stage 3, files as a workspace object).

``workspace_files`` is the catalogue ``files/service.py`` keeps: one row per
file, keyed by ``file_id`` (``file_<16 hex>``). The content itself is not in
the database: it lives under the state root at ``storage_key``
(``files/<workspace>/<file_id>/<safe name>``) and is mirrored to the object
store by ``common/blobs.py``, so a host that did not receive the upload can
still read it. ``sha256`` lets an upload of the same bytes into the same
workspace return the record that already exists instead of a second copy.
``deleted_at`` marks a deleted file: the row stays as a tombstone, so a chat
turn, a citation or an audit row that names the id can still say what it was,
while the content is removed.

``workspace_file_uses`` is the part of "where is this file used" that cannot
be read back from the objects themselves: a chat turn that attached the file
(by id, or by uploading it with "store in workspace"). Memory pools, tasks and
eval cases carry the id on their own records and are scanned instead, so a
reference removed there never leaves a stale row here.

Written in SQLite syntax and passed through ``execute_sql``, which rewrites
``INTEGER PRIMARY KEY AUTOINCREMENT`` and ``INTEGER`` for Postgres.
"""
from __future__ import annotations

from typing import Any

from common.migrations import execute_sql

_DDL = """
CREATE TABLE IF NOT EXISTS workspace_files (
    file_id      TEXT PRIMARY KEY,
    workspace    TEXT NOT NULL,
    name         TEXT NOT NULL,
    mime_type    TEXT,
    size         INTEGER NOT NULL DEFAULT 0,
    sha256       TEXT NOT NULL,
    storage_key  TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'upload',
    created_by   TEXT,
    created_at   TEXT NOT NULL,
    deleted_at   TEXT,
    meta         TEXT
);
CREATE INDEX IF NOT EXISTS idx_workspace_files_ws
    ON workspace_files(workspace, created_at);
CREATE INDEX IF NOT EXISTS idx_workspace_files_sha
    ON workspace_files(workspace, sha256);
CREATE TABLE IF NOT EXISTS workspace_file_uses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id      TEXT NOT NULL,
    kind         TEXT NOT NULL,
    ref_id       TEXT NOT NULL,
    label        TEXT,
    at           TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_workspace_file_uses_ref
    ON workspace_file_uses(file_id, kind, ref_id);
"""


def upgrade(conn: Any, dialect: str) -> None:
    execute_sql(conn, dialect, _DDL)
