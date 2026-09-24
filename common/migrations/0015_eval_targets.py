"""
0015: evals of any run kind (section 0c of the September 2026 plan,
docs/evals.md).

Until now an eval set named one agent (``eval_sets.agent_id``) and every cell
of a sweep was one agent run. A set can now target a flow, a team, a loop or
a scenario as well, and a cell of a container kind records the leaf runs it
went through. This migration:

- adds ``eval_sets.target_kind`` and ``eval_sets.target_id``, backfilled from
  ``agent_id``: a set with an agent id targets that agent. ``agent_id`` stays
  as the compatibility alias and is kept equal to ``target_id`` for agent
  targets (evals/store.py);
- adds ``eval_results.target_kind`` (backfilled to ``agent``: every result
  written before this was an agent run) and ``eval_results.trajectory``, the
  JSON list of leaf runs or steps behind a container cell.

Configs are JSON on ``eval_runs.configs``, so a config's target needs no
column. ``ah db migrate`` applies this like any other version.
"""
from __future__ import annotations

from typing import Any

from common.migrations import add_column_if_missing


def upgrade(conn: Any, dialect: str) -> None:
    added_kind = add_column_if_missing(conn, dialect, "eval_sets", "target_kind", "TEXT")
    added_id = add_column_if_missing(conn, dialect, "eval_sets", "target_id", "TEXT")
    if added_kind or added_id:
        conn.execute(
            "UPDATE eval_sets SET target_kind = 'agent', target_id = agent_id "
            "WHERE target_id IS NULL AND agent_id IS NOT NULL AND agent_id <> ''")

    add_column_if_missing(conn, dialect, "eval_results", "trajectory", "TEXT")
    if add_column_if_missing(conn, dialect, "eval_results", "target_kind", "TEXT"):
        conn.execute("UPDATE eval_results SET target_kind = 'agent' WHERE target_kind IS NULL")
