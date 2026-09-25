"""
0023: eval runs through provider batch APIs (fourth-cycle stage 3).

``eval_runs.mode`` says how a run executes its cells: ``live`` (NULL on every
run recorded before this) or ``batch``. A batch run sends its agent cells, and
then their judge calls, to the provider's batch API at half the price and
finishes when the provider does, within 24 hours.

``eval_batches`` has one row per provider batch a run submitted
(``evals/batch.py`` is its only reader and writer): ``phase`` is ``target``
(the cells' first model call) or ``judge`` (their llm_judge / rubric calls),
``provider``/``base_url``/``model`` say where it went (never the key, which
is resolved again when the row is polled), ``provider_batch_id`` is the
provider's id, ``items`` maps each request's ``custom_id`` to the cell it
belongs to and whether it has been recorded, and ``status`` moves
``submitted`` → ``processing`` → ``done`` (or ``failed`` / ``cancelled``).
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing, execute_sql

_DDL = """
CREATE TABLE IF NOT EXISTS eval_batches (
    batch_row_id       TEXT PRIMARY KEY,
    eval_run_id        TEXT NOT NULL,
    phase              TEXT NOT NULL,
    provider           TEXT NOT NULL,
    base_url           TEXT,
    model              TEXT,
    agent_id           TEXT,
    provider_batch_id  TEXT,
    status             TEXT NOT NULL,
    items              TEXT NOT NULL,
    counts             TEXT,
    error              TEXT,
    submitted_at       TEXT NOT NULL,
    checked_at         TEXT,
    finished_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_eval_batches_run ON eval_batches(eval_run_id);
CREATE INDEX IF NOT EXISTS idx_eval_batches_status ON eval_batches(status);
"""


def upgrade(conn: Any, dialect: str) -> None:
    execute_sql(conn, dialect, _DDL)
    add_column_if_missing(conn, dialect, "eval_runs", "mode", "TEXT")
