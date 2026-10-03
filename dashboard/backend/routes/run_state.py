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
    extra = dict(body.extra)
    if isinstance(extra.get("loop"), dict) and extra["loop"].get("tool_spills"):
        # Long tool outputs the container saved to files it could not
        # register without a database (agents/tool_spill.py).
        from agents.tool_spill import register_relayed_spills
        extra["loop"] = register_relayed_spills(run_id, extra["loop"])
    close_run_from_result(run_id, shim, **extra)
    return {"ok": True}


@router.post("/runs/{run_id}/heartbeat")
async def heartbeat_route(run_id: str):
    """Stamp the run's sign of life and hand back its status, so a run whose
    stop was requested from another host learns of it (the watchdog and
    ``managers.runs.store.touch_heartbeat`` are the other half)."""
    from managers.runs.store import touch_heartbeat

    status = touch_heartbeat(run_id)
    return {"ok": status is not None, "status": status}


class CheckpointBody(BaseModel):
    checkpoint: Dict[str, Any] = {}


@router.post("/runs/{run_id}/checkpoint")
async def save_checkpoint_route(run_id: str, body: CheckpointBody):
    """Store the agent loop's checkpoint (agents/checkpoint.py)."""
    from managers.runs.store import save_run_checkpoint

    save_run_checkpoint(run_id, body.checkpoint)
    return {"ok": True}


@router.get("/runs/{run_id}/checkpoint")
async def load_checkpoint_route(run_id: str):
    from managers.runs.store import load_run_checkpoint

    return {"checkpoint": load_run_checkpoint(run_id)}


class ClaimSteeringBody(BaseModel):
    step: int = 0


@router.post("/runs/{run_id}/steering/claim")
async def claim_steering_route(run_id: str, body: ClaimSteeringBody):
    """Mirrors ``common.steering.claim_pending``: the messages a person sent
    while the run worked, taken before its next model call
    (agents/loop_ext/steering.py). One indexed SELECT when there are none."""
    from common import steering

    return {"messages": steering.claim_pending(run_id, body.step)}


@router.get("/runs/{run_id}/steering/delivered")
async def delivered_steering_route(run_id: str):
    """Mirrors ``common.steering.delivered``, for a run that picks up again
    under its own id and needs the messages it already took."""
    from common import steering

    return {"messages": steering.delivered(run_id)}


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
    import asyncio

    from managers.run_manager import finalize_task_from_run

    # Finalizing may grade the task's outcome (tasks/outcome.py), a model
    # call: off the event loop, like every other blocking call here.
    await asyncio.to_thread(finalize_task_from_run, run_id, body.status, body.exit_code)
    return {"ok": True}


# ── delegation (tasks/delegate.py, tools/delegation.py) ──────────────────────
#
# A run that delegates from inside a container asks the backend to create the
# subtask and launch the delegate: launched here, the delegate gets its own
# container (or a place on the run queue in the ``api`` role) exactly like a
# launch from the dashboard, instead of a subprocess nested inside the
# parent's container; and it works when the container's state directory is
# read-only. The routes below are the four calls ``HttpStateTransport`` makes
# for that; ``DirectStateTransport`` calls the same functions in-process.

class DelegateBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    agent_id: str
    input: str
    title: Optional[str] = None
    model: Optional[str] = None
    workspace: Optional[str] = None
    caller_agent_id: Optional[str] = None
    depth: int = 0
    env: Dict[str, str] = {}
    #: The user the delegating run belongs to (``common.attribution.
    #: launching_user`` inside that run), so the child is attributed the way
    #: an in-process delegation attributes it, not to the relay's token.
    launched_by: Optional[str] = None


@router.post("/tasks/{task_id}/delegate")
async def delegate_route(task_id: str, body: DelegateBody):
    """Mirrors ``tasks.delegate.launch_delegation``: every check the tool
    makes (agent, workspace, allowlist, depth, model), the subtask, the
    launch. The answer is the function's own ``{"ok": ...}`` dict, refusals
    included, so the tool renders it the same either way."""
    import asyncio

    from tasks.delegate import launch_delegation

    def _launch() -> Dict[str, Any]:
        from common.identity import reset_current_user, set_current_user
        token = set_current_user(body.launched_by) if body.launched_by else None
        try:
            return launch_delegation(task_id, body.model_dump())
        finally:
            if token is not None:
                reset_current_user(token)

    return await asyncio.to_thread(_launch)


@router.get("/tasks/{task_id}/delegation")
async def delegation_status_route(task_id: str, run_id: Optional[str] = None):
    """Mirrors ``tasks.delegate.delegation_status``: what the waiting tool polls."""
    import asyncio

    from tasks.delegate import delegation_status

    return await asyncio.to_thread(delegation_status, task_id, run_id)


@router.get("/runs/{run_id}")
async def get_run_route(run_id: str):
    """One run record (``managers.run_manager.get_run_by_id``); the waiting
    tool reads its own parent's status with it."""
    from managers.run_manager import get_run_by_id

    return {"run": get_run_by_id(run_id)}


class StopRunBody(BaseModel):
    task_id: str


@router.post("/runs/{run_id}/stop")
async def stop_run_route(run_id: str, body: StopRunBody):
    """Mirrors ``managers.run_manager.stop_run``: a parent that was stopped
    while it waited stops its delegate."""
    from managers.run_manager import stop_run

    return {"ok": bool(stop_run(body.task_id, run_id))}
