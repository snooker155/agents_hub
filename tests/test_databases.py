"""Read-only databases connector: store, guard, drivers and routes.

Covers connectors/databases/guard.py's statement check (a table of allowed
and rejected SQL), store.py's public() masking of the dsn, the sqlite path
end to end through dashboard/backend/routes/databases.py (create a
connection, test it, read its schema, query it, see row_limit truncation and
JSON-safe values), and the three tools (tools/databases.py), including the
guard rejecting a write and an unknown connection name.

Postgres and clickhouse are covered with monkeypatched client objects
(sys.modules substitution, no real server): just enough to check each driver
opens its session with the right read-only options/settings.

Run: ``python -m pytest tests/test_databases.py -q``
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from connectors.databases import drivers, guard, store  # noqa: E402


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import databases as databases_routes

    app = FastAPI()
    app.include_router(databases_routes.router)
    return TestClient(app)


def _make_sqlite_file(tmp_path: Path, n_rows: int = 5) -> Path:
    db_path = tmp_path / "sample.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT, created_at TEXT)")
    for i in range(n_rows):
        conn.execute("INSERT INTO widgets (name, created_at) VALUES (?, ?)", (f"w{i}", "2026-01-01T00:00:00"))
    conn.commit()
    conn.close()
    return db_path


# ── guard: allowed and rejected statements ──────────────────────────────────

_ALLOWED_SQL = [
    "SELECT 1",
    "select * from widgets",
    "  WITH cte AS (SELECT 1) SELECT * FROM cte",
    "SHOW TABLES",
    "DESCRIBE widgets",
    "DESC widgets",
    "EXPLAIN SELECT 1",
    "VALUES (1), (2)",
    "SELECT * FROM widgets WHERE name = 'o''brien'",
    "SELECT * FROM widgets;",  # one trailing semicolon is fine
    "SELECT * FROM widgets -- delete this comment later",  # a keyword inside a comment is not a keyword
    "SELECT created_at, update_time FROM widgets",  # substrings of forbidden words, not the words themselves
]

_REJECTED_SQL = [
    ("DELETE FROM widgets", "read statements"),
    ("INSERT INTO widgets (name) VALUES ('x')", "read statements"),
    ("SELECT 1; DROP TABLE widgets", "one statement"),
    ("SELECT 1; SELECT 2", "one statement"),
    ("WITH cte AS (SELECT 1) DELETE FROM widgets", "disallowed keyword"),
    ("SELECT * FROM widgets INTO OUTFILE '/tmp/x'", "OUTFILE"),
    ("SELECT pg_read_file('/etc/passwd')", "disallowed function"),
    ("SELECT load_file('/etc/passwd')", "disallowed function"),
    ("SELECT * FROM file('/etc/passwd', 'CSV')", "disallowed function"),
    ("/* comment */ DROP TABLE widgets", "read statements"),
    ("", "empty statement"),
]


@pytest.mark.parametrize("sql", _ALLOWED_SQL)
def test_guard_allows_read_statements(sql):
    guard.check_read_only(sql)  # must not raise


@pytest.mark.parametrize("sql,fragment", _REJECTED_SQL)
def test_guard_rejects_everything_else(sql, fragment):
    with pytest.raises(guard.DatabaseError, match=fragment):
        guard.check_read_only(sql)


def test_guard_clean_statement_strips_one_trailing_semicolon():
    assert guard.clean_statement("SELECT 1;") == "SELECT 1"
    assert guard.clean_statement("  SELECT 1  ") == "SELECT 1"


# ── store: public() masks the dsn ────────────────────────────────────────────

def test_public_hides_dsn_and_shows_a_host_only_hint():
    record = store.create_connection(
        workspace="ws1", name="main", kind="postgres",
        dsn="postgresql://user:secret@db.internal:5432/app",
    )
    pub = store.public(record)
    assert "dsn" not in pub
    assert pub["dsn_hint"] == "postgresql://db.internal:5432/app"
    assert "secret" not in json.dumps(pub)


def test_kind_matches_dsn():
    assert store.kind_matches_dsn("postgres", "postgresql://h/db") is True
    assert store.kind_matches_dsn("postgres", "mysql://h/db") is False
    assert store.kind_matches_dsn("sqlite", "/any/path.db") is True
    assert store.kind_matches_dsn("clickhouse", "clickhouse://h:9000/db") is True


def test_credentials_spec_reports_the_supported_kinds():
    from connectors.databases import CREDENTIALS

    assert CREDENTIALS.is_configured() is True  # nothing to configure at the connector level
    assert set(CREDENTIALS.extra()["kinds"]) == {"postgres", "mysql", "clickhouse", "sqlite"}


# ── sqlite, end to end through the route ─────────────────────────────────────

def test_sqlite_connection_through_the_route(tmp_path):
    db_path = _make_sqlite_file(tmp_path, n_rows=3)
    c = _client()

    r = c.post("/api/databases/connections", json={
        "workspace": "ws-sqlite", "name": "local", "kind": "sqlite", "dsn": str(db_path),
    })
    assert r.status_code == 201, r.text
    conn = r.json()
    assert conn["dsn_hint"] == f"sqlite://{db_path.name}"
    assert "dsn" not in conn
    conn_id = conn["id"]

    listed = c.get("/api/databases/connections", params={"workspace": "ws-sqlite"}).json()
    assert len(listed) == 1 and listed[0]["id"] == conn_id

    tested = c.post(f"/api/databases/connections/{conn_id}/test").json()
    assert tested["ok"] is True and isinstance(tested["elapsed_ms"], int)

    schema = c.get(f"/api/databases/connections/{conn_id}/schema").json()
    tables = {t["name"]: t for t in schema["tables"]}
    assert "widgets" in tables
    columns = {col["name"] for col in tables["widgets"]["columns"]}
    assert {"id", "name", "created_at"} <= columns

    queried = c.post(
        f"/api/databases/connections/{conn_id}/query",
        json={"sql": "SELECT id, name FROM widgets ORDER BY id"},
    ).json()
    assert queried["columns"] == ["id", "name"]
    assert queried["row_count"] == 3
    assert queried["truncated"] is False

    rejected = c.post(f"/api/databases/connections/{conn_id}/query", json={"sql": "DELETE FROM widgets"})
    assert rejected.status_code == 400

    assert c.delete(f"/api/databases/connections/{conn_id}").status_code == 200
    assert c.delete(f"/api/databases/connections/{conn_id}").status_code == 404


def test_create_rejects_a_dsn_that_does_not_match_the_kind():
    c = _client()
    r = c.post("/api/databases/connections", json={
        "workspace": "ws-mismatch", "name": "x", "kind": "postgres", "dsn": "mysql://h/db",
    })
    assert r.status_code == 400


def test_row_limit_truncation_and_json_safe_values(tmp_path):
    db_path = _make_sqlite_file(tmp_path, n_rows=10)
    record = store.create_connection(
        workspace="ws-limit", name="local", kind="sqlite", dsn=str(db_path), row_limit=4,
    )
    result = drivers.run_query(record, "SELECT * FROM widgets ORDER BY id")
    assert result["row_count"] == 4
    assert result["truncated"] is True
    # The row shape must stay plain JSON-serializable values regardless of
    # what the driver fetched (sqlite returns str/int here; postgres/mysql/
    # clickhouse route datetimes, Decimal and bytes through _json_safe).
    json.dumps(result["rows"])


# ── tools ─────────────────────────────────────────────────────────────────

def test_db_query_tool(tmp_path, monkeypatch):
    from tools.databases import db_list_connections, db_query, db_schema

    db_path = _make_sqlite_file(tmp_path, n_rows=2)
    store.create_connection(workspace="ws-tool", name="local", kind="sqlite", dsn=str(db_path))
    monkeypatch.setattr("common.workspace_context.resolve_active_workspace", lambda *a, **k: "ws-tool")

    out = json.loads(db_list_connections.invoke({}))
    assert out["ok"] is True and len(out["connections"]) == 1
    assert "dsn" not in out["connections"][0]

    out = json.loads(db_schema.invoke({"connection": "local"}))
    assert out["ok"] is True
    assert any(t["name"] == "widgets" for t in out["tables"])

    out = json.loads(db_query.invoke({"connection": "local", "sql": "SELECT * FROM widgets"}))
    assert out["ok"] is True and out["row_count"] == 2

    out = json.loads(db_query.invoke({"connection": "missing", "sql": "SELECT 1"}))
    assert out["ok"] is False and "no database connection named" in out["error"]

    out = json.loads(db_query.invoke({"connection": "local", "sql": "DELETE FROM widgets"}))
    assert out["ok"] is False and "read statements" in out["error"]


# ── postgres / clickhouse: read-only options, monkeypatched ────────────────

def test_postgres_driver_opens_read_only(monkeypatch):
    import types

    calls = {}

    class FakeCursor:
        description = [types.SimpleNamespace(name="n")]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            calls["sql"] = sql

        def fetchmany(self, n):
            calls["fetchmany"] = n
            return [(1,)]

    class FakeConn:
        read_only = None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return FakeCursor()

    def fake_connect(dsn, connect_timeout=None, options=None):
        calls["dsn"] = dsn
        calls["connect_timeout"] = connect_timeout
        calls["options"] = options
        return FakeConn()

    fake_psycopg = types.SimpleNamespace(connect=fake_connect)
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)

    record = {"kind": "postgres", "dsn": "postgresql://h:5432/db", "row_limit": 200, "timeout_seconds": 7}
    result = drivers.run_query(record, "SELECT 1")

    assert calls["dsn"] == "postgresql://h:5432/db"
    assert calls["connect_timeout"] == 7
    assert "default_transaction_read_only=on" in calls["options"]
    assert "statement_timeout=7000" in calls["options"]
    assert result["row_count"] == 1


def test_clickhouse_driver_opens_read_only(monkeypatch):
    import types

    calls = {}

    class FakeResult:
        column_names = ["n"]
        result_rows = [(1,)]

    class FakeClient:
        def query(self, sql, parameters=None, settings=None):
            calls["sql"] = sql
            calls["settings"] = settings
            return FakeResult()

        def close(self):
            pass

    def fake_get_client(dsn=None):
        calls["dsn"] = dsn
        return FakeClient()

    fake_module = types.SimpleNamespace(get_client=fake_get_client)
    monkeypatch.setitem(sys.modules, "clickhouse_connect", fake_module)

    record = {"kind": "clickhouse", "dsn": "clickhouse://h:9000/db", "row_limit": 200, "timeout_seconds": 9}
    result = drivers.run_query(record, "SELECT 1")

    assert calls["dsn"] == "clickhouse://h:9000/db"
    assert calls["settings"] == {"readonly": 1, "max_execution_time": 9}
    assert result["row_count"] == 1
