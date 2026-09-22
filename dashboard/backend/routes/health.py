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

import json
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request

from chat.entity_chat import EntityChatSpec
from chat.entity_chat_router import EntityChatRoute, build_entity_chat_router
from common.health import snapshot

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health(request: Request):
    """Liveness + state snapshot. Never raises: a failed probe is reported as such."""
    from common.config import playground_enabled

    result = snapshot(request.app.state)
    # Optional-feature facts, kept separate from the liveness/state data above:
    # the frontend reads this to hide a feature's pages rather than probing
    # its routes. See docs/playground.md, "Turning the playground off".
    result["features"] = {"playground": playground_enabled()}
    return result


# ── The Service Agent's chat ─────────────────────────────────────────────────

SERVICE_AGENT_ID = "service_agent"
SERVICE_CHAT_KIND = "service"
#: There is one service, so there is one conversation about it. Clearing the
#: chat starts a new session epoch rather than a new thread id.
SERVICE_CHAT_ID = "service"


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


def _load_service_chat(request):
    """The service chat has one fixed entity — nothing to look up, nothing
    that can 404 — so this just hands back the constant id every hook needs."""
    from types import SimpleNamespace
    return SimpleNamespace(entity_id=SERVICE_CHAT_ID)


def _load_service_send(request: Request, body):
    """Same as ``_load_service_chat``, plus the checks only a turn needs: the
    agent must be registered, and the prompt wants this turn's own snapshot."""
    from common.bootstrap import ensure_system_agent

    if not ensure_system_agent(SERVICE_AGENT_ID):
        raise HTTPException(status_code=503,
                            detail=f"The '{SERVICE_AGENT_ID}' agent is not registered")
    ctx = _load_service_chat(request)
    ctx.app_state = request.app.state
    ctx.health_now = snapshot(ctx.app_state)
    return ctx


async def _service_post_turn(queue, ctx):
    # An action tool may have stopped something, so the page's snapshot is
    # refreshed from the turn rather than left stale until the next poll.
    await queue.put({"type": "health", "health": snapshot(ctx.app_state)})


router.include_router(build_entity_chat_router(EntityChatRoute(
    kind=SERVICE_CHAT_KIND,
    path="/health/chat",
    load=_load_service_chat,
    load_for_send=_load_service_send,
    prompt=lambda ctx, history, msg: _service_chat_prompt(history, msg, ctx.health_now),
    spec=lambda ctx: EntityChatSpec(
        kind=SERVICE_CHAT_KIND, agent_id=SERVICE_AGENT_ID, title="Service health",
    ),
    post_turn=_service_post_turn,
)))
