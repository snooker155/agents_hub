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

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from common import db

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
            "team_id", "target_mode", "origin")


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
        "ORDER BY COALESCE(updated_at, created_at) DESC, rowid DESC LIMIT ? OFFSET ?",
        [*params, max(1, min(int(limit), 500)), max(0, int(offset))],
    ).fetchall()
    items = [_summary(c) for c in (_row_to_chat(r) for r in rows) if c is not None]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def get_chat(chat_id: str) -> Optional[Dict[str, Any]]:
    """One conversation with its full transcript."""
    row = db.get_conn().execute(
        "SELECT doc FROM chats WHERE chat_id = ?", (str(chat_id),)).fetchone()
    return _row_to_chat(row) if row else None


def chat_ids() -> List[str]:
    rows = db.get_conn().execute("SELECT chat_id FROM chats").fetchall()
    return [r["chat_id"] for r in rows]


# ── writes ───────────────────────────────────────────────────────────────────

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
            "created_at": (chat.get("created_at")
                           or (existing or {}).get("created_at") or _now()),
            "updated_at": _now(),
        })
        conn.execute(
            "INSERT OR REPLACE INTO chats "
            "(chat_id, title, workspace, project_id, agent_id, flow_id, team_id,"
            " target_mode, origin, message_count, created_at, updated_at, doc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, *[doc.get(c) for c in _COLUMNS], len(_messages(doc)),
             doc.get("created_at"), doc.get("updated_at"), db.dumps(doc)),
        )
    return doc


def append_turn(chat_id: str, *, run_id: str, user_message: str,
                agent_message: str, agent_id: Optional[str] = None,
                usage: Optional[Dict[str, Any]] = None,
                duration_ms: Optional[int] = None) -> bool:
    """Add one completed exchange to a stored chat. Returns whether it was added.

    This is the safety net under the browser, not the normal path: the page that
    ran the turn writes its own, richer version of these bubbles (tool traces,
    touched files, rendered views). It matters when that page is gone — a tab
    closed mid-answer used to take the answer with it, although the run had
    completed and been recorded.

    Two guards keep it from fighting the browser. A chat that does not exist is
    left alone, because a conversation the dashboard never created (a Telegram
    thread, an instance delivery) should not appear in the sidebar. A run whose
    id is already on a message is skipped, so whichever writer got there first
    holds the turn and the other does not duplicate it.
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
        if any(str(m.get("run_id") or "") == str(run_id) for m in messages):
            return False
        turn = []
        if (user_message or "").strip():
            turn.append({"id": f"srv-u-{run_id}", "role": "user",
                         "content": user_message})
        turn.append({
            "id": f"srv-a-{run_id}", "role": "agent", "agent_id": agent_id,
            "content": agent_message, "error": False, "run_id": str(run_id),
            "inbound_tokens": (usage or {}).get("inbound_tokens"),
            "outbound_tokens": (usage or {}).get("outbound_tokens"),
            "total_tokens": (usage or {}).get("total_tokens"),
            "duration_ms": duration_ms,
        })
        doc = _fit({**chat, "messages": messages + turn, "updated_at": _now()})
        conn.execute(
            "UPDATE chats SET message_count = ?, updated_at = ?, doc = ? WHERE chat_id = ?",
            (len(_messages(doc)), doc["updated_at"], db.dumps(doc), str(chat_id)),
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


__all__ = ["list_chats", "get_chat", "save_chat", "delete_chat", "append_turn",
           "import_chats",
           "chat_ids", "MAX_MESSAGES", "MAX_DOC_BYTES"]
