"""
0016: usage of the hub as an OpenAI-compatible model server (feature 5C of
the September 2026 plan, docs/hub-as-provider.md).

Every call to ``POST /v1/chat/completions`` writes one row to
``serving_usage``: who called (the principal's user id, kind and name, and
the personal API key's id when a key was presented), which catalog model
answered, the token counts, how long it took, whether it streamed, and
whether it succeeded. ``common/serving.py`` writes and aggregates it for the
Models page. Tokens only, no prices: what a call cost is the provider's
business, and the catalog's prices already drive the cost pages for runs.

``estimated`` is 1 when the provider reported no usage and the counts come
from the four characters per token rule instead.

Written in SQLite syntax and passed through ``execute_sql``, which rewrites
the few types Postgres spells differently (``INTEGER PRIMARY KEY
AUTOINCREMENT``, ``INTEGER``). ``ah db migrate`` applies this like any other
version.
"""
from __future__ import annotations

from typing import Any

from common.migrations import execute_sql

_DDL = """
CREATE TABLE IF NOT EXISTS serving_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    at                TEXT NOT NULL,
    user_id           TEXT,
    actor_kind        TEXT,
    actor_name        TEXT,
    key_id            TEXT,
    provider          TEXT NOT NULL,
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    duration_ms       INTEGER NOT NULL DEFAULT 0,
    stream            INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'ok',
    error             TEXT,
    estimated         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_serving_usage_at ON serving_usage(at);
CREATE INDEX IF NOT EXISTS idx_serving_usage_model ON serving_usage(provider, model);
"""


def upgrade(conn: Any, dialect: str) -> None:
    execute_sql(conn, dialect, _DDL)
