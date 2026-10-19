"""
Per-instance mailbox.

Writing to a *live* instance is not the same as starting a new run: the copy
already exists, holds its own context and is sitting in its process, so the
message has to reach that process rather than spawn a rival. The mailbox is the
handoff: the API writes a row, the carrier finds it on its next look at the
mailbox (instances/wake.py), claims it and answers in its own run.

Finished instances have no process to claim anything; the API delivers their
messages directly through the chat pipeline instead (see the instances route),
with the history rebuilt by :mod:`instances.history`.

Every message belongs to a conversation of its instance. The instance page
writes to the main one (``conversation_id`` NULL, spelled ``"main"`` on the
API); a caller may name its own, so two callers of one copy never see each
other's turns. A carrier answers several conversations at once
but only one message of any conversation at a time, which is what
``claim_next(exclude_conversations=...)`` is for.

Claiming is atomic: ``claim_next`` marks the row delivered in the same
transaction that selects it, so two claimers on the same instance can never
answer one message twice.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Iterable, List, Optional

from common import db
from instances.store import utc_iso

log = logging.getLogger(__name__)

#: How the API spells the conversation stored as NULL.
MAIN_CONVERSATION = "main"
MAX_CONVERSATION_ID = 120

#: The only message kind: text the carrier answers with the conversation's
#: history (runtime/instance_run.py answer_message). The table's ``kind``,
#: ``payload`` and ``result`` columns are left from the full edition's jobs.
KIND_MESSAGE = "message"


def normalize_conversation(conversation_id: Optional[str]) -> Optional[str]:
    """The stored form of a conversation id: None for the main conversation."""
    cid = str(conversation_id or "").strip()
    if not cid or cid == MAIN_CONVERSATION:
        return None
    return cid[:MAX_CONVERSATION_ID]


def public_conversation(conversation_id: Optional[str]) -> str:
    """The API form of a stored conversation id."""
    return conversation_id or MAIN_CONVERSATION


def enqueue(instance_id: str, body: str, *, origin: str = "web",
            conversation_id: Optional[str] = None) -> str:
    """Queue a message for an instance and wake its carrier. Returns the message id."""
    msg_id = "msg_" + uuid.uuid4().hex[:16]
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO instance_inbox (msg_id, instance_id, body, origin, created_at, "
            "conversation_id, kind) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (msg_id, str(instance_id), str(body), origin, utc_iso(),
             normalize_conversation(conversation_id), KIND_MESSAGE),
        )
    return msg_id


def get(msg_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM instance_inbox WHERE msg_id = ?", (str(msg_id),)
    ).fetchone()
    return dict(row) if row is not None else None


def has_pending(instance_id: str) -> bool:
    row = db.get_conn().execute(
        "SELECT 1 FROM instance_inbox WHERE instance_id = ? AND delivered_at IS NULL LIMIT 1",
        (str(instance_id),),
    ).fetchone()
    return row is not None


def pending(instance_id: str, conversation_id: Optional[str] = None,
            *, any_conversation: bool = True) -> List[Dict[str, Any]]:
    """Undelivered messages, oldest first. ``any_conversation=False`` narrows
    to one conversation (``conversation_id`` None meaning the main one)."""
    sql = "SELECT * FROM instance_inbox WHERE instance_id = ? AND delivered_at IS NULL"
    params: List[Any] = [str(instance_id)]
    if not any_conversation:
        cid = normalize_conversation(conversation_id)
        if cid is None:
            sql += " AND conversation_id IS NULL"
        else:
            sql += " AND conversation_id = ?"
            params.append(cid)
    rows = db.get_conn().execute(sql + " ORDER BY created_at", params).fetchall()
    return [dict(r) for r in rows]


def claim_next(instance_id: str,
               exclude_conversations: Iterable[Optional[str]] = ()) -> Optional[Dict[str, Any]]:
    """Take the oldest undelivered message, marking it delivered atomically.

    ``exclude_conversations`` skips conversations the carrier is still
    answering (stored form, None for the main one), so a second message of a
    conversation waits for the first one's answer instead of racing it.
    """
    excluded = list(exclude_conversations or ())
    sql = "SELECT * FROM instance_inbox WHERE instance_id = ? AND delivered_at IS NULL"
    params: List[Any] = [str(instance_id)]
    if None in excluded:
        sql += " AND conversation_id IS NOT NULL"
    named = [c for c in excluded if c is not None]
    if named:
        sql += (f" AND (conversation_id IS NULL OR conversation_id NOT IN "
                f"({', '.join('?' * len(named))}))")
        params.extend(named)
    with db.transaction() as conn:
        row = conn.execute(sql + " ORDER BY created_at LIMIT 1", params).fetchone()
        if row is None:
            return None
        now = utc_iso()
        cur = conn.execute(
            "UPDATE instance_inbox SET delivered_at = ? WHERE msg_id = ? AND delivered_at IS NULL",
            (now, row["msg_id"]))
        if getattr(cur, "rowcount", 1) == 0:
            return None
        return {**dict(row), "delivered_at": now}


def attach_run(msg_id: str, run_id: str) -> None:
    """Record which run answered a message."""
    with db.transaction() as conn:
        conn.execute("UPDATE instance_inbox SET run_id = ? WHERE msg_id = ?",
                     (str(run_id), str(msg_id)))


def mark_error(msg_id: str, error: str) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE instance_inbox SET error = ? WHERE msg_id = ?",
                     (str(error)[:2000], str(msg_id)))


def history(instance_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    rows = db.get_conn().execute(
        "SELECT * FROM instance_inbox WHERE instance_id = ? ORDER BY created_at DESC LIMIT ?",
        (str(instance_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def conversations(instance_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    """The instance's conversations, most recent first: the main one always,
    plus every one a message or a run was recorded under."""
    conn = db.get_conn()
    seen: Dict[Optional[str], Dict[str, Any]] = {
        None: {"conversation_id": None, "messages": 0, "runs": 0, "last_at": None},
    }
    for row in conn.execute(
        "SELECT conversation_id, COUNT(*) AS n, MAX(created_at) AS last_at "
        "FROM instance_inbox WHERE instance_id = ? GROUP BY conversation_id",
        (str(instance_id),),
    ).fetchall():
        rec = seen.setdefault(row["conversation_id"], {
            "conversation_id": row["conversation_id"], "messages": 0, "runs": 0, "last_at": None})
        rec["messages"] = int(row["n"])
        rec["last_at"] = max(filter(None, [rec["last_at"], row["last_at"]]), default=None)
    for row in conn.execute(
        "SELECT conversation_id, COUNT(*) AS n, MAX(COALESCE(started_at, created_at)) AS last_at "
        "FROM runs WHERE instance_id = ? GROUP BY conversation_id",
        (str(instance_id),),
    ).fetchall():
        rec = seen.setdefault(row["conversation_id"], {
            "conversation_id": row["conversation_id"], "messages": 0, "runs": 0, "last_at": None})
        rec["runs"] = int(row["n"])
        rec["last_at"] = max(filter(None, [rec["last_at"], row["last_at"]]), default=None)
    return _order_conversations(seen, limit)


def _order_conversations(seen: Dict[Optional[str], Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    items = list(seen.values())
    main = [i for i in items if i["conversation_id"] is None]
    rest = sorted((i for i in items if i["conversation_id"] is not None),
                  key=lambda i: i["last_at"] or "", reverse=True)
    out = []
    for rec in (main + rest)[:limit]:
        out.append({**rec, "conversation_id": public_conversation(rec["conversation_id"])})
    return out
