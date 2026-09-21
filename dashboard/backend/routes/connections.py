"""Managing external connections: the operator's side of the push path.

Split from ``/api/ingest`` on purpose. This router is authenticated the way the
rest of the dashboard is (the optional global API token), because creating a
connection *issues a credential*; the ingest router is authenticated by that
credential and can only report. One prefix with two authentication models would
mean an external service's token opened the management API too.

    GET    /api/connections            what is attached
    POST   /api/connections            create one, and issue its token *once*
    GET    /api/connections/{id}       one, with its recent runs
    PATCH  /api/connections/{id}       rename, re-describe, disable
    POST   /api/connections/{id}/rotate   new token, old one dead immediately
    DELETE /api/connections/{id}       remove it; recorded runs stay

The token is returned by exactly two endpoints, create and rotate, and is never
stored in a form that can produce it again. Losing one means rotating it, which
is the property that makes a leak recoverable.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from connections import store as connection_store

router = APIRouter(prefix="/api/connections", tags=["connections"])


class CreateConnection(BaseModel):
    id: str
    name: Optional[str] = None
    kind: str = "other"
    description: str = ""
    workspace: Optional[str] = None


class UpdateConnection(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None
    description: Optional[str] = None
    workspace: Optional[str] = None
    disabled: Optional[bool] = None
    # How many runs this connection keeps, newest first. 0 keeps everything,
    # which is a reasonable choice for a quiet connection and a bad default for
    # a busy one — see connections/retention.py.
    retention_runs: Optional[int] = None


def _visible_or_404(connection_id: str, workspace: Optional[str]) -> Dict[str, Any]:
    """The connection, as seen from this workspace, or a 404.

    Every single-connection endpoint goes through here, so where a connection
    lives is decided in one place rather than in six. A connection from another
    workspace answers like one that does not exist, which is how the rest of
    this product behaves: a page shows what is in the workspace it is looking
    at.

    This is filing, not access control. The hub is a single-tenant install with
    no accounts, and a workspace is a place to keep things, not a permission.
    """
    record = connection_store.get_connection(connection_id, workspace)
    if record is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return record


def _stats(connection_id: str) -> Dict[str, Any]:
    """Recent volume for one connection, read off the runs it reported.

    Computed rather than stored: a counter maintained on the connection record
    would be another write on every ingest call, and would drift from the runs
    themselves the first time one was deleted.
    """
    from managers import run_manager

    page = run_manager.query_runs(agent_id=connection_id, limit=200)
    items = page.get("items") or []
    return {
        "runs": page.get("total", len(items)),
        "failed": sum(1 for r in items if r.get("status") == "failed"),
        "running": sum(1 for r in items if r.get("status") == "running"),
        "last_run_at": (items[0].get("started_at") or items[0].get("created_at")) if items else None,
    }


def _retention(connection: Dict[str, Any]) -> Dict[str, Any]:
    """What this connection keeps, and where that number comes from.

    The UI has to be able to say "2000, the hub default" rather than showing a
    blank field that silently means something: a retention limit nobody can see
    is a limit people discover by missing data.
    """
    from connections.retention import effective_limit

    return {
        "runs": effective_limit(connection),
        "source": "connection" if connection.get("retention_runs") is not None else "default",
    }


@router.get("")
async def list_connections(workspace: Optional[str] = None):
    """Every connection, with how much each has reported lately."""
    records = connection_store.list_connections(workspace)
    return {"connections": [{**r, "stats": _stats(r["id"]), "retention": _retention(r)}
                            for r in records],
            "kinds": list(connection_store.KINDS)}


@router.post("", status_code=201)
async def create_connection(body: CreateConnection):
    """Create a connection and issue its token.

    The response carries the token in full, and this is the only time it exists
    anywhere outside the client that will use it.
    """
    try:
        record, token = connection_store.create_connection(
            connection_id=body.id,
            name=body.name or "",
            kind=body.kind,
            description=body.description,
            workspace=body.workspace,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"connection": record, "token": token,
            "note": "Store this token now. It is not recoverable, only replaceable."}


@router.get("/{connection_id}")
async def get_connection(connection_id: str, limit: int = 50, workspace: Optional[str] = None):
    """One connection, its reported shape, and its recent runs."""
    from managers import run_manager

    record = _visible_or_404(connection_id, workspace)
    page = run_manager.query_runs(agent_id=connection_id, limit=max(1, min(limit, 200)))
    return {"connection": {**record, "stats": _stats(connection_id),
                           "retention": _retention(record)},
            "runs": page.get("items") or [], "total": page.get("total", 0)}


@router.patch("/{connection_id}")
async def update_connection(connection_id: str, body: UpdateConnection,
                            workspace: Optional[str] = None):
    """Edit the descriptive fields, or disable the connection.

    Disabling is the reversible half of revoking: the token stops working while
    the record and its history stay, which is what an operator wants when a
    service is misbehaving but may be fixed.
    """
    current = _visible_or_404(connection_id, workspace)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}

    # Moving a connection between workspaces after it has reported would move
    # the history with it: runs recorded in one team's workspace would start
    # answering another's queries, and costs already attributed would change
    # hands. Refused rather than quietly re-filed; a fresh connection is the
    # honest way to move reporting somewhere else.
    if "workspace" in changes and changes["workspace"] != (current.get("workspace") or None):
        from managers import run_manager

        if (run_manager.query_runs(agent_id=connection_id, limit=1).get("total") or 0) > 0:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This connection has already reported runs, so its workspace is "
                    "fixed: moving it would move that history too. Create a new "
                    "connection in the other workspace instead."
                ),
            )

    record = connection_store.update_connection(connection_id, changes)
    if record is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"connection": record}


@router.post("/{connection_id}/rotate")
async def rotate_token(connection_id: str, workspace: Optional[str] = None):
    """Issue a new token. The previous one stops working at once."""
    _visible_or_404(connection_id, workspace)
    result = connection_store.rotate_token(connection_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    record, token = result
    return {"connection": record, "token": token,
            "note": "The previous token no longer authenticates."}


class AnswerRequest(BaseModel):
    value: Any = None
    answered_by: str = ""


@router.post("/{connection_id}/runs/{run_id}/answer")
async def answer_run(connection_id: str, run_id: str, body: AnswerRequest,
                     workspace: Optional[str] = None):
    """Answer a reported run that stopped to ask a question.

    The operator's half of the exchange. The client is not told; it is polling
    (``GET /api/ingest/runs/{run_id}/answer``), because nothing here can call
    out to an agent that reports in.
    """
    from connections import service as ingest_service
    from managers import run_manager

    _visible_or_404(connection_id, workspace)

    run = run_manager.get_run_by_id(run_id)
    if run is None or str(run.get("connection_id") or "") != connection_id:
        raise HTTPException(status_code=404, detail="Run not found for this connection")

    try:
        return ingest_service.answer_run(run_id, body.value, answered_by=body.answered_by)
    except ingest_service.IngestError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


@router.post("/{connection_id}/prune")
async def prune_connection(connection_id: str, workspace: Optional[str] = None):
    """Trim this connection's history back to its cap now.

    The cap is enforced by the daily maintenance pass, not inside an ingest
    request: pruning is a burst of deletes, and doing it while a customer's
    graph waits would put this hub's housekeeping on their critical path. This
    endpoint is for the operator who does not want to wait for tomorrow.
    """
    from connections.retention import prune_connection as prune

    return prune(_visible_or_404(connection_id, workspace))


@router.delete("/{connection_id}")
async def delete_connection(connection_id: str, workspace: Optional[str] = None):
    """Remove a connection. The runs it reported are kept."""
    _visible_or_404(connection_id, workspace)
    if not connection_store.delete_connection(connection_id):
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"deleted": True, "connection_id": connection_id}
