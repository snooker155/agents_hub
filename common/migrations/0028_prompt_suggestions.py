"""
0028: prompt suggestions from failed eval cases (evals/prompt_suggest.py),
and the eval set flag that triggers one automatically.

``prompt_suggestions`` has one row per suggestion: which eval run and agent
it came from, the instructions.md it read and the one it proposed, a
rationale tied to the failed case ids, and its lifecycle (``pending`` until
an admin applies or dismisses it). ``suggest_run_id`` is the model call that
produced it, recorded as a run of its own (agent ``prompt_optimizer``,
channel ``eval``) so its cost is not folded into any cell's own cost.
``applied_run_id`` is the eval run started after Apply to compare before and
after, when one was started.

``eval_sets.suggest_on_failure`` (default off) makes a finished sweep with
failed cases on an agent target build a suggestion on its own.
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing, execute_sql

_DDL = """
CREATE TABLE IF NOT EXISTS prompt_suggestions (
    suggestion_id     TEXT PRIMARY KEY,
    eval_run_id       TEXT NOT NULL,
    eval_set_id       TEXT NOT NULL,
    agent_id          TEXT NOT NULL,
    workspace         TEXT,
    status            TEXT NOT NULL DEFAULT 'pending',
    old_instructions  TEXT NOT NULL DEFAULT '',
    new_instructions  TEXT NOT NULL DEFAULT '',
    rationale         TEXT,
    case_ids          TEXT,
    suggest_run_id    TEXT,
    applied_run_id    TEXT,
    cost_usd          REAL NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    decided_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_prompt_suggestions_eval_run ON prompt_suggestions(eval_run_id);
CREATE INDEX IF NOT EXISTS idx_prompt_suggestions_agent ON prompt_suggestions(agent_id);
"""


def upgrade(conn: Any, dialect: str) -> None:
    execute_sql(conn, dialect, _DDL)
    add_column_if_missing(conn, dialect, "eval_sets", "suggest_on_failure", "INTEGER NOT NULL DEFAULT 0")
