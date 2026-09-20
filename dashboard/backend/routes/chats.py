"""
Chats API — the conversations on the main Chat page.

The Chat page kept its history in the browser's ``localStorage``. That made the
central thing the service produces the one thing it did not store: clearing site
data wiped it, a second device never saw it, and once the quota was reached the
oldest conversations were dropped without telling anyone. These endpoints put
chats where tasks, runs and sessions already are — the shared database, via
:mod:`common.chat_store`.

The browser remains the author of a conversation's shape (it is what renders the
bubbles), so a write replaces the whole document rather than patching it. The
endpoints are therefore few and blunt: list without transcripts, read one with
its transcript, replace one, delete one, and a one-time import for the history a
browser is still holding from before this existed.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from common import chat_store, live_runs

router = APIRouter(prefix="/api/chats", tags=["chats"])


class ChatIn(BaseModel):
    """One conversation as the browser holds it.

    Deliberately open: the bubbles carry whatever the renderer needs (token
    tallies, tool calls, touched files, a view id), and pinning that shape here
    would mean a schema change every time the chat surface grows a field. The
    id is the contract; the rest is the client's document.
    """

    id: str = Field(..., min_length=1)
    title: Optional[str] = None
    workspace: Optional[str] = None
    project_id: Optional[str] = None
    agent_id: Optional[str] = None
    flow_id: Optional[str] = None
    team_id: Optional[str] = None
    target_mode: Optional[str] = None
    origin: Optional[str] = None
    created_at: Optional[str] = None
    messages: List[Dict[str, Any]] = []

    model_config = {"extra": "allow"}


class ImportIn(BaseModel):
    chats: List[Dict[str, Any]] = []


@router.get("")
async def list_chats(
    workspace: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
):
    """Every conversation, newest first, **without** its messages.

    The sidebar draws a title and a time per row; shipping every transcript to
    draw it would reproduce the problem this store exists to solve, one page
    load at a time. The transcript arrives when a chat is opened.
    """
    return chat_store.list_chats(workspace=workspace, limit=limit, offset=offset)


@router.get("/{chat_id}/live")
async def get_live_turn(chat_id: str):
    """The turn this conversation is in the middle of, if there is one.

    A browser that opens a chat while it is being answered has missed the tokens
    that already went past, and the channel only carries what comes next. This
    is the catch-up: what has been said so far, so the live view can start from
    the middle of the answer instead of from the middle of a word.

    ``{"turn": null}`` for a conversation that is idle, which is the common case
    and not an error.
    """
    return {"turn": live_runs.by_conversation(chat_id)}


@router.get("/{chat_id}")
async def get_chat(chat_id: str):
    """One conversation with its full transcript."""
    chat = chat_store.get_chat(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


@router.put("/{chat_id}")
async def save_chat(chat_id: str, payload: ChatIn,
                    x_client_id: Optional[str] = Header(default=None)):
    """Create or replace one conversation.

    The save is announced on the conversation's own channel, carrying the id of
    the client that made it. Another tab or device with this chat open reloads
    it; the writer recognises its own id and does not. The announcement is
    per-conversation on purpose: a global "chats changed" would make every open
    tab refetch its whole list on every keystroke-driven save.
    """
    if payload.id != chat_id:
        raise HTTPException(status_code=400, detail="Chat id mismatch")
    stored = chat_store.save_chat(payload.model_dump())
    await _announce_save(chat_id, x_client_id)
    return stored


async def _announce_save(chat_id: str, origin_client: Optional[str]) -> None:
    from chat.broadcast import channel_for
    from common.session_broker import broker

    channel = channel_for(chat_id)
    if not channel or not broker.has_subscribers(channel):
        return
    try:
        await broker.apublish(channel, {"type": "chat_saved", "chat_id": chat_id,
                                        "conversation_id": chat_id,
                                        "origin_client": origin_client})
    except Exception:
        pass


@router.delete("/{chat_id}")
async def delete_chat(chat_id: str):
    """Drop one conversation. Its runs stay in the ledger."""
    if not chat_store.delete_chat(chat_id):
        raise HTTPException(status_code=404, detail="Chat not found")
    return {"deleted": True}


@router.post("/import")
async def import_chats(payload: ImportIn):
    """Adopt the conversations a browser still holds in ``localStorage``.

    Runs once per browser profile. Ids already stored are skipped rather than
    overwritten — the stored copy may have moved on from the browser's snapshot.
    """
    return chat_store.import_chats(payload.chats)


__all__ = ["router"]
