"""
Read-only databases connector API: the operator's side of
connectors/databases.

Endpoints (prefix ``/api/databases``):
    GET    /connections                    list, scoped to a workspace
    POST   /connections                    create a named connection
    PATCH  /connections/{id}                edit it (dsn optional, replaces)
    DELETE /connections/{id}                remove it
    POST   /connections/{id}/test           open it, run SELECT 1
    GET    /connections/{id}/schema         tables and columns
    POST   /connections/{id}/query          run one read-only statement

This is a second surface over the same guard and drivers the db_query tool
uses (tools/databases.py): an operator exploring a connection from the
Connectors page goes through exactly the same read-only check an agent does,
not a looser one. The generic credential routes
(dashboard/backend/routes/connectors.py, ``/api/connectors/databases/...``)
still list this connector and its supported kinds; they carry no config of
their own since there is no single shared secret here.
"""
from __future__ import annotations

import asyncio
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from common.session_broker import notify_change
from connectors.databases import drivers, store

router = APIRouter(prefix="/api/databases", tags=["databases"])


class ConnectionCreate(BaseModel):
    workspace: str
    name: str
    kind: str
    dsn: str
    allowed_schemas: Optional[List[str]] = None
    row_limit: Optional[int] = None
    timeout_seconds: Optional[int] = None


class ConnectionUpdate(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None
    dsn: Optional[str] = None
    allowed_schemas: Optional[List[str]] = None
    row_limit: Optional[int] = None
    timeout_seconds: Optional[int] = None


class QueryRequest(BaseModel):
    sql: str


def _get_or_404(connection_id: str) -> dict[str, Any]:
    connection = store.get_connection(connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return connection


def _check_kind_and_dsn(kind: str, dsn: Optional[str]) -> None:
    if kind not in store.KINDS:
        raise HTTPException(status_code=400, detail=f"unknown database kind: {kind!r}")
    if dsn is not None and not store.kind_matches_dsn(kind, dsn):
        raise HTTPException(status_code=400, detail=f"dsn does not look like a {kind} connection string")


@router.get("/connections")
async def list_connections(workspace: str = Query(...)):
    return [store.public(c) for c in store.list_connections(workspace)]


@router.post("/connections", status_code=201)
async def create_connection(body: ConnectionCreate):
    _check_kind_and_dsn(body.kind, body.dsn)
    record = store.create_connection(
        workspace=body.workspace, name=body.name, kind=body.kind, dsn=body.dsn,
        allowed_schemas=body.allowed_schemas, row_limit=body.row_limit,
        timeout_seconds=body.timeout_seconds,
    )
    notify_change("databases")
    return store.public(record)


@router.patch("/connections/{connection_id}")
async def update_connection(connection_id: str, body: ConnectionUpdate):
    current = _get_or_404(connection_id)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    kind = changes.get("kind", current["kind"])
    _check_kind_and_dsn(kind, changes.get("dsn"))
    record = store.update_connection(connection_id, **changes)
    if record is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    notify_change("databases")
    return store.public(record)


@router.delete("/connections/{connection_id}")
async def delete_connection(connection_id: str):
    if not store.delete_connection(connection_id):
        raise HTTPException(status_code=404, detail="Connection not found")
    notify_change("databases")
    return {"deleted": True, "connection_id": connection_id}


@router.post("/connections/{connection_id}/test")
async def test_connection(connection_id: str):
    connection = _get_or_404(connection_id)
    return await asyncio.to_thread(drivers.test_connection, connection)


@router.get("/connections/{connection_id}/schema")
async def get_schema(connection_id: str):
    connection = _get_or_404(connection_id)
    try:
        return await asyncio.to_thread(drivers.list_schema, connection)
    except drivers.DatabaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/connections/{connection_id}/query")
async def run_query(connection_id: str, body: QueryRequest):
    connection = _get_or_404(connection_id)
    try:
        return await asyncio.to_thread(drivers.run_query, connection, body.sql)
    except drivers.DatabaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
