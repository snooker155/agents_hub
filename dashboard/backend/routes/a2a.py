"""A2A (Agent2Agent) server endpoints: every agent in this hub, as an A2A agent.

The hub already exposes its agents over its own HTTP API. A2A is the open
protocol for the same thing, so an orchestrator built on somebody else's stack
can drive an agent here without knowing anything about this API: it reads an
Agent Card, posts JSON-RPC, and reads Tasks back.

Three surfaces:

    GET  /.well-known/agent-card.json                     the default chat agent
    GET  /api/a2a/agents/{id}/.well-known/agent-card.json  one agent's card
    POST /api/a2a/agents/{id}                              JSON-RPC 2.0

The protocol itself (envelopes, error codes, the hub-to-A2A state mapping) is
in :mod:`a2a.server` as pure functions. What lives here is the I/O: creating the
task, launching the run exactly as the dashboard's own routes do
(``agents.agent_launcher.start_run``), polling it, stopping it, and subscribing
to its session channel for ``message/stream``.

This router carries no prefix because one of its paths is at the site root: the
well-known card path is fixed by the specification and cannot sit under
``/api``. Every other path spells out ``/api/a2a`` itself.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from a2a import card as a2a_card
from a2a import server as a2a
from agents import registry

router = APIRouter(tags=["a2a"])

AGENT_PREFIX = "/api/a2a/agents"

#: How long a blocking ``message/send`` waits for the run before it gives up and
#: returns the task as it stands. Override with AGENTS_HUB_A2A_BLOCKING_TIMEOUT.
#: The caller keeps the task id either way, so a timeout costs a poll, not work.
DEFAULT_BLOCKING_TIMEOUT = 120.0
#: How often the blocking path re-reads the run record.
POLL_INTERVAL = 0.5
#: Upper bound on one ``message/stream`` connection, so a run that never
#: finishes cannot hold a socket open forever.
STREAM_TIMEOUT = 3600.0

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _blocking_timeout() -> float:
    raw = (os.environ.get("AGENTS_HUB_A2A_BLOCKING_TIMEOUT") or "").strip()
    try:
        return max(1.0, float(raw)) if raw else DEFAULT_BLOCKING_TIMEOUT
    except ValueError:
        return DEFAULT_BLOCKING_TIMEOUT


# ── the card ────────────────────────────────────────────────────────────────

def _base_url(request: Request) -> str:
    """The absolute base URL to publish in a card.

    A card's ``url`` is the address a *foreign* client will post to, so it has
    to be the address the request arrived at from outside. Behind a reverse
    proxy that is what the ``X-Forwarded-*`` headers say and not what the socket
    saw, and a card advertising ``http://127.0.0.1:8000`` is useless to everyone
    but the machine that served it.
    """
    forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    if forwarded_host:
        scheme = forwarded_proto or request.url.scheme
        return f"{scheme}://{forwarded_host}"
    return str(request.base_url).rstrip("/")


def _agent_version(agent_id: str) -> str:
    """The agent's definition version, for the card's ``version`` field.

    Version history is the closest thing the hub has to a released version of an
    agent, and it is what changes when the agent's behaviour changes. An agent
    that has never been edited has no history, and "1" is then both true and
    what the spec expects (the field is required and free-form).
    """
    try:
        from agents.versions import list_versions
        versions = list_versions(agent_id)
        if versions:
            return str(versions[-1].get("version") or "1")
    except Exception:
        pass
    return "1"


def _token_required() -> bool:
    try:
        from common.config import settings
        return bool(settings.api_token)
    except Exception:
        return False


def _card_for(agent_id: str, request: Request) -> Dict[str, Any]:
    spec = registry.get_agent(agent_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return a2a_card.card_from_spec(
        spec,
        _base_url(request),
        version=_agent_version(agent_id),
        token_required=_token_required(),
    )


@router.get(AGENT_PREFIX + "/{agent_id}/.well-known/agent-card.json")
async def agent_card(agent_id: str, request: Request):
    """The A2A Agent Card for one agent of this hub."""
    return _card_for(agent_id, request)


@router.get(AGENT_PREFIX + "/{agent_id}/.well-known/agent.json")
async def agent_card_legacy(agent_id: str, request: Request):
    """The same card at the pre-0.3 path, for clients that still ask for it."""
    return _card_for(agent_id, request)


def _default_agent_id() -> Optional[str]:
    """The agent the site-root card describes, or None.

    The workspace's default chat agent is the one answer that is not arbitrary:
    it is the agent this hub puts in front of a person who has not chosen one,
    so it is the agent it should put in front of a client that has not either.
    """
    try:
        from workspace.storage import _default_chat_agent_id
        return _default_chat_agent_id()
    except Exception:
        return None


async def _root_card(request: Request):
    agent_id = _default_agent_id()
    if not agent_id:
        # A 404 with directions rather than a bare one: the cards exist, they
        # are just per agent, and a client that landed here has no other way to
        # find that out.
        return JSONResponse(
            status_code=404,
            content={
                "detail": (
                    "This hub serves no default agent card. Every agent has its own at "
                    f"{_base_url(request)}{AGENT_PREFIX}/<agent_id>/.well-known/agent-card.json"
                ),
                "agents_url": f"{_base_url(request)}/api/agents",
            },
        )
    return _card_for(agent_id, request)


@router.get("/.well-known/agent-card.json")
async def root_card(request: Request):
    """The card of the hub's default chat agent, if it has one."""
    return await _root_card(request)


@router.get("/.well-known/agent.json")
async def root_card_legacy(request: Request):
    """The same, at the pre-0.3 path."""
    return await _root_card(request)


# ── JSON-RPC ────────────────────────────────────────────────────────────────

def _require_agent(agent_id: str):
    spec = registry.get_agent(agent_id)
    if spec is None:
        raise a2a.JsonRpcError(a2a.INVALID_PARAMS, f"agent '{agent_id}' is not registered here")
    return spec


def _hub_task(task_id: str):
    """The hub task behind an A2A task id, or a TaskNotFound refusal."""
    from tasks import service as tasks_service

    try:
        uuid = UUID(str(task_id))
    except (ValueError, AttributeError, TypeError):
        raise a2a.JsonRpcError(a2a.TASK_NOT_FOUND, f"no task '{task_id}'")
    task = tasks_service.get_task(uuid)
    if task is None:
        raise a2a.JsonRpcError(a2a.TASK_NOT_FOUND, f"no task '{task_id}'")
    return task


def _latest_run(task_id: str) -> Dict[str, Any]:
    """The newest run of a hub task, or ``{}`` when it has not started one."""
    from managers import run_manager

    try:
        page = run_manager.query_runs(task_id=str(task_id), limit=1)
    except Exception:
        return {}
    items = page.get("items") or []
    return items[0] if items else {}


def _status_of(task) -> str:
    """The task's status as the plain word, not as an enum repr.

    ``TaskStatus`` is a str-mixin enum, and ``str()`` on one of those yields
    ``TaskStatus.awaiting_input`` rather than ``awaiting_input``, which would
    silently miss every mapping in :mod:`a2a.server` and report a task that is
    waiting on a person as still working.
    """
    status = getattr(task, "status", "")
    return str(getattr(status, "value", status) or "")


def _task_payload(task, run: Dict[str, Any]) -> Dict[str, Any]:
    """The A2A Task for one hub task plus its latest run."""
    state = a2a.state_for(str(run.get("status") or ""), _status_of(task))

    status_text = ""
    if state == a2a.INPUT_REQUIRED:
        pending = getattr(task, "pending_question", None) or {}
        approval = getattr(task, "pending_approval", None) or {}
        status_text = str(pending.get("question") or "").strip()
        if not status_text and approval:
            status_text = (
                f"The agent is waiting for approval to call '{approval.get('tool') or 'a tool'}'."
            )
    elif state == a2a.FAILED:
        status_text = str(run.get("error") or getattr(task, "blocked_reason", "") or "the run failed")

    output = str(run.get("output") or "") if state == a2a.COMPLETED else ""
    return a2a.build_task(
        task_id=str(task.id),
        context_id=str(run.get("session_id") or getattr(task, "session_id", "") or ""),
        state=state,
        status_text=status_text,
        artifact_text=output,
        metadata={"runId": str(run.get("run_id") or ""), "agentId": str(run.get("agent_id") or "")},
    )


def _start(agent_id: str, spec, text: str, params: Dict[str, Any]) -> tuple[str, str, str]:
    """Create the hub task and launch the run. Returns (task_id, run_id, session_id).

    Deliberately the same three calls the dashboard's own "run this task" route
    makes, in the same order: an A2A caller must land in the task list, the run
    list and the cost accounting exactly like anybody else, or the protocol
    becomes a side door with its own bookkeeping.
    """
    from agents import agent_launcher
    from tasks import CreatedBy, TaskStatus
    from tasks import service as tasks_service

    workspace = str(a2a.metadata_of(params).get("workspace") or "").strip() or spec.owner_workspace

    task = tasks_service.create_task(
        title=text[:120],
        description=text,
        workspace=workspace,
        status=TaskStatus.ready,
        created_by=CreatedBy.external,
    )
    launch_params = {"description": text}
    try:
        run_id, session_id = agent_launcher.start_run(str(task.id), agent_id, launch_params)
    except Exception as exc:  # noqa: BLE001 - a launch refusal is a protocol error, not a 500
        raise a2a.JsonRpcError(a2a.INTERNAL_ERROR, f"could not start a run: {exc}")
    tasks_service.assign_agent(task.id, agent_id, launch_params, run_id=run_id)
    return str(task.id), str(run_id), str(session_id or "")


async def _await_run(task_id: str, run_id: str, timeout: float) -> None:
    """Poll the run record until it reaches a terminal state or time runs out."""
    from managers import run_manager

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = run_manager.get_run_by_id(run_id) or {}
        state = a2a.state_for(str(run.get("status") or ""))
        if a2a.is_terminal(state) or state == a2a.INPUT_REQUIRED:
            return
        # A task can park on a question while its run record still reads
        # running, so the task is checked too. Waiting out the full timeout on
        # a conversation that is waiting on us is the worst possible answer.
        from tasks import service as tasks_service

        task = tasks_service.get_task(UUID(task_id))
        if task is not None and a2a.state_for("", _status_of(task)) == a2a.INPUT_REQUIRED:
            return
        await asyncio.sleep(POLL_INTERVAL)


def _handle_send(agent_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """``message/send`` up to the point where waiting begins."""
    spec = _require_agent(agent_id)
    message = a2a.require_message(params)
    if message.get("taskId"):
        raise a2a.JsonRpcError(
            a2a.UNSUPPORTED_OPERATION,
            "continuing an existing task is not implemented; send a new message instead",
        )
    text = a2a.message_text(message)
    task_id, run_id, session_id = _start(agent_id, spec, text, params)
    return {"task_id": task_id, "run_id": run_id, "session_id": session_id, "text": text}


async def _message_send(agent_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    started = _handle_send(agent_id, params)
    if a2a.blocking_requested(params):
        await _await_run(started["task_id"], started["run_id"], _blocking_timeout())
        return _task_payload(_hub_task(started["task_id"]), _latest_run(started["task_id"]))

    return a2a.build_task(
        task_id=started["task_id"],
        context_id=started["session_id"],
        state=a2a.SUBMITTED,
        metadata={"runId": started["run_id"], "agentId": agent_id},
    )


def _tasks_get(params: Dict[str, Any]) -> Dict[str, Any]:
    task_id = a2a.require_task_id(params)
    task = _hub_task(task_id)
    return _task_payload(task, _latest_run(task_id))


def _tasks_cancel(params: Dict[str, Any]) -> Dict[str, Any]:
    from managers import run_manager

    task_id = a2a.require_task_id(params)
    task = _hub_task(task_id)
    run = _latest_run(task_id)
    state = a2a.state_for(str(run.get("status") or ""), _status_of(task))
    if a2a.is_terminal(state):
        raise a2a.JsonRpcError(
            a2a.TASK_NOT_CANCELABLE, f"task '{task_id}' is already '{state}'"
        )
    if not run_manager.stop_run(task_id, run.get("run_id")):
        raise a2a.JsonRpcError(
            a2a.TASK_NOT_CANCELABLE, f"task '{task_id}' has no run that can be stopped"
        )
    return a2a.build_task(
        task_id=task_id,
        context_id=str(run.get("session_id") or ""),
        state=a2a.CANCELED,
        metadata={"runId": str(run.get("run_id") or "")},
    )


async def _message_stream(
    agent_id: str,
    params: Dict[str, Any],
    request_id: Any,
    request: Request,
) -> StreamingResponse:
    """``message/stream``: start the run, then relay its session channel as SSE.

    The hub's own stream is the source: a run publishes tokens and a closing
    frame to its session channel (see :mod:`common.session_broker`), and this
    translates those into the two event kinds A2A has. Nothing is buffered, so a
    client sees the agent's words at the same moment the dashboard does.
    """
    from common.session_broker import broker

    started = _handle_send(agent_id, params)
    task_id, session_id = started["task_id"], started["session_id"]

    async def _generate():
        # The Task first, so a client has the id before any update refers to it.
        yield a2a.sse_frame(
            a2a.build_task(task_id=task_id, context_id=session_id, state=a2a.SUBMITTED,
                           metadata={"runId": started["run_id"], "agentId": agent_id}),
            request_id,
        )
        if not session_id:
            # No channel to follow: report where the run ended up rather than
            # holding a connection open on a stream that will never exist.
            task = _hub_task(task_id)
            payload = _task_payload(task, _latest_run(task_id))
            yield a2a.sse_frame(
                a2a.status_event(task_id, session_id, payload["status"]["state"], final=True),
                request_id,
            )
            return

        events = broker.subscribe(session_id)
        finished = False
        deadline = time.monotonic() + STREAM_TIMEOUT
        try:
            async for event in events:
                if await request.is_disconnected():
                    break
                if time.monotonic() > deadline:
                    # The broker sends a heartbeat every 20 seconds, so this is
                    # reached on a run that is merely long rather than only on
                    # one that is producing output.
                    yield a2a.sse_frame(
                        a2a.status_event(
                            task_id, session_id, a2a.UNKNOWN, final=True,
                            text="the hub stopped following this run; poll tasks/get for its outcome",
                        ),
                        request_id,
                    )
                    finished = True
                    break
                for out in a2a.events_for_hub_frame(event, task_id=task_id, context_id=session_id):
                    yield a2a.sse_frame(out, request_id)
                    if out.get("kind") == "status-update" and out.get("final"):
                        finished = True
                if finished:
                    break
        finally:
            await events.aclose()

        if not finished:
            payload = _task_payload(_hub_task(task_id), _latest_run(task_id))
            yield a2a.sse_frame(
                a2a.status_event(task_id, session_id, payload["status"]["state"], final=True),
                request_id,
            )

    return StreamingResponse(_generate(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post(AGENT_PREFIX + "/{agent_id}")
async def jsonrpc(agent_id: str, request: Request):
    """The JSON-RPC 2.0 endpoint of one agent.

    Every refusal is a JSON-RPC error object with the code the spec assigns,
    carried in an HTTP 200: an A2A client reads the envelope, and a bare 4xx
    with FastAPI's own body tells it nothing it can act on.
    """
    request_id = None
    try:
        raw = await request.body()
        try:
            body = json.loads(raw or b"")
        except ValueError as exc:
            raise a2a.JsonRpcError(a2a.PARSE_ERROR, f"the request body is not JSON: {exc}")

        method, params, request_id = a2a.parse_request(body)

        if method == "message/stream":
            return await _message_stream(agent_id, params, request_id, request)
        if method == "message/send":
            return a2a.result_response(request_id, await _message_send(agent_id, params))
        if method == "tasks/get":
            _require_agent(agent_id)
            return a2a.result_response(request_id, _tasks_get(params))
        if method == "tasks/cancel":
            _require_agent(agent_id)
            return a2a.result_response(request_id, _tasks_cancel(params))
        # parse_request already refused anything else; this is unreachable and
        # says so rather than falling through to a silent None.
        raise a2a.JsonRpcError(a2a.METHOD_NOT_FOUND, f"method '{method}' is not supported")
    except a2a.JsonRpcError as exc:
        return JSONResponse(status_code=200, content=a2a.error_from(exc, request_id))
    except Exception as exc:  # noqa: BLE001 - the protocol has a code for this
        return JSONResponse(
            status_code=200,
            content=a2a.error_response(request_id, a2a.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"),
        )
