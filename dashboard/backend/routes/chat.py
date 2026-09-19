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
    events are missed, then pump the pipeline into the broker on a background task.
    """
    from common.session_broker import broker

    conv = request.conversation_id or str(uuid.uuid4())
    channel = f"chat:{conv}"
    client_id = request.client_id
    if client_id:
        broker.add_channel(client_id, channel)

    pipeline = _pipeline_for(request)

    async def pump():
        try:
            async for event in pipeline:
                await broker.apublish(channel, event)
        except Exception as e:  # surface a terminal error event to the client
            await broker.apublish(channel, {"type": "done", "ok": False, "error": str(e)})
        finally:
            # Sentinel so the client knows the run is over and can unsubscribe.
            await broker.apublish(channel, {"type": "chat_stream_end"})
            if client_id:
                broker.remove_channel(client_id, channel)

    task = asyncio.create_task(pump())
    _PUMP_TASKS.add(task)
    task.add_done_callback(_PUMP_TASKS.discard)
    return {"channel": channel, "conversation_id": conv}
