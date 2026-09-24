"""
0019: messages a person sends to a run while it works (steering, fourth
cycle stage 2, docs/steering.md).

One row per message. ``mode`` is ``inject`` (the agent loop takes it before
its next model call, ``agents/loop_ext/steering.py``) or ``interrupt`` (the
run is stopped and the message starts the next one, see
``dashboard/backend/routes/steering.py``). ``status`` follows the message:

- ``pending``: waiting for the run's next model call;
- ``delivered``: the loop took it; ``delivered_at`` and ``delivered_step``
  (how many tool results the run had by then) say when;
- ``expired``: the run ended before another model call, so nothing took it
  (a chat client sends these as its next turn);
- ``interrupted``: an interrupt; the run was stopped for it, and
  ``next_run_id`` names the run that carries it on when there is one;
- ``failed``: an interrupt whose stop or relaunch did not go through.

``seq`` keeps the order messages were written in, which a timestamp alone
cannot promise. The index on ``(run_id, delivered_at)`` is what makes the
check the loop does before every model call one indexed lookup.

Written in SQLite syntax and passed through ``execute_sql``, which rewrites
the few types Postgres spells differently (``INTEGER PRIMARY KEY
AUTOINCREMENT``, ``INTEGER``). ``ah db migrate`` applies this like any other
version.
"""
from __future__ import annotations

from typing import Any

from common.migrations import execute_sql

_DDL = """
CREATE TABLE IF NOT EXISTS run_steering (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id          TEXT NOT NULL UNIQUE,
    run_id          TEXT NOT NULL,
    body            TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'inject',
    author_id       TEXT,
    author_name     TEXT,
    created_at      TEXT NOT NULL,
    delivered_at    TEXT,
    delivered_step  INTEGER,
    status          TEXT NOT NULL DEFAULT 'pending',
    next_run_id     TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_steering_run ON run_steering(run_id, delivered_at);
"""


def upgrade(conn: Any, dialect: str) -> None:
    execute_sql(conn, dialect, _DDL)
