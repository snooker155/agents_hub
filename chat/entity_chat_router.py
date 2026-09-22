"""
Entity chat router factory — the four routes every entity chat exposes
(history, clear, send, stop), built once instead of copied per kind.

Ten pages across the dashboard each grew the same four handlers around
``chat.entity_chat``: a GET for the transcript, a DELETE that clears it, a POST
that runs one turn over SSE, and a POST ``.../stop`` that cancels an in-flight
one. The four are identical plumbing; what differs between a loop, a team, a
memory pool and a page chat is how the entity is loaded (and whether "not
found" is even a concept for it), what its prompt looks like, and what — if
anything — it reports back besides the reply. Those differences are hooks on
:class:`EntityChatRoute`; this module owns the wiring around them.

A call site looks like::

    router.include_router(build_entity_chat_router(EntityChatRoute(
        kind=TEAM_CHAT_KIND,
        path="/{team_id}/chat",
        load=_load_team_chat,
        prompt=lambda ctx, history, msg: _team_chat_prompt(ctx.team, history, msg),
        spec=lambda ctx: EntityChatSpec(kind=TEAM_CHAT_KIND, agent_id=TEAM_AGENT_ID,
                                        title=f"{ctx.team.name} · team", workspace=ctx.workspace),
        summarize=_team_summarize,
        context_setup=_team_context_setup,
        post_turn=_team_post_turn,
    )))

replacing the four ``@router.get/delete/post`` copies that used to sit there.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from chat.entity_chat import (
    EntityChatSpec, RecordingQueue, SSE_HEADERS, guarded, relay_queue,
    run_entity_chat_turn, spawn_detached, sse,
)


@dataclass
class EntityChatRoute:
    """One entity kind's four chat routes, for :func:`build_entity_chat_router`.

    ``path`` is the route suffix registered on the caller's own router (which
    already carries that file's prefix). Keep it identical to what the copy it
    replaces used, including where it sits relative to a sibling catch-all —
    a path whose segment count collides with one (``/evals/{eval_set_id}`` vs
    ``/evals/chat``, ``/{memory_id}`` vs ``/chat``) has to be registered first,
    same as the handler it replaces was.

    ``load`` resolves everything the GET / DELETE / stop routes need from one
    request — path params, query params, and, for a kind where "not found" is
    a real state, the entity itself (raising ``HTTPException`` when it is
    missing). Its return value is opaque to this module: every other hook gets
    it back and reads whatever it put there, by convention always including
    ``.entity_id`` — the store key passed to ``run_entity_chat_turn``.

    ``load_for_send`` is the same resolution for the turn-sending POST, which
    alone sees a request body; a kind whose body carries nothing ``load``
    doesn't already cover (the common case) leaves this as ``None`` and reuses
    ``load``. ``load_for_stop`` likewise defaults to ``load`` — override it
    only for a kind whose stop route skips the existence check the others do.
    """

    kind: str
    path: str
    load: Callable[[Request], Any]
    prompt: Callable[[Any, List[dict], str], str]
    spec: Callable[[Any], EntityChatSpec]
    load_for_send: Optional[Callable[[Request, Dict[str, Any]], Any]] = None
    load_for_stop: Optional[Callable[[Request], Any]] = None
    summarize: Optional[Callable[[Any], Optional[Callable[[], str]]]] = None
    context_setup: Optional[Callable[[Any], None]] = None
    #: Awaited right after the turn finishes, e.g. to emit a kind-specific
    #: "here is the entity now" event (``{"type": "team", "team": ...}``).
    post_turn: Optional[Callable[[asyncio.Queue, Any], Awaitable[None]]] = None
    #: The world/scenario build chats report every tool's effect the moment it
    #: lands, not just at the end of the turn — this returns the tap function
    #: (see ``RecordingQueue``) for one send, or None for the common case.
    tap: Optional[Callable[[Any], Optional[Callable[[dict], Any]]]] = None
    #: Extra keys merged into the GET response (memory's open-pool card).
    meta_extra: Optional[Callable[[Any], Dict[str, Any]]] = None


def build_entity_chat_router(route: EntityChatRoute) -> APIRouter:
    """Build the GET / DELETE / POST / POST-stop routes for one entity chat.

    Returned as its own router so a call site does
    ``router.include_router(build_entity_chat_router(...))`` at the exact spot
    the four handlers it replaces used to sit.
    """
    kind = route.kind
    load_for_send = route.load_for_send or (lambda request, body: route.load(request))
    load_for_stop = route.load_for_stop or route.load
    sub = APIRouter()

    @sub.get(route.path)
    async def _get_chat(request: Request):
        from common.entity_chat_store import entity_chat_store

        ctx = route.load(request)
        store = entity_chat_store()
        out: Dict[str, Any] = {
            "messages": store.get_messages(kind, ctx.entity_id),
            "trace": store.get_trace(kind, ctx.entity_id),
            # What the session picker needs to reach this chat's history
            # (routes/entity_chats.py); the browser never builds the key itself.
            "chat_ref": {"kind": kind, "id": ctx.entity_id},
        }
        if route.meta_extra:
            out.update(route.meta_extra(ctx))
        return out

    @sub.delete(route.path)
    async def _clear_chat(request: Request):
        from common.entity_chat_store import entity_chat_store

        ctx = route.load(request)
        epoch = entity_chat_store().clear(kind, ctx.entity_id, new_session=True)
        return {"cleared": True, "session_epoch": epoch}

    @sub.post(route.path)
    async def _send_chat(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}

        ctx = load_for_send(request, body)
        user_message = str(body.get("message") or "").strip()
        if not user_message:
            raise HTTPException(status_code=400, detail="Empty message")

        chat_spec = route.spec(ctx)
        summarize = route.summarize(ctx) if route.summarize else None

        async def run_turn(queue: asyncio.Queue) -> None:
            if route.context_setup:
                route.context_setup(ctx)
            await run_entity_chat_turn(
                queue, chat_spec, ctx.entity_id, user_message,
                lambda history: route.prompt(ctx, history, user_message),
                summarize=summarize,
            )
            if route.post_turn:
                await route.post_turn(queue, ctx)

        async def event_stream():
            tap = route.tap(ctx) if route.tap else None
            queue = RecordingQueue(tap=tap)
            yield sse({"type": "meta", "kind": kind, "id": ctx.entity_id})
            worker = spawn_detached(guarded(run_turn, queue))
            async for frame in relay_queue(queue):
                yield frame
            await worker

        return StreamingResponse(event_stream(), media_type="text/event-stream",
                                 headers=SSE_HEADERS)

    @sub.post(route.path.rstrip("/") + "/stop")
    async def _stop_chat(request: Request):
        from chat.entity_chat import cancel_entity_runs

        ctx = load_for_stop(request)
        cancelled = cancel_entity_runs(kind, ctx.entity_id)
        return {"stopped": cancelled > 0, "cancelled": cancelled}

    return sub


__all__ = ["EntityChatRoute", "build_entity_chat_router"]
