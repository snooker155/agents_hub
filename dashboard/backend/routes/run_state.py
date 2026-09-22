"""
/api/run-state: the write surface a run's own entrypoint needs when it cannot
open the shared database itself.

``runtime/agent_run.py`` normally calls ``managers.run_manager`` and
``tasks.*`` functions in-process (``common.state_transport.DirectStateTransport``).
A run container started with ``AGENT_RUN_STATE_TRANSPORT=http`` gets its
``.agents_hub`` mount read-only instead (see ``managers.container_manager.
build_run_command``) and uses ``HttpStateTransport``, which posts here.

Every handler below calls the exact same function the direct transport calls,
with the exact same arguments: this router does not reimplement any of the
run/task lifecycle logic, it only receives it over HTTP and executes it where
a database connection is actually available. Authenticated like every other
``/api`` route: the global bearer-token middleware in ``dashboard/backend/
main.py`` already covers this prefix, nothing extra is added here.

See docs/containers.md for what this transport covers and what still needs a
writable state directory.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

router = APIRouter(prefix="/api/run-state", tags=["run-state"])


# ── run records ──────────────────────────────────────────────────────────────

class OpenRunBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    agent_id: str
    task_id: Optional[str] = None
    session_id: Optional[str] = None
    session_type: Optional[str] = None
    log_file: Optional[str] = None
    workspace: Optional[str] = None
    title: Optional[str] = None
    message_origin: Optional[str] = None
    pid: Optional[int] = None
    status: str = "running"
    execution_mode: Optional[str] = None
    channel: Optional[str] = None
    instance_id: Optional[str] = None


@router.post("/runs/{run_id}/open")
async def open_run_route(run_id: str, body: OpenRunBody):
    """Create the run record. Mirrors ``managers.run_manager.open_run``."""
    from managers.run_manager import open_run

    kwargs = body.model_dump(exclude={"agent_id"}, exclude_none=True)
    open_run(run_id, body.agent_id, **kwargs)
    return {"ok": True}


class UpdateRunBody(BaseModel):
    updates: Dict[str, Any] = {}


@router.patch("/runs/{run_id}")
async def update_run_route(run_id: str, body: UpdateRunBody):
    """Apply partial updates to a run record. Mirrors
    ``managers.run_manager.update_run``, also what ``seed_run_input_context``
    and a run's provider/model/input bookkeeping reduce to on the wire."""
    from managers.run_manager import update_run

    update_run(run_id, body.updates)
    return {"ok": True}


class PayloadBody(BaseModel):
    process: Dict[str, Any] = {}


@router.post("/runs/{run_id}/payload")
async def payload_route(run_id: str, body: PayloadBody):
    """Store a run's heavy structured payload. Equivalent to
    ``update_run(run_id, {"process": ...})``, kept as its own route for the
    payload specifically (see docs/containers.md)."""
    from managers.run_manager import update_run

    update_run(run_id, {"process": body.process})
    return {"ok": True}


class _ResponseShim:
    """Stands in for an ``AgentResult.response`` object on this side of the
    wire: the container already reduced it to ``to_payload()``'s output, so
    all this needs to do is hand that back out the same way."""

    def __init__(self, payload: Dict[str, Any]):
        self._payload = payload

    def to_payload(self) -> Dict[str, Any]:
        return self._payload


class _ResultShim:
    """Stands in for the ``AgentInvocation.result`` object
    ``close_run_from_result`` expects: anything exposing ``.ok``,
    ``.agent_output``, ``.error`` and (optionally) ``.response``. The container
    already derived these fields from the real result object (see
    ``HttpStateTransport.close_run_from_result``); this reconstructs just
    enough of the shape for the existing derivation logic to run unchanged."""

    def __init__(self, ok: bool, agent_output: Optional[str], error: Optional[str],
                 response_payload: Optional[Dict[str, Any]]):
        self.ok = ok
        self.agent_output = agent_output
        self.error = error
        self.response = _ResponseShim(response_payload) if response_payload is not None else None


class CloseRunBody(BaseModel):
    ok: bool
    agent_output: Optional[str] = None
    error: Optional[str] = None
    response_payload: Optional[Dict[str, Any]] = None
    extra: Dict[str, Any] = {}


@router.post("/runs/{run_id}/close")
async def close_run_route(run_id: str, body: CloseRunBody):
    """Close a run, deriving status/exit_code/error/output the same way
    ``managers.run_manager.close_run_from_result`` always has, from a result
    object, here reconstructed as ``_ResultShim`` from what the container sent."""
    from managers.run_manager import close_run_from_result

    shim = _ResultShim(body.ok, body.agent_output, body.error, body.response_payload)
    close_run_from_result(run_id, shim, **body.extra)
    return {"ok": True}


# ── task-side transitions ────────────────────────────────────────────────────

class PersistTaskResultBody(BaseModel):
    run_id: str
    output: str = ""
    agent_id: Optional[str] = None


@router.post("/tasks/{task_id}/result")
async def persist_task_result_route(task_id: str, body: PersistTaskResultBody):
    """Mirrors ``tasks.context.persist_task_result``."""
    from tasks.context import persist_task_result

    persist_task_result(task_id, body.run_id, body.output, agent_id=body.agent_id)
    return {"ok": True}


class ParkAwaitingInputBody(BaseModel):
    question: Dict[str, Any] = {}
    agent_id: str = ""


@router.post("/runs/{run_id}/park-awaiting-input")
async def park_awaiting_input_route(run_id: str, body: ParkAwaitingInputBody):
    """Mirrors ``managers.run_manager.park_task_awaiting_input``."""
    from managers.run_manager import park_task_awaiting_input

    park_task_awaiting_input(run_id, body.question, agent_id=body.agent_id)
    return {"ok": True}


class ParkAwaitingApprovalBody(BaseModel):
    pending: Dict[str, Any] = {}
    run_id: str = ""
    agent_id: str = ""


@router.post("/tasks/{task_id}/park-awaiting-approval")
async def park_awaiting_approval_route(task_id: str, body: ParkAwaitingApprovalBody):
    """Mirrors ``tasks.service.park_task_awaiting_approval``."""
    from tasks.service import park_task_awaiting_approval

    try:
        tid = UUID(str(task_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid task_id")
    park_task_awaiting_approval(tid, body.pending, run_id=body.run_id, agent_id=body.agent_id)
    return {"ok": True}


class FinalizeTaskBody(BaseModel):
    status: str
    exit_code: int


@router.post("/runs/{run_id}/finalize-task")
async def finalize_task_route(run_id: str, body: FinalizeTaskBody):
    """Mirrors ``managers.run_manager.finalize_task_from_run``.

    Runs entirely on the backend, same as every other handler here, including
    everything it may itself trigger (auto-retry, auto-review, session
    continuations), which is why this is safe to call from inside a run
    container: none of that spawns *inside* the container, it spawns from the
    backend process the same way a local run's finalize always has.
    """
    from managers.run_manager import finalize_task_from_run

    finalize_task_from_run(run_id, body.status, body.exit_code)
    return {"ok": True}
