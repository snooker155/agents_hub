"""The database core's two-backend seams (common/db.py, common/migrations/,
common/db_transfer.py).

Everything here runs on whichever backend the suite is on; the pieces that
are inherently about one dialect (the placeholder adapter, the DDL rewrite)
are pure functions and run everywhere.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import common.db as db
from common import migrations
from common import db_transfer


# ── SQL adaptation for Postgres (pure) ───────────────────────────────────────

def test_placeholders_become_percent_s_outside_string_literals():
    out = db._adapt_sql_for_pg("SELECT * FROM t WHERE a = ? AND b = 'x?y' AND c LIKE ?", True)
    assert out == "SELECT * FROM t WHERE a = %s AND b = 'x?y' AND c LIKE %s"


def test_percent_is_escaped_only_when_parameters_are_passed():
    sql = "SELECT * FROM t WHERE a LIKE 'x%' AND b = ?"
    assert db._adapt_sql_for_pg(sql, True) == "SELECT * FROM t WHERE a LIKE 'x%%' AND b = %s"
    sql_no_params = "SELECT * FROM t WHERE a LIKE 'x%'"
    assert db._adapt_sql_for_pg(sql_no_params, False) == sql_no_params


def test_boolean_parameters_become_integers():
    assert db._adapt_params_for_pg((True, False, 3, "s", None)) == [1, 0, 3, "s", None]
    assert db._adapt_params_for_pg({"a": True}) == {"a": 1}


def test_upsert_sql_updates_every_non_key_column():
    sql = db.upsert_sql("meta", ("key", "value"), ("key",))
    assert sql == ("INSERT INTO meta (key, value) VALUES (?, ?) "
                   "ON CONFLICT (key) DO UPDATE SET value = excluded.value")
    assert db.upsert_sql("t", ("a", "b"), ("a", "b")).endswith("DO NOTHING")


def test_ddl_rewrite_for_postgres_touches_only_the_type_names():
    ddl = ("CREATE TABLE x (id INTEGER PRIMARY KEY AUTOINCREMENT, n INTEGER, "
           "score REAL, blob BLOB, integer_ish TEXT)")
    out = migrations.sql_for_dialect(ddl, "postgres")
    assert out == ("CREATE TABLE x (id BIGSERIAL PRIMARY KEY, n BIGINT, "
                   "score DOUBLE PRECISION, blob BYTEA, integer_ish TEXT)")
    assert migrations.sql_for_dialect(ddl, "sqlite") == ddl


# ── Rows ─────────────────────────────────────────────────────────────────────

def test_rows_answer_to_names_and_positions_on_both_backends():
    conn = db.get_conn()
    conn.execute(db.upsert_sql("meta", ("key", "value"), ("key",)), ("probe", "1"))
    row = conn.execute("SELECT key, value FROM meta WHERE key = ?", ("probe",)).fetchone()
    assert row["key"] == "probe" and row[0] == "probe"
    assert row["value"] == "1" and row[1] == "1"
    assert set(row.keys()) == {"key", "value"}
    assert dict(row) == {"key": "probe", "value": "1"}
    assert list(row) == ["probe", "1"]
    with pytest.raises((KeyError, IndexError)):
        row["missing"]


def test_the_dialect_helpers_run_on_the_live_backend():
    conn = db.get_conn()
    with db.transaction() as tx:
        tx.execute(db.upsert_sql("runs", ("run_id", "agent_id", "status", "extra"), ("run_id",)),
                   ("r-flow", "a", "completed", db.dumps({"is_flow": True, "flow_run_id": "fr1"})))
        tx.execute(db.upsert_sql("runs", ("run_id", "agent_id", "status", "extra"), ("run_id",)),
                   ("r-plain", "b", "failed", db.dumps({})))
    flow = conn.execute(
        f"SELECT run_id FROM runs WHERE {db.json_text('extra', 'flow_run_id')} = ?", ("fr1",)
    ).fetchall()
    assert [r["run_id"] for r in flow] == ["r-flow"]
    truthy = conn.execute(
        f"SELECT run_id FROM runs WHERE {db.json_truthy('extra', 'is_flow')}").fetchall()
    assert [r["run_id"] for r in truthy] == ["r-flow"]
    agg = conn.execute(
        f"SELECT {db.sum_if('status = ?')} AS done, {db.group_concat('DISTINCT agent_id')} AS agents "
        "FROM runs", ("completed",)).fetchone()
    assert int(agg["done"]) == 1
    assert set(str(agg["agents"]).split(",")) == {"a", "b"}
    page = conn.execute("SELECT run_id FROM runs ORDER BY run_id LIMIT ? OFFSET ?",
                        (db.NO_LIMIT(), 1)).fetchall()
    assert len(page) == 1


def test_a_transaction_is_reentrant_and_rolls_back_on_error():
    with db.transaction() as conn:
        conn.execute(db.upsert_sql("meta", ("key", "value"), ("key",)), ("outer", "1"))
        with db.transaction() as inner:
            inner.execute(db.upsert_sql("meta", ("key", "value"), ("key",)), ("inner", "1"))
    keys = {r["key"] for r in db.get_conn().execute("SELECT key FROM meta").fetchall()}
    assert {"outer", "inner"} <= keys

    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            conn.execute(db.upsert_sql("meta", ("key", "value"), ("key",)), ("doomed", "1"))
            raise RuntimeError("abort")
    keys = {r["key"] for r in db.get_conn().execute("SELECT key FROM meta").fetchall()}
    assert "doomed" not in keys


# ── Migrations ───────────────────────────────────────────────────────────────

def test_the_ledger_names_every_migration_and_meta_carries_the_version():
    conn = db.get_conn()
    applied = migrations.applied_versions(conn, db.dialect())
    assert applied == [mg.version for mg in migrations.select_for(db.dialect())]
    assert applied[0] == 5
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert int(row["value"]) == db.SCHEMA_VERSION == max(applied)


def test_a_database_from_a_newer_build_is_refused(reopen_db, tmp_path):
    reopen_db(tmp_path / "future.db")
    conn = db._connect()
    d = db.dialect()
    migrations._ensure_ledger(conn, d)
    conn.execute(f"INSERT INTO {migrations.LEDGER_TABLE} (version, name, applied_at) "
                 "VALUES (?, ?, ?)", (9999, "from_the_future", "2999-01-01"))
    try:
        with pytest.raises(RuntimeError, match="newer"):
            db.get_conn()
    finally:
        # The per-test reset keeps the ledger (it is the schema's own record),
        # so the row from the future must not outlive this test.
        conn.execute(f"DELETE FROM {migrations.LEDGER_TABLE} WHERE version = ?", (9999,))


def test_dialect_specific_files_win_over_shared_ones(tmp_path, monkeypatch):
    (tmp_path / "0007_thing.sql").write_text("CREATE TABLE a (x TEXT);", encoding="utf-8")
    (tmp_path / "0007_thing.postgres.sql").write_text("CREATE TABLE a (x TEXT);", encoding="utf-8")
    (tmp_path / "0006_other.py").write_text("def upgrade(conn, dialect):\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(migrations, "MIGRATIONS_DIR", tmp_path)
    pg = migrations.select_for("postgres")
    lite = migrations.select_for("sqlite")
    assert [m.version for m in pg] == [6, 7] and pg[1].dialect == "postgres"
    assert [m.version for m in lite] == [6, 7] and lite[1].dialect is None
    assert lite[0].kind == "py" and lite[1].kind == "sql"


@pytest.mark.sqlite_only
def test_a_pre_ledger_sqlite_database_is_upgraded_in_place(reopen_db, tmp_path):
    """A state directory written before numbered migrations existed: tables
    present, some post-hoc columns missing, meta.schema_version stamped by
    hand. Opening it must add the columns, backfill them and stamp 5."""
    path = tmp_path / "old.db"
    raw = sqlite3.connect(str(path))
    raw.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    raw.execute("INSERT INTO meta VALUES ('schema_version', '2')")
    raw.execute("INSERT INTO meta VALUES ('json_migrated', '{}')")
    raw.execute("INSERT INTO meta VALUES ('flow_runs_migrated', '{}')")
    raw.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, conversation_id TEXT, "
                "task_id TEXT, doc TEXT NOT NULL)")
    raw.execute("INSERT INTO sessions VALUES ('s1', 'c1', NULL, ?)",
                (json.dumps({"workspace": "w", "created_at": "2026-01-01", "is_flow": True}),))
    raw.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, key TEXT, parent_id TEXT, status TEXT, "
                "workspace TEXT, project_id TEXT, created_at TEXT, updated_at TEXT, doc TEXT NOT NULL)")
    raw.execute("INSERT INTO tasks (id, doc) VALUES ('t1', '{}')")
    raw.commit()
    raw.close()

    reopen_db(path)
    conn = db.get_conn()
    s = conn.execute("SELECT workspace, created_at, is_flow FROM sessions WHERE session_id='s1'").fetchone()
    assert (s["workspace"], s["created_at"], s["is_flow"]) == ("w", "2026-01-01", 1)
    t = conn.execute("SELECT created_by_user FROM tasks WHERE id='t1'").fetchone()
    assert t["created_by_user"] == "local"
    assert "progress" in migrations.table_columns(conn, "sqlite", "loop_runs")
    assert int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) \
        == db.SCHEMA_VERSION
    assert migrations.applied_versions(conn, "sqlite") == [
        mg.version for mg in migrations.select_for("sqlite")]
    # Every baseline table exists now, with the legacy rows kept.
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


# ── Transfer ─────────────────────────────────────────────────────────────────

def test_the_whole_database_moves_to_another_sqlite_file_and_counts_match(tmp_path):
    with db.transaction() as conn:
        conn.execute(db.upsert_sql("runs", ("run_id", "agent_id", "status"), ("run_id",)),
                     ("r1", "a", "completed"))
        conn.execute("INSERT INTO task_activity (task_id, entry) VALUES (?, ?)", ("t", "{}"))
        conn.execute("INSERT INTO task_activity (task_id, entry) VALUES (?, ?)", ("t", "{}"))
    target = tmp_path / "copy.db"
    report = db_transfer.transfer(str(target))
    assert report["tables"]["runs"] == {"source": 1, "target": 1}
    assert report["tables"]["task_activity"] == {"source": 2, "target": 2}

    copy = db_transfer.open_target(str(target))
    assert copy.execute("SELECT status FROM runs").fetchone()[0] == "completed"
    keys = {r[0] for r in copy.execute("SELECT key FROM meta").fetchall()}
    assert {"schema_version", "json_migrated", "flow_runs_migrated"} <= keys
    assert migrations.applied_versions(copy, "sqlite") == migrations.applied_versions(
        db.get_conn(), db.dialect())
    # The copy keeps accepting appends after the explicit ids it was given.
    copy.execute("INSERT INTO task_activity (task_id, entry) VALUES (?, ?)", ("t", "{}"))
    assert copy.execute("SELECT COUNT(*) FROM task_activity").fetchone()[0] == 3
    copy.close()

    # A second copy refuses to overwrite rows unless told to.
    with pytest.raises(RuntimeError, match="force"):
        db_transfer.transfer(str(target))
    assert db_transfer.transfer(str(target), force=True)["tables"]["runs"]["target"] == 1


def test_describe_hides_the_password():
    assert db_transfer.describe("postgresql://ah:secret@db:5432/x") == "postgresql://ah:***@db:5432/x"
    assert db_transfer.describe("state/agents_hub.db").endswith("agents_hub.db")


def test_status_reports_the_backend_and_counts():
    info = db_transfer.status()
    assert info["dialect"] in ("sqlite", "postgres")
    assert info["schema_version"] == db.SCHEMA_VERSION
    assert "runs" in info["counts"]
    assert Path(info["location"]).name == "test.db" or info["location"].startswith("postgresql://")
