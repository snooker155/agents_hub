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

from typing import Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/entity-chats", tags=["entity-chats"])


class ActivateIn(BaseModel):
    kind: str
    entity_id: str
    session_id: str


def _store():
    from common.entity_chat_store import entity_chat_store

    return entity_chat_store()


def _require_own(kind: str, entity_id: str) -> None:
    """A chat keyed on a person (the Help panel's ``("help", "user-<id>")``,
    the assistant's ``("assistant", "user-<id>")`` and an administrator's
    ``("assistant", "service-<id>")``) is that person's alone: anyone else gets
    the same 404 as a thread that does not exist, so a key cannot be probed.
    Every other kind is keyed on a shared record and stays as open as the
    record itself."""
    from routes.assistant import ASSISTANT_CHAT_KIND, own_thread_ids
    from routes.help_chat import HELP_CHAT_KIND, help_chat_id

    if kind == HELP_CHAT_KIND and entity_id != help_chat_id():
        raise HTTPException(status_code=404, detail="Chat session not found")
    if kind == ASSISTANT_CHAT_KIND and entity_id not in own_thread_ids():
        raise HTTPException(status_code=404, detail="Chat session not found")


def _record_workspace(kind: str, entity_id: str) -> Tuple[bool, Optional[str]]:
    """``(found, workspace)`` of the record a chat of ``kind`` is keyed on,
    for the kinds keyed on one record that names a workspace; ``(False,
    None)`` for every other kind (a workspace, a page, a person) or a record
    that is gone."""
    if kind == "view":
        from views.store import get_view
        view = get_view(entity_id)
        return (view is not None, (view or {}).get("workspace"))
    if kind in ("scenario", "world"):
        from playground import store
        rec = store.get_scenario(entity_id) if kind == "scenario" else store.get_world(entity_id)
        return (rec is not None, getattr(rec, "workspace", None))
    if kind == "memory" and entity_id.startswith("pool:"):
        from memory.store import MemoryStore
        mem = MemoryStore().get(entity_id[len("pool:"):])
        return (mem is not None, getattr(mem, "workspace", None))
    if kind in ("team", "loop"):
        from common import record_access
        found = record_access.resolve(kind, entity_id)
        if found is None:
            return (False, None)
        return (True, found.workspace)
    return (False, None)


def _require_record_visible(request: Request, kind: str, entity_id: str) -> None:
    """A chat keyed on a record is as visible as the record: a view, scenario,
    world, memory pool, team or loop of a workspace the caller cannot see
    answers 403 (one that names no workspace is the default one's,
    common/access.py)."""
    from common import access, identity
    principal = identity.request_principal(request)
    if access.visible_workspaces(principal) is None:
        return
    try:
        found, workspace = _record_workspace(kind, entity_id)
    except Exception:  # noqa: BLE001 - an optional store (the playground) that is off has no records
        return
    if found:
        access.require_visible(principal, workspace)


@router.get("/sessions")
async def list_entity_chat_sessions(
    request: Request, kind: str = Query(...), entity_id: str = Query(...),
):
    """Every thread this chat has: the live one first, then the archive.

    Carries ``has_history`` alongside them, because the two are not the same
    question: a chat opened on one of its old threads can be down to a single
    thread and still be one whose history the user must be able to reach.

    Empty for a chat nobody has written in yet, which is a state the list
    renders rather than an error.
    """
    _require_own(kind, entity_id)
    _require_record_visible(request, kind, entity_id)
    return _store().history(kind, entity_id)


@router.post("/sessions/activate")
async def activate_entity_chat_session(payload: ActivateIn):
    """Reopen one thread, filing the current one under the archive."""
    from chat.entity_chat import entity_run_active

    _require_own(payload.kind, payload.entity_id)
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
    _require_own(kind, entity_id)
    if not _store().delete_session(kind, entity_id, session_id):
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"deleted": True, **_store().history(kind, entity_id)}


__all__ = ["router"]
