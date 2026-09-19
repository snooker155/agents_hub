"""
Session history for the entity build chats.

Every page with a chat of its own — a scenario's builder, the Visualizer, the
Service Agent, the memory reader — stores its conversation in
:mod:`common.entity_chat_store` under one ``(kind, entity_id)`` key. Until now
"Clear" was the only way out of a thread that had gone somewhere unhelpful, and
it took the thread with it. The store keeps those threads instead, and this
router is how the browser reaches them: list what this chat has been, reopen
one, drop one.

One router rather than three endpoints in each of the eleven chat modules,
because nothing here is per-kind: the key is the key. The browser learns its
own key from ``chat_ref`` on the chat's GET response, so a page keeps passing
around a path and never has to know that a kind exists.

Reopening a thread is a **swap**, not a copy: the chosen thread becomes the live
one, epoch included, so the next turn continues it under the conversation id it
already had in Messages. A turn in flight blocks the swap — it would otherwise
be filed under whichever thread was live when it finished.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/entity-chats", tags=["entity-chats"])


class ActivateIn(BaseModel):
    kind: str
    entity_id: str
    session_id: str


def _store():
    from common.entity_chat_store import entity_chat_store

    return entity_chat_store()


@router.get("/sessions")
async def list_entity_chat_sessions(
    kind: str = Query(...), entity_id: str = Query(...),
):
    """Every thread this chat has: the live one first, then the archive.

    Carries ``has_history`` alongside them, because the two are not the same
    question: a chat opened on one of its old threads can be down to a single
    thread and still be one whose history the user must be able to reach.

    Empty for a chat nobody has written in yet, which is a state the list
    renders rather than an error.
    """
    return _store().history(kind, entity_id)


@router.post("/sessions/activate")
async def activate_entity_chat_session(payload: ActivateIn):
    """Reopen one thread, filing the current one under the archive."""
    from chat.entity_chat import entity_run_active

    if entity_run_active(payload.kind, payload.entity_id):
        raise HTTPException(status_code=409,
                            detail="A turn is still running in this chat")
    restored = _store().activate_session(payload.kind, payload.entity_id,
                                         payload.session_id)
    if restored is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"activated": True, **restored,
            **_store().history(payload.kind, payload.entity_id)}


@router.delete("/sessions")
async def delete_entity_chat_session(
    kind: str = Query(...), entity_id: str = Query(...),
    session_id: str = Query(...),
):
    """Drop one archived thread. The live one is cleared, not deleted."""
    if not _store().delete_session(kind, entity_id, session_id):
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"deleted": True, **_store().history(kind, entity_id)}


__all__ = ["router"]
