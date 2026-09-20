"""
Chat API router – direct in-process conversation with an agent.

Each call receives the full conversation history so the agent has context.
The agent is created fresh per message using the factory (YAML-based agents only).
Remote agents are not supported for chat.

No running node is required — each message runs the agent on-request inside the
server process. Each message exchange is recorded as a run in the ``runs`` table
so it appears in the Sessions list, complete with a log file.

The two endpoints:
- ``POST /api/chat/message`` — blocking single-agent request/response.
- ``POST /api/chat/stream``  — SSE stream of tokens + execution events, routed to
  the single-agent, flow or team pipeline depending on which target the request
  names (``agent_id`` / ``flow_id`` / ``team_id``).
"""
import asyncio
import json
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

# Strong references to in-flight background pump tasks. asyncio keeps only weak
# references to tasks created with create_task, so a long flow-chat run could be
# garbage-collected mid-stream and vanish without a terminal event. Holding the
# task here until it finishes prevents that.
_PUMP_TASKS: set[asyncio.Task] = set()

from models import ChatRequest

from chat.pipelines import run_chat_pipeline, run_chat_flow_pipeline, run_chat_team_pipeline
from chat.send import send_chat_message, ChatSendError

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/message")
async def send_message(request: ChatRequest):
    """Send a message to an agent and get a response.

    The turn itself lives in :func:`chat.send.send_chat_message`, so the
    terminal client runs the identical orchestration; this maps its failures
    onto HTTP.
    """
    try:
        return await send_chat_message(request)
    except ChatSendError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


def _pipeline_for(request: ChatRequest):
    """Pick the execution pipeline for a chat turn: a team (a roster that talks),
    a flow (a DAG), or one agent. Shared by both streaming transports so a target
    never behaves differently depending on which one the browser used."""
    if request.team_id:
        return run_chat_team_pipeline(request)
    if request.flow_id:
        return run_chat_flow_pipeline(request)
    return run_chat_pipeline(request)


@router.post("/stream")
async def stream_message(request: ChatRequest):
    """Stream chat response tokens and execution events (SSE)."""
    pipeline = _pipeline_for(request)

    async def event_stream():
        async for event in pipeline:
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/stream-sse")
async def stream_message_sse(request: ChatRequest):
    """Run a chat turn and deliver its events over the browser's single existing
    multiplexed SSE connection (/api/stream) instead of a dedicated streaming
    response.

    The browser caps concurrent connections per origin (~6 over HTTP/1.1), shared
    across all tabs. A flow chat's streaming POST is held for the entire run —
    minutes with a reasoning model — and with one SSE per open tab that pool is
    quickly exhausted, stalling requests everywhere. Here the POST returns
    immediately; events are published to a per-conversation broker channel that
    the caller's already-open SSE delivers, so a tab holds just one connection.

    The caller passes its SSE ``client_id``; we subscribe that client to
    ``chat:{conversation_id}`` synchronously *before* starting the run so no early
    events are missed, then drive the pipeline on a background task.

    The publishing itself is not done here: every pipeline puts its events on the
    conversation's channel already (``chat.broadcast``), which is what lets a
    second tab, another device or the run's own page follow the same turn. This
    endpoint only has to subscribe the caller and keep the run going after the
    POST has returned.
    """
    from chat.broadcast import channel_for
    from common.session_broker import broker

    conv = request.conversation_id or str(uuid.uuid4())
    request.conversation_id = conv
    channel = channel_for(conv)
    # Subscribing here as well as in the browser covers the gap between the POST
    # and the client's own channel request. It is never unsubscribed from this
    # side: the browser holds the channel for as long as the conversation is
    # open, and dropping it at the end of one turn would cut that off.
    if request.client_id:
        broker.add_channel(request.client_id, channel)

    pipeline = _pipeline_for(request)

    async def drive():
        # The events reach the browser through the broadcaster; draining the
        # generator here is what makes the turn run at all.
        try:
            async for _event in pipeline:
                pass
        except Exception:
            # The broadcaster has already published the terminal error event.
            pass

    task = asyncio.create_task(drive())
    _PUMP_TASKS.add(task)
    task.add_done_callback(_PUMP_TASKS.discard)
    return {"channel": channel, "conversation_id": conv}
