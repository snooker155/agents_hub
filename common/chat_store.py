"""
Persistence for the main Chat page's conversations.

A chat is one of the few things on this service a person actually accumulates:
what they asked, what the agent answered, which runs it spawned, which files it
touched. It used to live in ``localStorage`` under ``agent_hub_chats_v1``, which
made it a property of one browser profile rather than of the service — gone with
the site data, absent on the next device, and silently trimmed oldest-first once
the 5 MB quota was hit. This module puts it where the rest of the state already
lives: the shared SQLite database (:mod:`common.db`), one row per conversation.

Shape
-----
The row stores the conversation **as the UI renders it**: metadata (title,
target, workspace, project) plus the ordered bubbles, each with the extras a
bubble carries (run id, token tallies, tool calls, touched files, a rich view).
That is deliberate. A transcript is reconstructible from the runs table (the
Telegram thread is rebuilt exactly that way), but only as bare user/assistant
text — the reconstruction loses which bubble a view belonged to and what the
user saw. The runs table remains the ledger of what executed; this table is the
record of the conversation that executed it.

Writes replace the whole document, the way the browser used to rewrite the whole
localStorage key. A conversation is small next to a run payload, and a
full-document write keeps the store honest about being a mirror of the client's
state rather than a second source of truth that can drift from it.

Two bounds keep one runaway chat from growing without limit: ``MAX_MESSAGES``
per chat (oldest bubbles drop first, the same order localStorage used) and
``MAX_DOC_BYTES``, after which the heavy per-bubble extras are shed before the
text is. Both are far above any hand-written conversation.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from common import db, remote_state

log = logging.getLogger(__name__)


def _current_user_id() -> str:
    """Owner for a newly stored conversation. Imported lazily: the identity
    module reads the settings, and this store is imported by processes that
    only ever read chats."""
    from common.identity import current_user_id
    return current_user_id()


#: How many bubbles one chat keeps. Beyond this the oldest are dropped, which is
#: what the browser store did to whole conversations once the quota was hit.
MAX_MESSAGES = 2000

#: Ceiling on one stored conversation. Bubbles carry streamed extras (tool
#: calls, file lists, view payloads) that dwarf their text, so an oversized doc
#: sheds those first and only then drops bubbles.
MAX_DOC_BYTES = 4 * 1024 * 1024

#: Per-bubble fields worth dropping before any message is lost.
_HEAVY_MSG_FIELDS = ("tool_calls", "files", "view", "timeline", "thinking_live",
                     "running_tool", "board")

#: Fields the client owns and the row mirrors into columns.
_COLUMNS = ("title", "workspace", "project_id", "agent_id", "flow_id",
            "team_id", "target_mode", "origin", "owner")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _messages(chat: Dict[str, Any]) -> List[dict]:
    msgs = chat.get("messages")
    return [m for m in msgs if isinstance(m, dict)] if isinstance(msgs, list) else []


def _fit(chat: Dict[str, Any]) -> Dict[str, Any]:
    """Bring one conversation inside the stored-size bounds."""
    msgs = _messages(chat)
    if len(msgs) > MAX_MESSAGES:
        msgs = msgs[-MAX_MESSAGES:]
    chat = {**chat, "messages": msgs}
    if len(db.dumps(chat)) <= MAX_DOC_BYTES:
        return chat

    # Too big: shed the streamed extras first — they are display detail that the
    # run record can still answer for, while the text of the turn cannot be
    # recovered from anywhere else.
    stripped = []
    for m in msgs:
        copy = {k: v for k, v in m.items() if k not in _HEAVY_MSG_FIELDS}
        stripped.append(copy)
    chat = {**chat, "messages": stripped}
    while len(stripped) > 1 and len(db.dumps(chat)) > MAX_DOC_BYTES:
        # Drop from the front: a conversation reads from its tail.
        stripped = stripped[len(stripped) // 4 + 1:]
        chat = {**chat, "messages": stripped}
    return chat


def _summary(chat: Dict[str, Any]) -> Dict[str, Any]:
    """A chat without its transcript, for the sidebar list.

    The list renders a title and a timestamp per row, so shipping every bubble
    of every conversation to draw it would be the localStorage problem in a new
    place: a page load proportional to the whole history.
    """
    msgs = _messages(chat)
    last = msgs[-1] if msgs else None
    return {
        **{k: v for k, v in chat.items() if k != "messages"},
        "messages": [],
        "message_count": len(msgs),
        "preview": (str(last.get("content") or "")[:200] if last else ""),
    }


def _row_to_chat(row) -> Optional[Dict[str, Any]]:
    doc = db.loads(row["doc"])
    return doc if isinstance(doc, dict) else None


# ── reads ────────────────────────────────────────────────────────────────────

@remote_state.hub_call("workspace")
def list_chats(workspace: Optional[str] = None, limit: int = 200,
               offset: int = 0) -> Dict[str, Any]:
    """A page of conversations, newest activity first, without transcripts."""
    where, params = "", []
    if workspace:
        where = "WHERE workspace = ?"
        params.append(workspace)
    conn = db.get_conn()
    total = conn.execute(f"SELECT COUNT(*) FROM chats {where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT doc FROM chats {where} "
        "ORDER BY COALESCE(updated_at, created_at) DESC, chat_id DESC LIMIT ? OFFSET ?",
        [*params, max(1, min(int(limit), 500)), max(0, int(offset))],
    ).fetchall()
    items = [_summary(c) for c in (_row_to_chat(r) for r in rows) if c is not None]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


#: How much text a search hit shows around the words it found.
_SNIPPET_CHARS = 140


def _snippet(text: str, at: int, length: int) -> str:
    start = max(0, at - _SNIPPET_CHARS // 3)
    end = min(len(text), at + length + _SNIPPET_CHARS)
    piece = " ".join(text[start:end].split())
    return ("…" if start else "") + piece + ("…" if end < len(text) else "")


@remote_state.hub_call("workspace")
def search_chats(q: str, workspace: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
    """The conversations whose title or messages hold ``q`` (any case), newest
    activity first, without transcripts; a hit in a message carries the text
    around it as ``match``.

    Read in Python, not by SQL: the transcript is a JSON document, and
    matching its text case-blind in any script (Cyrillic too) the same way on
    SQLite and Postgres is simplest where the document is decoded."""
    needle = " ".join(str(q or "").split()).casefold()
    if not needle:
        return {"items": [], "total": 0, "q": q}
    where, params = "", []
    if workspace:
        where = "WHERE workspace = ?"
        params.append(workspace)
    rows = db.get_conn().execute(
        f"SELECT doc FROM chats {where} ORDER BY COALESCE(updated_at, created_at) DESC, chat_id DESC",
        params,
    ).fetchall()
    cap = max(1, min(int(limit), 200))
    items: List[Dict[str, Any]] = []
    for row in rows:
        chat = _row_to_chat(row)
        if chat is None:
            continue
        title = str(chat.get("title") or "")
        if needle in " ".join(title.split()).casefold():
            items.append(_summary(chat))
        else:
            for m in reversed(_messages(chat)):
                if m.get("role") not in ("user", "agent"):
                    continue
                text = str(m.get("content") or "")
                at = text.casefold().find(needle)
                if at < 0:
                    continue
                items.append({**_summary(chat), "match": _snippet(text, at, len(needle))})
                break
        if len(items) >= cap:
            break
    return {"items": items, "total": len(items), "q": q}


@remote_state.hub_call("chat:chat_id")
def get_chat(chat_id: str) -> Optional[Dict[str, Any]]:
    """One conversation with its full transcript."""
    row = db.get_conn().execute(
        "SELECT doc FROM chats WHERE chat_id = ?", (str(chat_id),)).fetchone()
    return _row_to_chat(row) if row else None


def chat_ids() -> List[str]:
    rows = db.get_conn().execute("SELECT chat_id FROM chats").fetchall()
    return [r["chat_id"] for r in rows]


# ── writes ───────────────────────────────────────────────────────────────────

@remote_state.hub_call(("chat:chat.id", "workspace:chat.workspace"))
def save_chat(chat: Dict[str, Any]) -> Dict[str, Any]:
    """Create or replace one conversation. Returns what was stored.

    ``created_at`` survives a rewrite: the client sends the conversation it
    holds, and a reload that lost the original timestamp must not reorder the
    history.
    """
    chat_id = str(chat.get("id") or "").strip()
    if not chat_id:
        raise ValueError("chat id is required")

    with db.transaction() as conn:
        row = conn.execute("SELECT doc FROM chats WHERE chat_id = ?",
                           (chat_id,)).fetchone()
        existing = _row_to_chat(row) if row else None
        doc = _fit({
            **chat,
            "id": chat_id,
            # Who the conversation belongs to. Taken from the request in
            # flight rather than from the client, which must not be able to
            # claim someone else's chat, and never rewritten once set: a
            # client that re-saves a conversation is not changing its owner.
            # ``local`` outside AUTH_MODE=multi. See common/identity.py.
            "owner": ((existing or {}).get("owner") or _current_user_id()),
            "created_at": (chat.get("created_at")
                           or (existing or {}).get("created_at") or _now()),
            "updated_at": _now(),
        })
        conn.execute(
            db.upsert_sql(
                "chats",
                ("chat_id",) + _COLUMNS
                + ("message_count", "created_at", "updated_at", "doc"),
                ("chat_id",),
            ),
            (chat_id, *[doc.get(c) for c in _COLUMNS], len(_messages(doc)),
             doc.get("created_at"), doc.get("updated_at"), db.dumps(doc)),
        )
    _mirror(chat_id, doc.get("origin"))
    return doc


def _mirror(chat_id: str, origin: Any) -> None:
    """A saved conversation is due for the hub link's mirror
    (hublink/mirror.py), when one is on. Never blocks the save."""
    try:
        from hublink.mirror import on_chat_saved
        on_chat_saved(str(chat_id), str(origin or ""))
    except Exception:  # noqa: BLE001 - the mirror must never break a chat write
        log.debug("chat store: mirror hook failed", exc_info=True)


def _notice_text(notice: Dict[str, Any]) -> str:
    """A notice in words, for a reader that does not word it itself."""
    if notice.get("type") == "background_run_finished":
        return f"{notice.get('kind') or 'run'} '{notice.get('name') or ''}' finished: {notice.get('status') or ''}"
    return str(notice.get("text") or "")


@remote_state.hub_call(("chat:chat_id", "run:run_id"))
def append_turn(chat_id: str, *, run_id: str, user_message: str,
                agent_message: str, agent_id: Optional[str] = None,
                usage: Optional[Dict[str, Any]] = None,
                duration_ms: Optional[int] = None,
                extra: Optional[Dict[str, Any]] = None,
                notice: Optional[Dict[str, Any]] = None) -> bool:
    """Add one completed exchange to a stored chat. Returns whether it was added.

    This is the safety net under the browser, not the normal path: the page that
    ran the turn writes its own, richer version of these bubbles (tool traces,
    touched files, rendered views). It matters when that page is gone — a tab
    closed mid-answer used to take the answer with it, although the run had
    completed and been recorded.

    Two guards keep it from fighting the browser. A chat that does not exist is
    left alone, because a conversation the dashboard never created (a Telegram
    thread, an instance delivery) should not appear in the sidebar. A run whose
    id is already on a finished message is skipped, so whichever writer got
    there first holds the turn and the other does not duplicate it. A bubble the
    page saved while the turn was still being written (no ``duration_ms`` and
    no ``total_tokens`` yet) is not finished: the page saves as it streams, and when it is gone before
    the end (left for another page, closed) that half answer would otherwise be
    the record. Such a bubble gets the final answer here, keeping the rest of
    what the page wrote.

    ``extra`` is merged into the agent bubble: a reply that took the
    conversation over by handoff carries its ``handoff`` there, which is what
    the chat draws the "handed over" divider from (chat/handoff.py).

    ``notice`` is what started a turn the hub began by itself (a background
    run that ended, chat/background_runs.py): a system line above the reply.
    """
    if not chat_id or not run_id:
        return False
    with db.transaction() as conn:
        row = conn.execute("SELECT doc FROM chats WHERE chat_id = ?",
                           (str(chat_id),)).fetchone()
        chat = _row_to_chat(row) if row else None
        if chat is None:
            return False
        messages = _messages(chat)
        same_run = [i for i, m in enumerate(messages)
                    if str(m.get("run_id") or "") == str(run_id)]
        if same_run:
            i = same_run[-1]
            partial = messages[i]
            unfinished = (partial.get("role") == "agent" and partial.get("duration_ms") is None
                          and partial.get("total_tokens") is None)
            if not unfinished:
                return False
            messages = list(messages)
            messages[i] = {
                **partial, **(extra or {}),
                "content": agent_message, "error": False,
                "inbound_tokens": (usage or {}).get("inbound_tokens", partial.get("inbound_tokens")),
                "outbound_tokens": (usage or {}).get("outbound_tokens", partial.get("outbound_tokens")),
                "total_tokens": (usage or {}).get("total_tokens", partial.get("total_tokens")),
                "duration_ms": duration_ms if duration_ms is not None else 0,
                "thinking_live": "",
            }
            doc = _fit({**chat, "messages": messages, "updated_at": _now()})
            conn.execute(
                "UPDATE chats SET message_count = ?, updated_at = ?, doc = ? WHERE chat_id = ?",
                (len(_messages(doc)), doc["updated_at"], db.dumps(doc), str(chat_id)),
            )
            _mirror(chat_id, chat.get("origin"))
            return True
        turn = []
        if notice:
            turn.append({"id": f"srv-n-{run_id}", "role": "system", "notice": notice,
                         "content": _notice_text(notice)})
        # A conversation opened with its message already in it (a scheduled
        # run, plans/service.py) keeps that one bubble.
        last = messages[-1] if messages else {}
        asked_already = (last.get("role") == "user"
                         and str(last.get("content") or "").strip() == (user_message or "").strip())
        if (user_message or "").strip() and not asked_already:
            turn.append({"id": f"srv-u-{run_id}", "role": "user",
                         "content": user_message})
        turn.append({
            "id": f"srv-a-{run_id}", "role": "agent", "agent_id": agent_id,
            "content": agent_message, "error": False, "run_id": str(run_id),
            "inbound_tokens": (usage or {}).get("inbound_tokens"),
            "outbound_tokens": (usage or {}).get("outbound_tokens"),
            "total_tokens": (usage or {}).get("total_tokens"),
            "duration_ms": duration_ms,
            **(extra or {}),
        })
        doc = _fit({**chat, "messages": messages + turn, "updated_at": _now()})
        conn.execute(
            "UPDATE chats SET message_count = ?, updated_at = ?, doc = ? WHERE chat_id = ?",
            (len(_messages(doc)), doc["updated_at"], db.dumps(doc), str(chat_id)),
        )
    _mirror(chat_id, chat.get("origin"))
    return True


@remote_state.hub_call("chat:chat_id")
def mark_stopped(chat_id: str, *, run_id: str, user_message: str,
                 agent_id: Optional[str] = None) -> bool:
    """Record in a stored chat that the person stopped a turn. Returns whether
    anything changed.

    The page that ran the turn marks its own bubble when its Stop button ends
    it. A turn stopped from anywhere else (another tab, the page opened again
    after it was left, the run's own page) has no page to write that, so the
    mark is made here, the same safety net as :func:`append_turn`:

    * the bubble already holding the run gets ``stopped`` and keeps the text
      it was saved with;
    * a turn the transcript holds without its run (stopped before the server
      named it) is found by its message, the last one the person sent, and
      its reply is marked, or added empty when the page saved none;
    * a turn the transcript does not hold at all is added: the message and an
      empty reply marked stopped.
    """
    if not chat_id or not run_id:
        return False
    with db.transaction() as conn:
        row = conn.execute("SELECT doc FROM chats WHERE chat_id = ?",
                           (str(chat_id),)).fetchone()
        chat = _row_to_chat(row) if row else None
        if chat is None:
            return False
        messages = list(_messages(chat))
        reply = {"id": f"srv-a-{run_id}", "role": "agent", "agent_id": agent_id,
                 "content": "", "error": False, "run_id": str(run_id), "stopped": True}
        same_run = [i for i, m in enumerate(messages)
                    if str(m.get("run_id") or "") == str(run_id)]
        if same_run:
            i = same_run[-1]
            if messages[i].get("stopped"):
                return False
            messages[i] = {**messages[i], "stopped": True, "thinking_live": "", "running_tool": None}
        else:
            users = [i for i, m in enumerate(messages) if m.get("role") == "user"]
            asked = (user_message or "").strip()
            if asked and users and str(messages[users[-1]].get("content") or "").strip() == asked:
                after = [i for i in range(users[-1] + 1, len(messages))
                         if messages[i].get("role") == "agent" and not messages[i].get("run_id")]
                if after:
                    i = after[-1]
                    messages[i] = {**messages[i], "stopped": True, "run_id": str(run_id),
                                   "thinking_live": "", "running_tool": None}
                else:
                    messages.insert(users[-1] + 1, reply)
            else:
                if asked:
                    messages.append({"id": f"srv-u-{run_id}", "role": "user", "content": user_message})
                messages.append(reply)
        doc = _fit({**chat, "messages": messages, "updated_at": _now()})
        conn.execute(
            "UPDATE chats SET message_count = ?, updated_at = ?, doc = ? WHERE chat_id = ?",
            (len(_messages(doc)), doc["updated_at"], db.dumps(doc), str(chat_id)),
        )
    return True


@remote_state.hub_call("chat:chat_id")
def set_agent(chat_id: str, agent_id: str) -> bool:
    """Point a stored chat at another agent. Returns whether it changed.

    A conversation handed over to another agent (chat/handoff.py) continues
    with that agent: the next turn the page sends goes to it. The page that
    ran the turn saves this itself; this is the same safety net as
    :func:`append_turn` for a page that is gone. Only the target moves, in one
    transaction, so a transcript the page wrote meanwhile is kept.
    """
    if not chat_id or not agent_id:
        return False
    with db.transaction() as conn:
        row = conn.execute("SELECT doc FROM chats WHERE chat_id = ?",
                           (str(chat_id),)).fetchone()
        chat = _row_to_chat(row) if row else None
        if chat is None or chat.get("agent_id") == agent_id:
            return False
        doc = {**chat, "agent_id": agent_id, "updated_at": _now()}
        conn.execute(
            "UPDATE chats SET agent_id = ?, updated_at = ?, doc = ? WHERE chat_id = ?",
            (agent_id, doc["updated_at"], db.dumps(doc), str(chat_id)),
        )
    return True


def delete_chat(chat_id: str) -> bool:
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM chats WHERE chat_id = ?", (str(chat_id),))
    return cur.rowcount > 0


def import_chats(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Take in conversations a browser still holds, without clobbering.

    Used once per browser profile to lift the old ``localStorage`` history into
    the database. An id already in the store is left alone: the stored copy is
    the one other devices have been writing to, and the browser's copy may be a
    stale snapshot of the same conversation.
    """
    known = set(chat_ids())
    imported, skipped = 0, 0
    for item in items or []:
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            skipped += 1
            continue
        if str(item["id"]) in known:
            skipped += 1
            continue
        save_chat(item)
        known.add(str(item["id"]))
        imported += 1
    return {"imported": imported, "skipped": skipped}


__all__ = ["list_chats", "get_chat", "save_chat", "delete_chat", "append_turn", "mark_stopped",
           "import_chats",
           "chat_ids", "MAX_MESSAGES", "MAX_DOC_BYTES"]
