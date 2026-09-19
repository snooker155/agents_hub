"""
Health & liveness endpoint.

``GET /api/health`` reports whether the database is reachable, the row counts of
the core stores, the liveness of every background service (plan scheduler, run
watchdog, Telegram poller, external-state publisher), and on-disk state sizes.
Gives operators one place to see that the moving parts are alive and how large
local state has grown, instead of inferring it from logs.

The snapshot itself lives in ``common.health`` so the Service Agent's
``service_health`` tool reports exactly what this endpoint does, without either
of them going through HTTP to reach the other.

The Service Agent's own chat lives here too, under ``/api/health/chat``. It is
the one build chat with nothing to build: the subject is the running service, so
the "entity" is a single fixed id rather than a row in a store.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from common.health import snapshot

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health(request: Request):
    """Liveness + state snapshot. Never raises: a failed probe is reported as such."""
    return snapshot(request.app.state)


# ── The Service Agent's chat ─────────────────────────────────────────────────

SERVICE_AGENT_ID = "service_agent"
SERVICE_CHAT_KIND = "service"
#: There is one service, so there is one conversation about it. Clearing the
#: chat starts a new session epoch rather than a new thread id.
SERVICE_CHAT_ID = "service"


class ServiceChatIn(BaseModel):
    message: str = ""


def _service_chat_prompt(history: List[dict], user_message: str,
                         health_now: Dict[str, Any]) -> str:
    """One turn's prompt: the live snapshot, then the talk.

    The snapshot is inlined rather than left for the agent to fetch, because it
    is the one call every investigation starts with and paying a tool round-trip
    for it on every turn is pure latency.
    """
    from chat.entity_chat import transcript_block

    parts = [
        "You are looking at this service's own health page. The user is looking "
        "at the same snapshot beside this chat.",
        "",
        "=== Health right now ===",
        json.dumps(health_now, ensure_ascii=False, indent=2, default=str),
        "",
        "Rules for this conversation:",
        "- The snapshot above is already current. Do not call service_health "
        "again unless you have reason to think something changed during the "
        "conversation.",
        "- Follow the symptom down with your other tools instead of speculating: "
        "nodes and containers for 'nothing runs', runs and logs for 'this "
        "failed', instances and sessions for 'it hung'.",
        "- Quote the evidence. Run ids, node ids and error lines are things the "
        "user can open; an unsourced diagnosis is a guess wearing a uniform.",
        "- Never stop, restart or delete anything without a clear yes in this "
        "conversation to that exact action.",
        "- If everything is healthy, say so in one line. Do not manufacture a "
        "report out of a quiet system.",
    ]
    talk = transcript_block(history[:-1])
    if talk:
        parts += ["", "=== Conversation so far ===", talk]
    parts += ["", "=== The user's latest message ===", user_message]
    return "\n".join(parts)


@router.get("/health/chat")
async def get_service_chat():
    """The Service Agent's transcript plus the rich replay trace."""
    from common.entity_chat_store import entity_chat_store

    chat_store = entity_chat_store()
    return {
        "messages": chat_store.get_messages(SERVICE_CHAT_KIND, SERVICE_CHAT_ID),
        "trace": chat_store.get_trace(SERVICE_CHAT_KIND, SERVICE_CHAT_ID),
        # What the session picker needs to reach this chat's history
        # (routes/entity_chats.py); the browser never builds the key itself.
        "chat_ref": {"kind": SERVICE_CHAT_KIND, "id": SERVICE_CHAT_ID},
    }


@router.delete("/health/chat")
async def clear_service_chat():
    """Clear the transcript and start a fresh session."""
    from common.entity_chat_store import entity_chat_store

    epoch = entity_chat_store().clear(SERVICE_CHAT_KIND, SERVICE_CHAT_ID, new_session=True)
    return {"cleared": True, "session_epoch": epoch}


@router.post("/health/chat")
async def chat_service(payload: ServiceChatIn, request: Request):
    """Run one turn of the Service Agent chat (SSE).

    Streams the agent's ``tool_*`` / ``thinking`` / ``token`` events, then a
    ``health`` event carrying the snapshot as it stands after the turn (an
    action may have changed it), the final ``message`` and ``done``.
    """
    from chat.entity_chat import (
        EntityChatSpec, RecordingQueue, SSE_HEADERS, guarded, relay_queue,
        run_entity_chat_turn, spawn_detached, sse,
    )
    from common.bootstrap import ensure_system_agent

    if not ensure_system_agent(SERVICE_AGENT_ID):
        raise HTTPException(status_code=503,
                            detail=f"The '{SERVICE_AGENT_ID}' agent is not registered")
    user_message = (payload.message or "").strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="Empty message")

    app_state = request.app.state
    health_now = snapshot(app_state)

    spec = EntityChatSpec(
        kind=SERVICE_CHAT_KIND,
        agent_id=SERVICE_AGENT_ID,
        title="Service health",
    )

    async def run_turn(queue: asyncio.Queue):
        await run_entity_chat_turn(
            queue, spec, SERVICE_CHAT_ID, user_message,
            lambda history: _service_chat_prompt(history, user_message, health_now),
        )
        # An action tool may have stopped something, so the page's snapshot is
        # refreshed from the turn rather than left stale until the next poll.
        await queue.put({"type": "health", "health": snapshot(app_state)})

    async def event_stream():
        queue = RecordingQueue()
        yield sse({"type": "meta", "kind": SERVICE_CHAT_KIND, "id": SERVICE_CHAT_ID})
        worker = spawn_detached(guarded(run_turn, queue))
        async for frame in relay_queue(queue):
            yield frame
        await worker

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers=SSE_HEADERS)


@router.post("/health/chat/stop")
async def stop_service_chat():
    """Stop the in-flight Service Agent turn."""
    from chat.entity_chat import cancel_entity_runs

    cancelled = cancel_entity_runs(SERVICE_CHAT_KIND, SERVICE_CHAT_ID)
    return {"stopped": cancelled > 0, "cancelled": cancelled}
