"""
Session context service — SQLite-backed storage helpers.

Manages session contexts and pending continuations without any FastAPI
dependency so that both API routes and tool/agent subprocess code can use
these functions.

A session context groups one or more individual agent run messages:
- Chat: one session per conversation_id, one message per exchange
- Task: one session per task_id, one message per agent run on that task

Storage lives in the shared database (``common.db``): the ``sessions`` table
holds the full context document as JSON plus lookup columns, ``continuations``
holds pending re-invocations. The old session_contexts.json /
pending_continuations.json files are migrated in on first open.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from common import db


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_ctx(row) -> Optional[Dict[str, Any]]:
    ctx = db.loads(row["doc"])
    return ctx if isinstance(ctx, dict) else None


def _write_ctx(conn, ctx: Dict[str, Any]) -> None:
    """Persist a context: the full document plus the columns the list queries
    filter and order by (kept in sync with the doc on every write)."""
    conn.execute(
        db.upsert_sql("sessions", ("session_id", "conversation_id", "task_id", "workspace",
                                   "created_at", "is_flow", "doc"), ("session_id",)),
        (str(ctx.get("session_id")), ctx.get("conversation_id"),
         str(ctx["task_id"]) if ctx.get("task_id") else None,
         ctx.get("workspace"), ctx.get("created_at"),
         1 if ctx.get("is_flow") else 0, db.dumps(ctx)),
    )


def _notify() -> None:
    try:
        from common.session_broker import notify_change
        notify_change("sessions")
    except Exception:
        pass


def load_contexts(timeout: float = 10.0) -> List[Dict[str, Any]]:
    rows = db.get_conn().execute(
        "SELECT doc FROM sessions ORDER BY COALESCE(created_at, ''), session_id").fetchall()
    return [c for c in (_row_to_ctx(r) for r in rows) if c is not None]


def query_contexts(
    *,
    workspace: Optional[str] = None,
    conversation_id: Optional[str] = None,
    is_flow: Optional[bool] = None,
    task_id: Optional[str] = None,
    session_ids: Optional[List[str]] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """A filtered, ordered page of session contexts plus the total match count.

    Returns ``{items, total, limit, offset}``, newest first. Everything here is
    done in SQL on the lookup columns: the sessions list used to parse every
    session document in the database on each request, which a workspace with
    thousands of sessions turns into a full scan per open tab.

    ``session_ids`` narrows to a pre-computed set — used by the status filter,
    which is derived from a session's runs and so cannot live in this table.
    """
    clauses: List[str] = []
    params: List[Any] = []
    if workspace:
        if workspace == "default":
            clauses.append("(workspace IS NULL OR workspace = '' OR workspace = 'default')")
        else:
            clauses.append("workspace = ?")
            params.append(workspace)
    if conversation_id:
        clauses.append("conversation_id = ?")
        params.append(str(conversation_id))
    if task_id:
        clauses.append("task_id = ?")
        params.append(str(task_id))
    if is_flow is not None:
        clauses.append("COALESCE(is_flow, 0) = ?")
        params.append(1 if is_flow else 0)
    if from_date:
        clauses.append("created_at >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("created_at <= ?")
        params.append(to_date)
    if session_ids is not None:
        if not session_ids:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        clauses.append(f"session_id IN ({', '.join('?' * len(session_ids))})")
        params.extend([str(s) for s in session_ids])

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = db.get_conn()
    total = conn.execute(f"SELECT COUNT(*) FROM sessions{where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT doc FROM sessions{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        list(params) + [int(limit), int(offset)],
    ).fetchall()
    items = [c for c in (_row_to_ctx(r) for r in rows) if c is not None]
    return {"items": items, "total": int(total), "limit": int(limit), "offset": int(offset)}


def session_ids_without_runs() -> List[str]:
    """Sessions that have no run at all — they read as "pending".

    A tally over the runs table cannot see them (they contribute no rows), so
    the pending filter unions them in. Answered by an anti-join rather than by
    parsing every session document.
    """
    rows = db.get_conn().execute(
        "SELECT session_id FROM sessions WHERE session_id NOT IN "
        "(SELECT session_id FROM runs WHERE session_id IS NOT NULL AND session_id != '')"
    ).fetchall()
    return [str(r["session_id"]) for r in rows]


def save_contexts(contexts: List[Dict[str, Any]], timeout: float = 10.0) -> None:
    """Replace the entire session list (bulk mutations only)."""
    with db.transaction() as conn:
        conn.execute("DELETE FROM sessions")
        for ctx in contexts:
            if isinstance(ctx, dict) and ctx.get("session_id"):
                _write_ctx(conn, ctx)
    _notify()


def upsert_context(ctx: Dict[str, Any]) -> None:
    """Insert or merge-update one session context (atomic read-modify-write)."""
    session_id = str(ctx.get("session_id") or "")
    if not session_id:
        return
    with db.transaction() as conn:
        row = conn.execute("SELECT doc FROM sessions WHERE session_id = ?",
                           (session_id,)).fetchone()
        existing = _row_to_ctx(row) if row is not None else None
        merged = {**(existing or {}), **ctx}
        _write_ctx(conn, merged)
    _notify()


def delete_context(session_id: str) -> bool:
    """Remove a session context. Returns True if one was deleted."""
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE session_id = ?", (str(session_id),))
        removed = cur.rowcount > 0
    if removed:
        _notify()
    return removed


def get_context_by_id(session_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT doc FROM sessions WHERE session_id = ?", (str(session_id),)).fetchone()
    return _row_to_ctx(row) if row is not None else None


def get_or_create_task_session(
    title: str,
    workspace: Optional[str] = None,
    is_flow: bool = False,
    task_id: Optional[str] = None,
) -> str:
    """Return the session_id for a task, creating a context if needed.

    When ``task_id`` is provided, an existing session bound to that task is
    reused instead of creating a new one — this keeps the "one session per task"
    invariant even for callers that don't pre-check ``task.session_id``. Without
    a ``task_id`` there is no stable key to look up by, so a new session is
    always created.
    """
    session_id = str(uuid4())
    ctx: Dict[str, Any] = {
        "session_id": session_id,
        "task_id": str(task_id) if task_id is not None else None,
        "title": title,
        "description": "",
        "workspace": workspace,
        "session_type": "task",
        "is_flow": is_flow,
        "message_ids": [],
        "events": [],
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
        "finished_at": None,
    }
    with db.transaction() as conn:
        if task_id is not None:
            row = conn.execute("SELECT doc FROM sessions WHERE task_id = ? "
                               "ORDER BY COALESCE(created_at, ''), session_id LIMIT 1",
                               (str(task_id),)).fetchone()
            existing = _row_to_ctx(row) if row is not None else None
            if existing and existing.get("session_id"):
                return str(existing["session_id"])
        _write_ctx(conn, ctx)
    _notify()
    return session_id


def get_or_create_chat_session(
    conversation_id: str,
    title: str,
    workspace: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> str:
    """Return the session_id for a chat conversation, creating a context if needed."""
    session_id = str(uuid4())
    ctx: Dict[str, Any] = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "title": title,
        "description": "",
        "workspace": workspace,
        "is_flow": False,
        "session_type": "chat",
        "agent_id": agent_id,
        "message_ids": [],
        "events": [],
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
        "finished_at": None,
    }
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT doc FROM sessions WHERE conversation_id = ? OR session_id = ? "
            "ORDER BY COALESCE(created_at, ''), session_id LIMIT 1",
            (str(conversation_id), str(conversation_id))).fetchone()
        existing = _row_to_ctx(row) if row is not None else None
        if existing:
            if not existing.get("conversation_id"):
                merged = {**existing, "conversation_id": conversation_id,
                          "updated_at": _utc_now_iso()}
                _write_ctx(conn, merged)
            return str(existing["session_id"])
        _write_ctx(conn, ctx)
    _notify()
    return session_id


# ── Conversation summary (history compaction) ─────────────────────────────────
# A long chat is folded by ``chat.compaction``: its older turns become one
# summary, and only the recent tail is sent verbatim. The summary belongs to the
# conversation rather than to any one run, so it lives on the session context —
# the next turn reads it back and extends it instead of paying to summarise the
# same material again.

def get_session_summary(session_id: Optional[str]) -> Dict[str, Any]:
    """The stored summary: ``{text, covers_until, anchor, updated_at}``.

    ``covers_until`` is how many of the conversation's history messages the
    summary speaks for, counted from the start, and ``anchor`` fingerprints the
    last of them so the count can be re-found once the surface's history window
    has slid. Returns ``{}`` when the session has none (or does not exist), which
    reads as "nothing folded yet".
    """
    if not session_id:
        return {}
    ctx = get_context_by_id(str(session_id)) or {}
    summary = ctx.get("summary")
    return dict(summary) if isinstance(summary, dict) else {}


def set_session_summary(session_id: str, text: str, covers_until: int,
                        anchor: str = "") -> None:
    """Store (or replace) a session's conversation summary."""
    if not session_id:
        return
    with db.transaction() as conn:
        row = conn.execute("SELECT doc FROM sessions WHERE session_id = ?",
                           (str(session_id),)).fetchone()
        ctx = _row_to_ctx(row) if row is not None else None
        if ctx is None:
            return
        _write_ctx(conn, {**ctx, "summary": {
            "text": str(text or ""),
            "covers_until": max(0, int(covers_until or 0)),
            "anchor": str(anchor or ""),
            "updated_at": _utc_now_iso(),
        }, "updated_at": _utc_now_iso()})
    _notify()


def add_run_to_session(session_id: str, run_id: str) -> None:
    """Append a run_id to a session context's message_ids (idempotent)."""
    with db.transaction() as conn:
        row = conn.execute("SELECT doc FROM sessions WHERE session_id = ?",
                           (str(session_id),)).fetchone()
        ctx = _row_to_ctx(row) if row is not None else None
        if ctx is None:
            return
        message_ids = list(ctx.get("message_ids") or [])
        if run_id not in message_ids:
            message_ids.append(run_id)
        _write_ctx(conn, {**ctx, "message_ids": message_ids, "updated_at": _utc_now_iso()})
    _notify()


def add_event_to_session(session_id: str, event: Dict[str, Any]) -> None:
    """Append a non-message event (e.g. approval step) to a session context."""
    with db.transaction() as conn:
        row = conn.execute("SELECT doc FROM sessions WHERE session_id = ?",
                           (str(session_id),)).fetchone()
        ctx = _row_to_ctx(row) if row is not None else None
        if ctx is None:
            return
        events = list(ctx.get("events") or [])
        events.append(event)
        _write_ctx(conn, {**ctx, "events": events, "updated_at": _utc_now_iso()})
    _notify()


# ── Session continuations ─────────────────────────────────────────────────────

def register_continuation(
    session_id: str,
    task_id: str,
    workspace: Optional[str] = None,
    agent_id: str = "orchestrator",
    run_id: Optional[str] = None,
) -> None:
    """Register that session_id should be re-invoked on agent_id when task_id finishes.

    When run_id is given, the continuation fires only when that specific run
    closes. Other runs on the same task — most importantly the orchestrator's
    own continuation run, which carries the same task_id — cannot trigger it.
    """
    doc = {
        "session_id": session_id,
        "task_id": str(task_id),
        "workspace": workspace,
        "agent_id": agent_id,
        "run_id": str(run_id) if run_id else None,
        "registered_at": _utc_now_iso(),
    }
    with db.transaction() as conn:
        # Deduplicate: one pending continuation per (session_id, task_id)
        conn.execute("DELETE FROM continuations WHERE session_id = ? AND task_id = ?",
                     (str(session_id), str(task_id)))
        conn.execute(
            "INSERT INTO continuations (session_id, task_id, run_id, doc) VALUES (?, ?, ?, ?)",
            (str(session_id), str(task_id), doc["run_id"], db.dumps(doc)))


def pop_continuations_for_task(task_id: str, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Remove and return pending continuations matching the finished run.

    Continuations bound to a run_id match only when that run finished;
    unbound entries (flows, records from before run binding) match any run
    of the task.
    """
    matched: List[Dict[str, Any]] = []
    with db.transaction() as conn:
        rows = conn.execute("SELECT seq, run_id, doc FROM continuations WHERE task_id = ?",
                            (str(task_id),)).fetchall()
        for r in rows:
            bound = r["run_id"]
            if not bound or bound == str(run_id or ""):
                doc = db.loads(r["doc"])
                if isinstance(doc, dict):
                    matched.append(doc)
                conn.execute("DELETE FROM continuations WHERE seq = ?", (r["seq"],))
    return matched


def rebind_continuations_to_run(task_id: str, run_id: str) -> None:
    """Re-point run-bound continuations for task_id at a new run_id.

    Needed when a task resumes under a fresh run (e.g. after an ask_user
    pause) so the pending continuation follows the run that will actually
    finish the work.
    """
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT seq, doc FROM continuations WHERE task_id = ? AND run_id IS NOT NULL",
            (str(task_id),)).fetchall()
        for r in rows:
            doc = db.loads(r["doc"]) or {}
            doc["run_id"] = str(run_id)
            conn.execute("UPDATE continuations SET run_id = ?, doc = ? WHERE seq = ?",
                         (str(run_id), db.dumps(doc), r["seq"]))


def drop_continuations_for_task(task_id: str) -> int:
    """Remove every pending continuation for a task, regardless of run binding.

    Used when a run is stopped by the user: the stop is a deliberate halt of the
    whole task, so no follow-up orchestrator should fire for it — neither the
    continuation bound to the stopped run nor any unbound leftover. Returns the
    number of entries removed.
    """
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM continuations WHERE task_id = ?", (str(task_id),))
        return cur.rowcount


def has_continuations_for_task(task_id: str) -> bool:
    """Return True if there are pending continuations for the given task_id (non-destructive)."""
    row = db.get_conn().execute(
        "SELECT 1 FROM continuations WHERE task_id = ? LIMIT 1", (str(task_id),)).fetchone()
    return row is not None
