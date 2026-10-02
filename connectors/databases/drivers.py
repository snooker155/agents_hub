"""
One ``run_query`` / ``list_schema`` implementation per database kind
(postgres, mysql, clickhouse, sqlite), dispatched from a connection record's
``kind`` (connectors/databases/store.py). Every call goes through
guard.check_read_only first, then opens the session itself read-only
(``default_transaction_read_only`` for postgres, ``SET SESSION TRANSACTION
READ ONLY`` for mysql, ``readonly: 1`` for clickhouse, a ``file:...?mode=ro``
URI for sqlite) — the guard is the first line, the session the second, see
guard.py's docstring for why both matter.

Every result is JSON-safe (datetimes to ISO strings, Decimal to float, bytes
to hex) and capped at the connection's ``row_limit``: one extra row is
fetched past the limit so ``truncated`` can be set honestly without a second
round trip. Every call runs on its own thread with a hard timeout
(``timeout_seconds``), so a slow or hanging connection cannot block the
caller (the agent run, or the dashboard request) indefinitely.

mysql's driver (``pymysql``) is not installed by default; its functions
raise :class:`guard.DatabaseError` with an install hint rather than failing
at import time, so the other three kinds stay usable without it.
"""
from __future__ import annotations

import concurrent.futures
import logging
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.parse import quote, urlsplit

from .guard import DatabaseError, check_read_only, clean_statement

log = logging.getLogger("connectors.databases")

_SCHEMA_TABLE_LIMIT = 300


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return value


def _run_with_timeout(fn: Callable[[], Any], timeout_seconds: int) -> Any:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError as exc:
            raise DatabaseError(f"query timed out after {timeout_seconds}s") from exc


def _rows_to_schema(rows: Sequence[Sequence[Any]], limit_tables: int = _SCHEMA_TABLE_LIMIT) -> Dict[str, Any]:
    """Group ``(schema, table, column, type)`` rows into the tables/columns shape."""
    tables: Dict[tuple, Dict[str, Any]] = {}
    order: List[tuple] = []
    for schema, table, column, dtype in rows:
        key = (schema, table)
        if key not in tables:
            if len(order) >= limit_tables:
                continue
            tables[key] = {"schema": schema, "name": table, "columns": []}
            order.append(key)
        tables[key]["columns"].append({"name": column, "type": dtype or ""})
    return {"tables": [tables[k] for k in order]}


# ── postgres ──────────────────────────────────────────────────────────────

def _pg_connect(dsn: str, timeout_seconds: int):
    import psycopg

    options = f"-c default_transaction_read_only=on -c statement_timeout={timeout_seconds * 1000}"
    conn = psycopg.connect(dsn, connect_timeout=timeout_seconds, options=options)
    conn.read_only = True
    return conn


def _pg_run_query(connection: Dict[str, Any], sql: str, params: Optional[Sequence[Any]]) -> Dict[str, Any]:
    row_limit = int(connection.get("row_limit") or 200)
    timeout_s = int(connection.get("timeout_seconds") or 20)

    def _do() -> Dict[str, Any]:
        start = time.monotonic()
        with _pg_connect(connection["dsn"], timeout_s) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params or None)
                columns = [d.name for d in (cur.description or [])]
                rows = cur.fetchmany(row_limit + 1)
        truncated = len(rows) > row_limit
        rows = rows[:row_limit]
        return {
            "columns": columns,
            "rows": [[_json_safe(v) for v in row] for row in rows],
            "row_count": len(rows),
            "truncated": truncated,
            "elapsed_ms": int((time.monotonic() - start) * 1000),
        }

    return _run_with_timeout(_do, timeout_s)


def _pg_list_schema(connection: Dict[str, Any]) -> Dict[str, Any]:
    timeout_s = int(connection.get("timeout_seconds") or 20)
    allowed_schemas = list(connection.get("allowed_schemas") or ["public"])

    def _do() -> Dict[str, Any]:
        with _pg_connect(connection["dsn"], timeout_s) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_schema, table_name, column_name, data_type "
                    "FROM information_schema.columns WHERE table_schema = ANY(%s) "
                    "ORDER BY table_schema, table_name, ordinal_position",
                    (allowed_schemas,),
                )
                rows = cur.fetchall()
        return _rows_to_schema(rows)

    return _run_with_timeout(_do, timeout_s)


# ── mysql ─────────────────────────────────────────────────────────────────

def _mysql_dsn_kwargs(dsn: str) -> Dict[str, Any]:
    parts = urlsplit(dsn)
    return {
        "host": parts.hostname or "localhost",
        "port": parts.port or 3306,
        "user": parts.username or "",
        "password": parts.password or "",
        "database": (parts.path or "/").lstrip("/") or None,
    }


def _mysql_connect(dsn: str, timeout_seconds: int):
    try:
        import pymysql
    except ImportError as exc:
        raise DatabaseError("mysql driver not installed: pip install pymysql") from exc
    return pymysql.connect(connect_timeout=timeout_seconds, **_mysql_dsn_kwargs(dsn))


def _mysql_run_query(connection: Dict[str, Any], sql: str, params: Optional[Sequence[Any]]) -> Dict[str, Any]:
    row_limit = int(connection.get("row_limit") or 200)
    timeout_s = int(connection.get("timeout_seconds") or 20)

    def _do() -> Dict[str, Any]:
        start = time.monotonic()
        conn = _mysql_connect(connection["dsn"], timeout_s)
        try:
            with conn.cursor() as cur:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
                cur.execute(f"SET SESSION max_execution_time={timeout_s * 1000}")
                cur.execute(sql, params or None)
                columns = [d[0] for d in (cur.description or [])]
                rows = cur.fetchmany(row_limit + 1)
        finally:
            conn.close()
        truncated = len(rows) > row_limit
        rows = rows[:row_limit]
        return {
            "columns": columns,
            "rows": [[_json_safe(v) for v in row] for row in rows],
            "row_count": len(rows),
            "truncated": truncated,
            "elapsed_ms": int((time.monotonic() - start) * 1000),
        }

    return _run_with_timeout(_do, timeout_s)


def _mysql_list_schema(connection: Dict[str, Any]) -> Dict[str, Any]:
    timeout_s = int(connection.get("timeout_seconds") or 20)
    kwargs = _mysql_dsn_kwargs(connection["dsn"])
    allowed_schemas = list(connection.get("allowed_schemas") or ([kwargs["database"]] if kwargs.get("database") else []))

    def _do() -> Dict[str, Any]:
        conn = _mysql_connect(connection["dsn"], timeout_s)
        try:
            with conn.cursor() as cur:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
                if allowed_schemas:
                    placeholders = ",".join(["%s"] * len(allowed_schemas))
                    cur.execute(
                        "SELECT table_schema, table_name, column_name, data_type "
                        f"FROM information_schema.columns WHERE table_schema IN ({placeholders}) "
                        "ORDER BY table_schema, table_name, ordinal_position",
                        allowed_schemas,
                    )
                else:
                    cur.execute(
                        "SELECT table_schema, table_name, column_name, data_type "
                        "FROM information_schema.columns WHERE table_schema NOT IN "
                        "('mysql', 'information_schema', 'performance_schema', 'sys') "
                        "ORDER BY table_schema, table_name, ordinal_position"
                    )
                rows = cur.fetchall()
        finally:
            conn.close()
        return _rows_to_schema(rows)

    return _run_with_timeout(_do, timeout_s)


# ── clickhouse ────────────────────────────────────────────────────────────

def _ch_run_query(connection: Dict[str, Any], sql: str, params: Optional[Sequence[Any]]) -> Dict[str, Any]:
    import clickhouse_connect

    row_limit = int(connection.get("row_limit") or 200)
    timeout_s = int(connection.get("timeout_seconds") or 20)

    def _do() -> Dict[str, Any]:
        start = time.monotonic()
        client = clickhouse_connect.get_client(dsn=connection["dsn"])
        try:
            result = client.query(
                sql, parameters=params or None,
                settings={"readonly": 1, "max_execution_time": timeout_s},
            )
            columns = list(result.column_names)
            all_rows = list(result.result_rows)
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                log.debug("ClickHouse client close failed", exc_info=True)
        truncated = len(all_rows) > row_limit
        rows = all_rows[:row_limit]
        return {
            "columns": columns,
            "rows": [[_json_safe(v) for v in row] for row in rows],
            "row_count": len(rows),
            "truncated": truncated,
            "elapsed_ms": int((time.monotonic() - start) * 1000),
        }

    return _run_with_timeout(_do, timeout_s)


def _ch_list_schema(connection: Dict[str, Any]) -> Dict[str, Any]:
    import clickhouse_connect

    timeout_s = int(connection.get("timeout_seconds") or 20)
    allowed_schemas = list(connection.get("allowed_schemas") or [])

    def _do() -> Dict[str, Any]:
        client = clickhouse_connect.get_client(dsn=connection["dsn"])
        try:
            if allowed_schemas:
                result = client.query(
                    "SELECT database, table, name, type FROM system.columns "
                    "WHERE database IN {schemas:Array(String)} "
                    "ORDER BY database, table, position",
                    parameters={"schemas": allowed_schemas},
                    settings={"readonly": 1, "max_execution_time": timeout_s},
                )
            else:
                result = client.query(
                    "SELECT database, table, name, type FROM system.columns "
                    "WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA') "
                    "ORDER BY database, table, position",
                    settings={"readonly": 1, "max_execution_time": timeout_s},
                )
            rows = list(result.result_rows)
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                log.debug("ClickHouse client close failed", exc_info=True)
        return _rows_to_schema(rows)

    return _run_with_timeout(_do, timeout_s)


# ── sqlite ────────────────────────────────────────────────────────────────

def _sqlite_path(dsn: str) -> str:
    s = (dsn or "").strip()
    if s.startswith("sqlite://"):
        s = s[len("sqlite://"):]
    elif s.startswith("file:"):
        s = s[len("file:"):].split("?")[0]
    return s


def _sqlite_connect(dsn: str, timeout_seconds: int):
    import sqlite3

    path = _sqlite_path(dsn)
    uri = f"file:{quote(path, safe='/:')}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=timeout_seconds)


def _sqlite_run_query(connection: Dict[str, Any], sql: str, params: Optional[Sequence[Any]]) -> Dict[str, Any]:
    row_limit = int(connection.get("row_limit") or 200)
    timeout_s = int(connection.get("timeout_seconds") or 20)

    def _do() -> Dict[str, Any]:
        start = time.monotonic()
        conn = _sqlite_connect(connection["dsn"], timeout_s)
        try:
            cur = conn.cursor()
            cur.execute(sql, params or ())
            columns = [d[0] for d in (cur.description or [])]
            rows = cur.fetchmany(row_limit + 1)
        finally:
            conn.close()
        truncated = len(rows) > row_limit
        rows = rows[:row_limit]
        return {
            "columns": columns,
            "rows": [[_json_safe(v) for v in row] for row in rows],
            "row_count": len(rows),
            "truncated": truncated,
            "elapsed_ms": int((time.monotonic() - start) * 1000),
        }

    return _run_with_timeout(_do, timeout_s)


def _sqlite_quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _sqlite_list_schema(connection: Dict[str, Any]) -> Dict[str, Any]:
    timeout_s = int(connection.get("timeout_seconds") or 20)

    def _do() -> Dict[str, Any]:
        conn = _sqlite_connect(connection["dsn"], timeout_s)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name LIMIT ?",
                (_SCHEMA_TABLE_LIMIT,),
            )
            table_names = [row[0] for row in cur.fetchall()]
            tables = []
            for name in table_names:
                cur.execute(f"PRAGMA table_info({_sqlite_quote_ident(name)})")
                columns = [{"name": row[1], "type": row[2] or ""} for row in cur.fetchall()]
                tables.append({"schema": "main", "name": name, "columns": columns})
        finally:
            conn.close()
        return {"tables": tables}

    return _run_with_timeout(_do, timeout_s)


# ── dispatch ──────────────────────────────────────────────────────────────

_RUN_QUERY: Dict[str, Callable[[Dict[str, Any], str, Optional[Sequence[Any]]], Dict[str, Any]]] = {
    "postgres": _pg_run_query,
    "mysql": _mysql_run_query,
    "clickhouse": _ch_run_query,
    "sqlite": _sqlite_run_query,
}
_LIST_SCHEMA: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "postgres": _pg_list_schema,
    "mysql": _mysql_list_schema,
    "clickhouse": _ch_list_schema,
    "sqlite": _sqlite_list_schema,
}


def run_query(connection: Dict[str, Any], sql: str, params: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
    """Guard, run and shape one read-only statement against ``connection``.

    Returns ``{"columns", "rows", "row_count", "truncated", "elapsed_ms"}``.
    Raises :class:`DatabaseError` for a rejected statement, an unknown kind,
    a missing driver, a connection failure or a timeout.
    """
    kind = str(connection.get("kind") or "")
    check_read_only(sql, kind)
    fn = _RUN_QUERY.get(kind)
    if fn is None:
        raise DatabaseError(f"unknown database kind: {kind!r}")
    return fn(connection, clean_statement(sql), params)


def list_schema(connection: Dict[str, Any]) -> Dict[str, Any]:
    """Tables and columns of ``connection``, capped at 300 tables."""
    kind = str(connection.get("kind") or "")
    fn = _LIST_SCHEMA.get(kind)
    if fn is None:
        raise DatabaseError(f"unknown database kind: {kind!r}")
    return fn(connection)


def test_connection(connection: Dict[str, Any]) -> Dict[str, Any]:
    """Open ``connection``, run ``SELECT 1``, report ok/elapsed_ms or ok/error."""
    try:
        result = run_query(connection, "SELECT 1")
    except DatabaseError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "elapsed_ms": result["elapsed_ms"]}


__all__ = ["DatabaseError", "run_query", "list_schema", "test_connection"]
