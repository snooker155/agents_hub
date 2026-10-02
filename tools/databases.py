"""
databases — the agent-facing read path over the connections an operator
configured on the Databases connector page (connectors/databases).

Three tools: db_list_connections (what is available), db_schema (its tables
and columns), db_query (one read-only SQL statement). All three resolve the
active workspace the way the rest of the connector tools do
(common.workspace_context.resolve_active_workspace), so an agent never names
a workspace itself, and db_schema/db_query accept either a connection's id or
its name within that workspace (connectors.databases.store.find_connection).

Rows a connection returns are the operator's own data, read from a
connection the operator configured: tools/capabilities.py grants db_query
only READS_PRIVATE, not INGESTS_UNTRUSTED, so results are not wrapped with
tools.web.wrap_untrusted the way a fetched page or an imported issue body
is. The query itself is still guarded: connectors/databases/guard.py rejects
anything but a single read-only statement before it reaches
connectors/databases/drivers.py, and the database session underneath is
opened read-only as a second line of defence.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from langchain_core.tools import tool
from pydantic import BaseModel, Field


def _ok(payload: Dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, default=str)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _find_connection(connection: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    from common.workspace_context import resolve_active_workspace
    from connectors.databases import store

    workspace = resolve_active_workspace()
    return workspace, store.find_connection(workspace, connection)


class DbListConnectionsInput(BaseModel):
    pass


@tool("db_list_connections", args_schema=DbListConnectionsInput)
def db_list_connections() -> str:
    """List the read-only database connections configured for this workspace.

    Each entry carries its id, name, kind and a host-only dsn_hint (never the
    real connection string), plus allowed_schemas and row_limit.
    """
    from common.workspace_context import resolve_active_workspace
    from connectors.databases import store

    workspace = resolve_active_workspace()
    records = store.list_connections(workspace)
    return _ok({"connections": [store.public(r) for r in records]})


class DbSchemaInput(BaseModel):
    connection: str = Field(..., description="A connection's id or its name within the active workspace")


@tool("db_schema", args_schema=DbSchemaInput)
def db_schema(connection: str) -> str:
    """The tables and columns of a configured database connection, capped at 300 tables."""
    from connectors.databases import drivers

    _, record = _find_connection(connection)
    if record is None:
        return _err(f"no database connection named {connection!r} in this workspace")
    try:
        schema = drivers.list_schema(record)
    except drivers.DatabaseError as exc:
        return _err(str(exc))
    return _ok(schema)


class DbQueryInput(BaseModel):
    connection: str = Field(..., description="A connection's id or its name within the active workspace")
    sql: str = Field(..., min_length=1, description="A single read-only SQL statement")


@tool("db_query", args_schema=DbQueryInput)
def db_query(connection: str, sql: str) -> str:
    """Run one read-only SQL statement against a configured database connection.

    Only SELECT, WITH, SHOW, DESCRIBE, DESC, EXPLAIN or VALUES statements are
    allowed, one at a time: a write, a second statement, or a call to a
    function that reads files or object storage from inside the database is
    rejected before it reaches the connection. Returns columns, rows and
    row_count; rows are capped at the connection's row_limit, and the result
    carries a note when that cut the answer short.
    """
    from connectors.databases import drivers

    _, record = _find_connection(connection)
    if record is None:
        return _err(f"no database connection named {connection!r} in this workspace")
    try:
        result = drivers.run_query(record, sql)
    except drivers.DatabaseError as exc:
        return _err(str(exc))
    if result.get("truncated"):
        result["note"] = f"Result truncated to {result['row_count']} rows (the connection's row_limit)."
    return _ok(result)


CONNECTOR_TOOLS = [db_list_connections, db_schema, db_query]

__all__ = ["db_list_connections", "db_schema", "db_query", "CONNECTOR_TOOLS"]
