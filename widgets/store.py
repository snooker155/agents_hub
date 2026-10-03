"""
The widget tables (migration 0024): widgets, their visitors' threads, and the
messages of those threads.

Plain functions over ``common.db`` returning dicts, the shape the routes send
and the service reasons about. Nothing here checks who may do what: the
service decides, this module only reads and writes. JSON columns (origins,
limits, attachment names, citations, a handoff) are decoded on the way out, so
no caller ever sees a JSON string.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from common import db

#: Every widget id starts with this, so one is recognisable in a snippet or a
#: log line, and a path segment like ``public`` can never be taken for one.
WIDGET_PREFIX = "wgt_"
THREAD_PREFIX = "wth_"
MESSAGE_PREFIX = "wms_"

_WIDGET_COLUMNS = ("widget_id", "workspace", "name", "agent_id", "owner_id", "public_key",
                   "allowed_origins", "enabled", "title", "greeting", "placeholder", "accent",
                   "language", "limits", "created_at", "updated_at", "agent_version")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_widget_id() -> str:
    return WIDGET_PREFIX + secrets.token_hex(8)


def new_thread_id() -> str:
    return THREAD_PREFIX + secrets.token_hex(10)


def new_message_id() -> str:
    return MESSAGE_PREFIX + secrets.token_hex(10)


# ── widgets ──────────────────────────────────────────────────────────────────

def _widget(row) -> Dict[str, Any]:
    return {
        "widget_id": row["widget_id"],
        "workspace": row["workspace"],
        "name": row["name"],
        "agent_id": row["agent_id"],
        "owner_id": row["owner_id"],
        "public_key": row["public_key"],
        "allowed_origins": list(db.loads(row["allowed_origins"], []) or []),
        "enabled": bool(row["enabled"]),
        "title": row["title"] or "",
        "greeting": row["greeting"] or "",
        "placeholder": row["placeholder"] or "",
        "accent": row["accent"] or "navy",
        "language": row["language"] or "auto",
        "limits": dict(db.loads(row["limits"], {}) or {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        # The agent version the widget pins (migration 0032), None = live.
        "agent_version": _agent_version(row),
    }


def _agent_version(row) -> Optional[int]:
    try:
        value = row["agent_version"]
    except (KeyError, IndexError):  # a row read before migration 0032 ran
        return None
    return int(value) if value is not None else None


def _widget_params(record: Dict[str, Any]) -> tuple:
    return tuple(
        db.dumps(record[c]) if c in ("allowed_origins", "limits")
        else (1 if record[c] else 0) if c == "enabled"
        else record[c]
        for c in _WIDGET_COLUMNS)


def insert_widget(record: Dict[str, Any]) -> Dict[str, Any]:
    placeholders = ", ".join("?" for _ in _WIDGET_COLUMNS)
    with db.transaction() as conn:
        conn.execute(
            f"INSERT INTO widgets ({', '.join(_WIDGET_COLUMNS)}) VALUES ({placeholders})",
            _widget_params(record))
    return get_widget(record["widget_id"]) or record


def update_widget(record: Dict[str, Any]) -> Dict[str, Any]:
    """Write every column of an already loaded and modified record."""
    columns = [c for c in _WIDGET_COLUMNS if c != "widget_id"]
    assignments = ", ".join(f"{c} = ?" for c in columns)
    params = _widget_params(record)[1:] + (record["widget_id"],)
    with db.transaction() as conn:
        conn.execute(f"UPDATE widgets SET {assignments} WHERE widget_id = ?", params)
    return get_widget(record["widget_id"]) or record


def get_widget(widget_id: str) -> Optional[Dict[str, Any]]:
    if not widget_id or not str(widget_id).startswith(WIDGET_PREFIX):
        return None
    row = db.get_conn().execute(
        "SELECT * FROM widgets WHERE widget_id = ?", (str(widget_id),)).fetchone()
    return _widget(row) if row else None


def list_widgets(workspace: Optional[str] = None) -> List[Dict[str, Any]]:
    if workspace:
        rows = db.get_conn().execute(
            "SELECT * FROM widgets WHERE workspace = ? ORDER BY created_at DESC",
            (str(workspace),)).fetchall()
    else:
        rows = db.get_conn().execute(
            "SELECT * FROM widgets ORDER BY created_at DESC").fetchall()
    return [_widget(r) for r in rows]


def delete_widget(widget_id: str) -> bool:
    """The widget, its threads and their messages."""
    with db.transaction() as conn:
        conn.execute("DELETE FROM widget_messages WHERE widget_id = ?", (str(widget_id),))
        conn.execute("DELETE FROM widget_threads WHERE widget_id = ?", (str(widget_id),))
        cursor = conn.execute("DELETE FROM widgets WHERE widget_id = ?", (str(widget_id),))
    return bool(getattr(cursor, "rowcount", 0))


# ── threads ──────────────────────────────────────────────────────────────────

def _thread(row) -> Dict[str, Any]:
    return {
        "thread_id": row["thread_id"],
        "widget_id": row["widget_id"],
        "visitor_id": row["visitor_id"],
        "title": row["title"] or "",
        "agent_id": row["agent_id"],
        "preview": bool(row["preview"]),
        "visitor_deleted_at": row["visitor_deleted_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def insert_thread(*, widget_id: str, visitor_id: str, agent_id: str, title: str = "",
                  preview: bool = False) -> Dict[str, Any]:
    now = now_iso()
    thread_id = new_thread_id()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO widget_threads (thread_id, widget_id, visitor_id, title, agent_id, "
            "preview, visitor_deleted_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (thread_id, str(widget_id), str(visitor_id), title or "", str(agent_id),
             1 if preview else 0, now, now))
    return get_thread(thread_id) or {}


def get_thread(thread_id: str) -> Optional[Dict[str, Any]]:
    if not thread_id or not str(thread_id).startswith(THREAD_PREFIX):
        return None
    row = db.get_conn().execute(
        "SELECT * FROM widget_threads WHERE thread_id = ?", (str(thread_id),)).fetchone()
    return _thread(row) if row else None


def list_threads(widget_id: str, *, visitor_id: Optional[str] = None,
                 include_visitor_deleted: bool = True, limit: int = 200) -> List[Dict[str, Any]]:
    """Newest activity first, with each thread's message count."""
    where = ["t.widget_id = ?"]
    args: List[Any] = [str(widget_id)]
    if visitor_id is not None:
        where.append("t.visitor_id = ?")
        args.append(str(visitor_id))
    if not include_visitor_deleted:
        where.append("t.visitor_deleted_at IS NULL")
    args.append(max(1, min(int(limit or 200), 1000)))
    rows = db.get_conn().execute(
        "SELECT t.*, (SELECT COUNT(*) FROM widget_messages m WHERE m.thread_id = t.thread_id) "
        "AS message_count FROM widget_threads t WHERE " + " AND ".join(where)
        + " ORDER BY t.updated_at DESC LIMIT ?", tuple(args)).fetchall()
    return [{**_thread(r), "message_count": int(r["message_count"] or 0)} for r in rows]


def count_threads(widget_id: str) -> int:
    row = db.get_conn().execute(
        "SELECT COUNT(*) AS n FROM widget_threads WHERE widget_id = ?", (str(widget_id),)).fetchone()
    return int(row["n"] if row else 0)


def count_visitor_threads(widget_id: str, visitor_id: str) -> int:
    row = db.get_conn().execute(
        "SELECT COUNT(*) AS n FROM widget_threads WHERE widget_id = ? AND visitor_id = ? "
        "AND visitor_deleted_at IS NULL", (str(widget_id), str(visitor_id))).fetchone()
    return int(row["n"] if row else 0)


def touch_thread(thread_id: str, *, title: Optional[str] = None,
                 agent_id: Optional[str] = None) -> None:
    sets = ["updated_at = ?"]
    args: List[Any] = [now_iso()]
    if title is not None:
        sets.append("title = ?")
        args.append(title)
    if agent_id is not None:
        sets.append("agent_id = ?")
        args.append(agent_id)
    args.append(str(thread_id))
    with db.transaction() as conn:
        conn.execute(f"UPDATE widget_threads SET {', '.join(sets)} WHERE thread_id = ?",
                     tuple(args))


def mark_thread_deleted_by_visitor(thread_id: str) -> bool:
    with db.transaction() as conn:
        cursor = conn.execute(
            "UPDATE widget_threads SET visitor_deleted_at = ? "
            "WHERE thread_id = ? AND visitor_deleted_at IS NULL", (now_iso(), str(thread_id)))
    return bool(getattr(cursor, "rowcount", 0))


def delete_thread(thread_id: str) -> bool:
    with db.transaction() as conn:
        conn.execute("DELETE FROM widget_messages WHERE thread_id = ?", (str(thread_id),))
        cursor = conn.execute("DELETE FROM widget_threads WHERE thread_id = ?", (str(thread_id),))
    return bool(getattr(cursor, "rowcount", 0))


# ── messages ─────────────────────────────────────────────────────────────────

def _message(row) -> Dict[str, Any]:
    return {
        "message_id": row["message_id"],
        "thread_id": row["thread_id"],
        "widget_id": row["widget_id"],
        "role": row["role"],
        "text": row["text"] or "",
        "attachments": list(db.loads(row["attachments"], []) or []),
        "run_id": row["run_id"],
        "agent_id": row["agent_id"],
        "citations": list(db.loads(row["citations"], []) or []),
        "handoff": db.loads(row["handoff"], None),
        "status": row["status"] or "ok",
        "tokens": int(row["tokens"] or 0),
        "created_at": row["created_at"],
    }


def insert_message(*, thread_id: str, widget_id: str, role: str, text: str,
                   attachments: Optional[List[str]] = None, run_id: Optional[str] = None,
                   agent_id: Optional[str] = None, citations: Optional[List[dict]] = None,
                   handoff: Optional[dict] = None, status: str = "ok",
                   tokens: int = 0) -> Dict[str, Any]:
    message_id = new_message_id()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO widget_messages (message_id, thread_id, widget_id, role, text, "
            "attachments, run_id, agent_id, citations, handoff, status, tokens, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (message_id, str(thread_id), str(widget_id), role, text or "",
             db.dumps(list(attachments or [])), run_id, agent_id,
             db.dumps(list(citations or [])), db.dumps(handoff) if handoff else None,
             status, max(0, int(tokens or 0)), now_iso()))
    row = db.get_conn().execute(
        "SELECT * FROM widget_messages WHERE message_id = ?", (message_id,)).fetchone()
    return _message(row)


def list_messages(thread_id: str, *, limit: int = 500) -> List[Dict[str, Any]]:
    """Oldest first: the order a transcript is read in."""
    rows = db.get_conn().execute(
        "SELECT * FROM (SELECT * FROM widget_messages WHERE thread_id = ? "
        "ORDER BY id DESC LIMIT ?) recent ORDER BY id ASC",
        (str(thread_id), max(1, min(int(limit or 500), 2000)))).fetchall()
    return [_message(r) for r in rows]


def tokens_since(widget_id: str, since_iso: str) -> int:
    row = db.get_conn().execute(
        "SELECT COALESCE(SUM(tokens), 0) AS used FROM widget_messages "
        "WHERE widget_id = ? AND created_at >= ?", (str(widget_id), since_iso)).fetchone()
    return int((row["used"] if row else 0) or 0)


__all__ = [
    "MESSAGE_PREFIX", "THREAD_PREFIX", "WIDGET_PREFIX", "count_threads",
    "count_visitor_threads", "delete_thread", "delete_widget", "get_thread", "get_widget",
    "insert_message", "insert_thread", "insert_widget", "list_messages", "list_threads",
    "list_widgets", "mark_thread_deleted_by_visitor", "new_message_id", "new_thread_id",
    "new_widget_id", "now_iso", "tokens_since", "touch_thread", "update_widget",
]
