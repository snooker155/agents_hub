"""
Migration 0013 folds ``flow_runs``, ``loop_runs``, ``team_runs`` and
``sim_runs`` into ``entity_runs`` (common/migrations/0013_entity_runs.py).

A database written by the previous build has rows in all four tables, each
with its own spelling of the common fields. Opening it with this build must
move every row across with its kind, its ids under both names, its
kind-specific fields in the document, the flow's checkpoint in the checkpoint
column, the loop's heartbeat and attempt counter lifted out of its position,
the scenario's ``starting`` read as ``pending``, and then drop the old tables.
The same pass gives ``runs`` a ``parent_run_id`` backfilled from the keys node
runs and decisions already carried, and ``views`` an owner.
"""
from __future__ import annotations

import json
import sqlite3
import threading

import pytest

import common.db as db
from common import entity_runs, migrations

pytestmark = pytest.mark.sqlite_only


def _pre_0013_database(path):
    """A SQLite file at schema version 12, with one run of each kind."""
    raw = sqlite3.connect(str(path))
    raw.row_factory = sqlite3.Row
    raw.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    migrations.applied_versions(raw, "sqlite")   # creates the ledger
    for mg in migrations.select_for("sqlite"):
        if mg.version >= 13:
            break
        migrations.apply_one(raw, "sqlite", mg)
    raw.execute(
        "INSERT INTO flow_runs (flow_run_id, flow_id, task_id, workspace, status, pid, doc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("fr-1", "flow-a", "task-1", "ws", "running", 4242, json.dumps({
            "flow_run_id": "fr-1", "flow_id": "flow-a", "task_id": "task-1",
            "workspace": "ws", "status": "running", "pid": 4242, "title": "nightly",
            "heartbeat_at": "2026-09-23T10:00:00+00:00", "host": "box",
            "checkpoint": {"done": [{"node_id": "a"}], "state": {}},
            "resume_attempts": 1,
        })))
    raw.execute(
        "INSERT INTO loop_runs (loop_run_id, loop_id, workspace, status, goal, iterations_done, "
        "best_score, total_cost, started_at, progress) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("lr-1", "loop-a", "ws", "running", "polish it", 2, 70.0, 0.5,
         "2026-09-23T09:00:00+00:00", json.dumps({
             "iterations_done": 2, "best_score": 70.0, "heartbeat_at": "2026-09-23T09:30:00+00:00",
             "resume_attempts": 1, "host": "box", "history": [{"iteration": 1, "score": 50}],
         })))
    raw.execute(
        "INSERT INTO team_runs (team_run_id, team_id, workspace, mode, status, goal, "
        "conversation_id, rounds_done, result, started_at, finished_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("tr-1", "team-a", "ws", "centralized", "completed", "ship it", "conv-1", 3,
         "shipped", "2026-09-22T09:00:00+00:00", "2026-09-22T09:30:00+00:00"))
    raw.execute(
        "INSERT INTO sim_runs (sim_run_id, scenario_id, workspace, environment, status, "
        "activation, ticks_done, total_cost, scores, final_state, config, story, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sim-1", "scn-a", "ws", "market", "starting", "synchronous", 0, 0.0,
         json.dumps({"pnl": 1}), json.dumps({"price": 100}), json.dumps({"max_ticks": 5}),
         json.dumps({"title": "A day"}), "2026-09-23T11:00:00+00:00"))
    raw.execute(
        "INSERT INTO runs (run_id, agent_id, status, extra) VALUES (?, ?, ?, ?)",
        ("node-1", "writer", "completed", json.dumps({"flow_run_id": "fr-1", "flow_node_label": "draft"})))
    raw.execute(
        "INSERT INTO runs (run_id, agent_id, status, extra) VALUES (?, ?, ?, ?)",
        ("dec-1", "trader", "completed", json.dumps({"sim_run_id": "sim-1", "tick": 1})))
    raw.execute(
        "INSERT INTO runs (run_id, agent_id, status, extra) VALUES (?, ?, ?, ?)",
        ("lone-1", "writer", "completed", "not json at all"))
    raw.execute(
        "INSERT INTO views (view_id, workspace, run_id, kind, title) VALUES (?, ?, ?, ?, ?)",
        ("v-1", "ws", "node-1", "table", "numbers"))
    raw.execute("INSERT INTO meta VALUES ('json_migrated', '{}')")
    raw.execute("INSERT INTO meta VALUES ('flow_runs_migrated', '{}')")
    raw.commit()
    raw.close()


def _open(reopen_db, path):
    reopen_db(path)
    return db.get_conn()


def test_the_four_run_tables_become_one(reopen_db, tmp_path):
    path = tmp_path / "old.db"
    _pre_0013_database(path)
    conn = _open(reopen_db, path)
    try:
        assert 13 in migrations.applied_versions(conn, "sqlite")
        for table in ("flow_runs", "loop_runs", "team_runs", "sim_runs"):
            assert not migrations.table_exists(conn, "sqlite", table)

        flow = entity_runs.get("fr-1")
        assert flow["kind"] == "flow"
        assert flow["flow_run_id"] == "fr-1" and flow["flow_id"] == "flow-a"
        assert flow["entity_id"] == "flow-a" and flow["task_id"] == "task-1"
        assert flow["title"] == "nightly" and flow["pid"] == 4242
        assert flow["heartbeat_at"] == "2026-09-23T10:00:00+00:00"
        assert flow["host"] == "box" and flow["resume_attempts"] == 1
        assert flow["checkpoint"] == {"done": [{"node_id": "a"}], "state": {}}
        assert entity_runs.load_checkpoint("fr-1") == flow["checkpoint"]

        loop = entity_runs.get("lr-1")
        assert loop["kind"] == "loop" and loop["loop_id"] == "loop-a"
        assert loop["iterations_done"] == 2 and loop["best_score"] == 70.0
        assert loop["heartbeat_at"] == "2026-09-23T09:30:00+00:00"
        assert loop["resume_attempts"] == 1 and loop["host"] == "box"
        assert loop["position"]["history"] == [{"iteration": 1, "score": 50}]
        assert loop["created_at"] == "2026-09-23T09:00:00+00:00"

        team = entity_runs.get("tr-1")
        assert team["kind"] == "team" and team["team_id"] == "team-a"
        assert team["status"] == "completed" and team["rounds_done"] == 3
        assert team["conversation_id"] == "conv-1" and team["result"] == "shipped"

        sim = entity_runs.get("sim-1")
        assert sim["kind"] == "scenario" and sim["scenario_id"] == "scn-a"
        assert sim["status"] == "pending"          # was "starting"
        assert sim["environment"] == "market"
        assert sim["scores"] == {"pnl": 1} and sim["final_state"] == {"price": 100}
        assert sim["config"] == {"max_ticks": 5} and sim["story"] == {"title": "A day"}
    finally:
        db._schema_ready = False
        db._local = threading.local()


def test_the_adapters_read_the_moved_rows(reopen_db, tmp_path):
    path = tmp_path / "old.db"
    _pre_0013_database(path)
    _open(reopen_db, path)
    try:
        from flow import run_store
        from loops import store as loop_store
        from playground import store as sim_store
        from teams import store as team_store

        assert run_store.get_flow_run("fr-1")["status"] == "running"
        assert [r["flow_run_id"] for r in run_store.load_flow_runs()] == ["fr-1"]
        loop = loop_store.get_run("lr-1")
        assert loop.iterations_done == 2 and loop.resume_attempts == 1
        assert loop.position["heartbeat_at"] == "2026-09-23T09:30:00+00:00"
        team = team_store.get_run("tr-1")
        assert team.status == "completed" and team.rounds_done == 3
        sim = sim_store.get_sim_run("sim-1")
        assert sim.status == "pending" and sim.scores == {"pnl": 1}
        assert sim_store.get_story("sim-1") == {"title": "A day"}
        assert sim_store.latest_runs_by_scenario(["scn-a"])["scn-a"].sim_run_id == "sim-1"
    finally:
        db._schema_ready = False
        db._local = threading.local()


def test_leaves_and_views_learn_their_owner(reopen_db, tmp_path):
    path = tmp_path / "old.db"
    _pre_0013_database(path)
    conn = _open(reopen_db, path)
    try:
        rows = {r["run_id"]: r["parent_run_id"] for r in
                conn.execute("SELECT run_id, parent_run_id FROM runs").fetchall()}
        assert rows == {"node-1": "fr-1", "dec-1": "sim-1", "lone-1": None}
        assert entity_runs.leaf_children("fr-1") == ["node-1"]
        view = conn.execute("SELECT owner_kind, owner_id FROM views WHERE view_id = 'v-1'").fetchone()
        assert (view["owner_kind"], view["owner_id"]) == ("run", "node-1")
    finally:
        db._schema_ready = False
        db._local = threading.local()


def test_a_fresh_database_has_only_the_shared_table():
    conn = db.get_conn()
    assert migrations.table_exists(conn, db.dialect(), "entity_runs")
    for table in ("flow_runs", "loop_runs", "team_runs", "sim_runs"):
        assert not migrations.table_exists(conn, db.dialect(), table)
    assert "parent_run_id" in migrations.table_columns(conn, db.dialect(), "runs")
    assert {"owner_kind", "owner_id"} <= migrations.table_columns(conn, db.dialect(), "views")
