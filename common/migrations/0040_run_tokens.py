"""
0040: run tokens (common/run_tokens.py, docs/identity.md "Run tokens").

A run's own process (an agent run, a flow, a loop, a resident instance's
carrier) calls back into the API to relay its events and, in a container, to
write its run state. It used to present the shared API token or the admin
service credential for that, which reach the whole API. Now each launch gets a
token of its own that reaches only those relay routes (common/auth.py
``RELAY_ROUTES``).

``run_tokens``
    One row per launch. ``token_hash`` is a SHA-256 of the token, never the
    token. ``run_id``, ``session_id``, ``instance_id`` and ``workspace`` say
    what it was minted for (``run_id`` is how closing the run retires it).
    ``expires_at`` slides forward while the process keeps using the token, so
    a long loop does not lose its relays; ``retired_at`` stops the sliding
    (the run closed) and pulls ``expires_at`` in to a short grace period for
    the last events.

Written in SQLite syntax and passed through ``execute_sql``, which rewrites
what Postgres spells differently.
"""
from __future__ import annotations

from typing import Any

from common.migrations import execute_sql

_DDL = """
CREATE TABLE IF NOT EXISTS run_tokens (
    token_hash   TEXT PRIMARY KEY,
    token_id     TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'run',
    run_id       TEXT,
    session_id   TEXT,
    instance_id  TEXT,
    workspace    TEXT,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    retired_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_tokens_run ON run_tokens(run_id);
CREATE INDEX IF NOT EXISTS idx_run_tokens_instance ON run_tokens(instance_id);
CREATE INDEX IF NOT EXISTS idx_run_tokens_expires ON run_tokens(expires_at);
"""


def upgrade(conn: Any, dialect: str) -> None:
    execute_sql(conn, dialect, _DDL)
