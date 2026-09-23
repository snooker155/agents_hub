"""
0013: one table for the runs of flows, loops, teams and scenarios (stage 0a
of the September 2026 plan, docs/sessions-and-runs.md).

Until now each kind kept its own run table (``flow_runs``, ``loop_runs``,
``team_runs``, ``sim_runs``) with its own spelling of the same columns, and
only agent runs had a heartbeat, a host, a checkpoint and a place in the
launch queue. Once every kind runs as a process with a lease, a heartbeat and
a checkpoint, the four tables are the same table with four names, and the
watchdog, the run groups page and a recursive stop want to read them as one.

This migration:

- creates ``entity_runs``: the common columns every kind has (who, where,
  which task and session, the parent run, status, pid, host, container,
  heartbeat, checkpoint, log file, cost, error, times) plus ``doc``, the
  whole record as JSON, where each kind keeps what is its own (a team's
  rounds and result, a scenario's scores and final state, a loop's
  position);
- moves every row of the four old tables into it (``starting``, the
  scenario's old first status, becomes ``pending``, the shared spelling of
  "no process yet"), then drops them;
- adds ``runs.parent_run_id``, so a node run, a member's turn and a role's
  decision name the container they belong to the same way, and a stop can
  walk the tree;
- adds ``views.owner_kind`` and ``views.owner_id``: a view is owned by the
  run that made it, whatever its kind, not only by an agent run. Existing
  rows with a ``run_id`` are owned by that run.

The per-kind adapters (flow/run_store.py, loops/store.py, teams/store.py,
playground/store.py) keep their public functions; only where they read and
write changes. ``ah db migrate`` applies this like any other version.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from common.migrations import (
    add_column_if_missing, execute_sql, table_columns, table_exists,
)

_TABLES = """
CREATE TABLE IF NOT EXISTS entity_runs (
    run_id          TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,     -- flow | loop | team | scenario
    entity_id       TEXT,              -- flow_id, loop_id, team_id, scenario_id
    workspace       TEXT,
    task_id         TEXT,
    session_id      TEXT,
    parent_run_id   TEXT,              -- the run this one runs inside, if any
    title           TEXT,
    status          TEXT,              -- common/run_status.py
    pid             INTEGER,
    host            TEXT,
    container_name  TEXT,
    execution_mode  TEXT,              -- local | docker
    heartbeat_at    TEXT,
    resume_attempts INTEGER,
    log_file        TEXT,
    total_cost      REAL,
    stop_reason     TEXT,
    error           TEXT,
    exit_code       INTEGER,
    created_at      TEXT,
    started_at      TEXT,
    finished_at     TEXT,
    checkpoint      TEXT,              -- JSON, what a resume starts from
    doc             TEXT               -- JSON, the whole record
);
CREATE INDEX IF NOT EXISTS idx_entity_runs_kind_entity ON entity_runs(kind, entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_runs_status      ON entity_runs(status);
CREATE INDEX IF NOT EXISTS idx_entity_runs_workspace   ON entity_runs(workspace);
CREATE INDEX IF NOT EXISTS idx_entity_runs_task        ON entity_runs(task_id);
CREATE INDEX IF NOT EXISTS idx_entity_runs_parent      ON entity_runs(parent_run_id);
CREATE INDEX IF NOT EXISTS idx_entity_runs_started     ON entity_runs(started_at);
"""

_ENTITY_COLUMNS = (
    "run_id", "kind", "entity_id", "workspace", "task_id", "session_id",
    "parent_run_id", "title", "status", "pid", "host", "container_name",
    "execution_mode", "heartbeat_at", "resume_attempts", "log_file",
    "total_cost", "stop_reason", "error", "exit_code", "created_at",
    "started_at", "finished_at", "checkpoint", "doc",
)

_INSERT = (
    f"INSERT INTO entity_runs ({', '.join(_ENTITY_COLUMNS)}) VALUES "
    f"({', '.join('?' * len(_ENTITY_COLUMNS))})"
)


def _loads(text: Any, default: Any) -> Any:
    if text is None or text == "":
        return default
    if isinstance(text, (dict, list)):
        return text
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return default
    return value if value is not None else default


def _cell(row: Any, column: str) -> Any:
    try:
        return row[column]
    except (KeyError, IndexError):
        return None


def _insert(conn: Any, rec: Dict[str, Any], *, kind: str, checkpoint: Any) -> None:
    """Write one record: the common columns from ``rec``, ``checkpoint`` in
    its own column, and everything else (``checkpoint`` excluded) in ``doc``."""
    doc = {k: v for k, v in rec.items() if k != "checkpoint"}
    values = []
    for col in _ENTITY_COLUMNS:
        if col == "kind":
            values.append(kind)
        elif col == "checkpoint":
            values.append(json.dumps(checkpoint) if checkpoint else None)
        elif col == "doc":
            values.append(json.dumps(doc, default=str))
        else:
            values.append(rec.get(col))
    conn.execute(_INSERT, values)


def _move_flow_runs(conn: Any, dialect: str) -> int:
    if not table_exists(conn, dialect, "flow_runs"):
        return 0
    n = 0
    for row in conn.execute("SELECT * FROM flow_runs").fetchall():
        doc = _loads(_cell(row, "doc"), {})
        rec: Dict[str, Any] = dict(doc) if isinstance(doc, dict) else {}
        for col in ("flow_run_id", "flow_id", "task_id", "session_id", "workspace",
                    "status", "pid", "started_at", "finished_at", "exit_code", "error"):
            if rec.get(col) is None and _cell(row, col) is not None:
                rec[col] = _cell(row, col)
        run_id = str(rec.get("flow_run_id") or _cell(row, "flow_run_id") or "")
        if not run_id:
            continue
        rec["run_id"] = run_id
        rec["entity_id"] = rec.get("flow_id")
        rec.setdefault("created_at", rec.get("started_at"))
        checkpoint = rec.get("checkpoint")
        _insert(conn, rec, kind="flow", checkpoint=checkpoint if isinstance(checkpoint, dict) else None)
        n += 1
    return n


def _move_loop_runs(conn: Any, dialect: str) -> int:
    if not table_exists(conn, dialect, "loop_runs"):
        return 0
    n = 0
    for row in conn.execute("SELECT * FROM loop_runs").fetchall():
        run_id = str(_cell(row, "loop_run_id") or "")
        if not run_id:
            continue
        position = _loads(_cell(row, "progress"), {})
        position = position if isinstance(position, dict) else {}
        rec: Dict[str, Any] = {
            col: _cell(row, col) for col in (
                "loop_run_id", "loop_id", "workspace", "status", "goal", "task_id",
                "session_id", "iterations_done", "best_score", "final_score",
                "stop_reason", "result", "error", "total_cost", "started_at",
                "finished_at")
        }
        rec["position"] = position
        rec["run_id"] = run_id
        rec["entity_id"] = rec.get("loop_id")
        rec["created_at"] = rec.get("started_at")
        rec["heartbeat_at"] = position.get("heartbeat_at")
        rec["resume_attempts"] = position.get("resume_attempts")
        rec["host"] = position.get("host")
        _insert(conn, rec, kind="loop", checkpoint=None)
        n += 1
    return n


def _move_team_runs(conn: Any, dialect: str) -> int:
    if not table_exists(conn, dialect, "team_runs"):
        return 0
    n = 0
    for row in conn.execute("SELECT * FROM team_runs").fetchall():
        run_id = str(_cell(row, "team_run_id") or "")
        if not run_id:
            continue
        rec: Dict[str, Any] = {
            col: _cell(row, col) for col in (
                "team_run_id", "team_id", "workspace", "mode", "status", "goal",
                "task_id", "session_id", "conversation_id", "rounds_done",
                "total_cost", "result", "stop_reason", "error", "started_at",
                "finished_at")
        }
        rec["run_id"] = run_id
        rec["entity_id"] = rec.get("team_id")
        rec["created_at"] = rec.get("started_at")
        _insert(conn, rec, kind="team", checkpoint=None)
        n += 1
    return n


def _move_sim_runs(conn: Any, dialect: str) -> int:
    if not table_exists(conn, dialect, "sim_runs"):
        return 0
    n = 0
    have = table_columns(conn, dialect, "sim_runs")
    for row in conn.execute("SELECT * FROM sim_runs").fetchall():
        run_id = str(_cell(row, "sim_run_id") or "")
        if not run_id:
            continue
        rec: Dict[str, Any] = {
            col: _cell(row, col) for col in (
                "sim_run_id", "scenario_id", "workspace", "environment", "status",
                "activation", "stop_reason", "ticks_done", "total_cost", "error",
                "started_at", "finished_at")
        }
        for col in ("scores", "final_state", "config", "story"):
            rec[col] = _loads(_cell(row, col), {}) if col in have else {}
        if rec.get("status") == "starting":
            rec["status"] = "pending"
        rec["run_id"] = run_id
        rec["entity_id"] = rec.get("scenario_id")
        rec["created_at"] = rec.get("started_at")
        _insert(conn, rec, kind="scenario", checkpoint=None)
        n += 1
    return n


def upgrade(conn: Any, dialect: str) -> None:
    execute_sql(conn, dialect, _TABLES)

    # Leaves name their container. Backfilled from the keys the node runs,
    # the member turns and the scenario decisions already carried in
    # ``extra``; done in Python because ``extra`` may be NULL, empty or not
    # JSON at all on an old row, which a JSON cast would refuse on Postgres.
    if add_column_if_missing(conn, dialect, "runs", "parent_run_id", "TEXT"):
        rows = conn.execute(
            "SELECT run_id, extra FROM runs WHERE extra IS NOT NULL AND ("
            "extra LIKE '%flow_run_id%' OR extra LIKE '%sim_run_id%' "
            "OR extra LIKE '%team_run_id%')").fetchall()
        for row in rows:
            extra = _loads(_cell(row, "extra"), {})
            if not isinstance(extra, dict):
                continue
            parent = extra.get("flow_run_id") or extra.get("sim_run_id") or extra.get("team_run_id")
            if parent:
                conn.execute("UPDATE runs SET parent_run_id = ? WHERE run_id = ?",
                             (str(parent), _cell(row, "run_id")))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_parent ON runs(parent_run_id)")

    # A view's owner is a (kind, id) pair; a view made by an agent run keeps
    # that run as its owner.
    added_kind = add_column_if_missing(conn, dialect, "views", "owner_kind", "TEXT")
    added_id = add_column_if_missing(conn, dialect, "views", "owner_id", "TEXT")
    if added_kind or added_id:
        conn.execute(
            "UPDATE views SET owner_kind = 'run', owner_id = run_id "
            "WHERE owner_id IS NULL AND run_id IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_views_owner ON views(owner_kind, owner_id)")

    # The four tables become one. A database this migration already ran on
    # (the ledger says so) never reaches here twice, and a fresh database has
    # the old tables empty from the baseline, so the moves are no-ops there.
    _move_flow_runs(conn, dialect)
    _move_loop_runs(conn, dialect)
    _move_team_runs(conn, dialect)
    _move_sim_runs(conn, dialect)
    for table in ("flow_runs", "loop_runs", "team_runs", "sim_runs"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")

