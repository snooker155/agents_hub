"""
Messages a person sends to a run while it is working (docs/steering.md).

Until this existed a second message to a busy agent waited in a mailbox
(``instances/inbox.py``) until the run was over, and the chat had no way to
talk to a turn in progress at all. A steering message is written here instead
and reaches the run one of two ways:

- ``inject``: the agent loop claims it before its next model call and places
  it in the context right after the tool results the run had by then
  (``agents/loop_ext/steering.py``). The run keeps going with the new words
  in view.
- ``interrupt``: the run is stopped and the message starts the next one (a
  task relaunches its agent with the message in the instruction, a chat
  client sends it as its next turn); see ``dashboard/backend/routes/steering.py``.

The loop checks for messages before every model call, from the backend
process for a chat turn and from the run's own process (or container, over
``common.state_transport``) for a task. That check has to cost nothing when
nobody wrote: :func:`claim_pending` is one indexed SELECT on
``(run_id, delivered_at)`` in that case and only opens a write transaction
when there is something to take.

Rows live in ``run_steering`` (migration 0019).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from common import db

log = logging.getLogger(__name__)

MODE_INJECT = "inject"
MODE_INTERRUPT = "interrupt"
MODES = (MODE_INJECT, MODE_INTERRUPT)

STATUS_PENDING = "pending"
STATUS_DELIVERED = "delivered"
STATUS_EXPIRED = "expired"
STATUS_INTERRUPTED = "interrupted"
STATUS_FAILED = "failed"

#: Longest message kept, in characters. A steering message is a sentence or a
#: paragraph; anything longer is a new task and belongs in one.
MAX_BODY_CHARS = 20000

_COLUMNS = ("seq", "msg_id", "run_id", "body", "mode", "author_id", "author_name",
            "created_at", "delivered_at", "delivered_step", "status", "next_run_id")

# The loop's check: pending inject messages of one run. Served by the index on
# (run_id, delivered_at).
_PENDING_WHERE = ("run_id = ? AND delivered_at IS NULL AND status = 'pending' "
                  "AND mode = 'inject'")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row: Any) -> Dict[str, Any]:
    out = {k: row[k] for k in _COLUMNS}
    out.pop("seq", None)
    step = out.get("delivered_step")
    out["delivered_step"] = int(step) if step is not None else None
    return out


def _author_fields(author: Any) -> tuple:
    """``(author_id, author_name)`` from a principal, a dict or a plain name."""
    if author is None:
        return None, None
    if isinstance(author, str):
        return None, author or None
    if isinstance(author, dict):
        return (str(author.get("id") or author.get("author_id") or "") or None,
                str(author.get("name") or author.get("author_name") or author.get("username") or "") or None)
    return (str(getattr(author, "id", "") or "") or None,
            str(getattr(author, "username", "") or getattr(author, "name", "") or "") or None)


#: Author id prefix of a steering message that stands for an instance
#: mailbox message (``inbox:<msg_id>``, see instances/delivery.py).
INBOX_AUTHOR_PREFIX = "inbox:"


def inbox_message_id(author_id: Any) -> str:
    """The mailbox message a steering message stands for, or ""."""
    value = str(author_id or "")
    return value[len(INBOX_AUTHOR_PREFIX):] if value.startswith(INBOX_AUTHOR_PREFIX) else ""


def post(run_id: str, body: str, *, mode: str = MODE_INJECT,
         author: Any = None) -> Dict[str, Any]:
    """Store one message for ``run_id``. Returns the stored row.

    Raises ``ValueError`` for an empty body, an unknown mode or a missing run
    id; the caller (the route) has already checked that the run is running.
    """
    run_id = str(run_id or "").strip()
    text = str(body or "").strip()
    if not run_id:
        raise ValueError("run_id is required")
    if not text:
        raise ValueError("the message is empty")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    text = text[:MAX_BODY_CHARS]
    author_id, author_name = _author_fields(author)
    msg_id = "steer_" + uuid.uuid4().hex[:16]
    created = _now()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO run_steering (msg_id, run_id, body, mode, author_id, author_name, "
            "created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (msg_id, run_id, text, mode, author_id, author_name, created, STATUS_PENDING),
        )
    return {
        "msg_id": msg_id, "run_id": run_id, "body": text, "mode": mode,
        "author_id": author_id, "author_name": author_name, "created_at": created,
        "delivered_at": None, "delivered_step": None, "status": STATUS_PENDING,
        "next_run_id": None,
    }


def get(msg_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM run_steering WHERE msg_id = ?", (str(msg_id),)).fetchone()
    return _row(row) if row is not None else None


def pending_count(run_id: str) -> int:
    """How many inject messages are waiting for the run's next model call."""
    if not run_id:
        return 0
    row = db.get_conn().execute(
        f"SELECT COUNT(*) AS n FROM run_steering WHERE {_PENDING_WHERE}",
        (str(run_id),)).fetchone()
    return int((row["n"] if row else 0) or 0)


def claim_pending(run_id: str, step: int) -> List[Dict[str, Any]]:
    """Take every pending inject message of ``run_id``, oldest first, marking
    each delivered at ``step`` (the number of tool results the run has).

    Atomic: each row is marked with a conditional UPDATE inside one write
    transaction and only the rows this call actually changed are returned, so
    two claimers (a retry racing the original, say) never both deliver one
    message. With nothing pending this is a single indexed SELECT and no
    transaction at all.
    """
    if not run_id:
        return []
    conn = db.get_conn()
    probe = conn.execute(
        f"SELECT 1 FROM run_steering WHERE {_PENDING_WHERE} LIMIT 1", (str(run_id),)).fetchone()
    if probe is None:
        return []
    claimed: List[Dict[str, Any]] = []
    when = _now()
    with db.transaction() as tx:
        rows = tx.execute(
            f"SELECT * FROM run_steering WHERE {_PENDING_WHERE} ORDER BY seq",
            (str(run_id),)).fetchall()
        for row in rows:
            inbox_id = inbox_message_id(row["author_id"])
            if inbox_id:
                # A message written to a busy instance (instances/delivery.py)
                # waits in its mailbox too. Taking it here takes it from the
                # mailbox in the same transaction, so it is answered once:
                # here, or by the mailbox if the run ended first.
                took = tx.execute(
                    "UPDATE instance_inbox SET delivered_at = ?, run_id = ? "
                    "WHERE msg_id = ? AND delivered_at IS NULL",
                    (when, str(run_id), inbox_id))
                if getattr(took, "rowcount", 1) == 0:
                    tx.execute("UPDATE run_steering SET status = ? WHERE msg_id = ?",
                               (STATUS_EXPIRED, row["msg_id"]))
                    continue
            cur = tx.execute(
                "UPDATE run_steering SET delivered_at = ?, delivered_step = ?, status = ? "
                "WHERE msg_id = ? AND delivered_at IS NULL AND status = 'pending'",
                (when, int(step), STATUS_DELIVERED, row["msg_id"]))
            if getattr(cur, "rowcount", 1) == 0:
                continue
            item = _row(row)
            item.update(delivered_at=when, delivered_step=int(step), status=STATUS_DELIVERED)
            claimed.append(item)
    return claimed


def delivered(run_id: str) -> List[Dict[str, Any]]:
    """The inject messages the loop has already taken for ``run_id``, in
    order. A run that picks up again under the same id (a checkpoint resume,
    the chat's retry after folding its history) reads these back so what the
    person said is still in its context."""
    if not run_id:
        return []
    rows = db.get_conn().execute(
        "SELECT * FROM run_steering WHERE run_id = ? AND status = 'delivered' "
        "AND mode = 'inject' ORDER BY seq", (str(run_id),)).fetchall()
    return [_row(r) for r in rows]


def list_for_run(run_id: str) -> List[Dict[str, Any]]:
    """Every message written to ``run_id``, oldest first, with its state."""
    rows = db.get_conn().execute(
        "SELECT * FROM run_steering WHERE run_id = ? ORDER BY seq", (str(run_id),)).fetchall()
    return [_row(r) for r in rows]


def undelivered(run_id: str) -> List[Dict[str, Any]]:
    """The inject messages still waiting for ``run_id``, oldest first."""
    if not run_id:
        return []
    rows = db.get_conn().execute(
        f"SELECT * FROM run_steering WHERE {_PENDING_WHERE} ORDER BY seq",
        (str(run_id),)).fetchall()
    return [_row(r) for r in rows]


def mark_expired(run_id: str) -> List[Dict[str, Any]]:
    """Close the messages a finished run never took. Returns them, oldest
    first, so the caller can hand them on (the chat sends them as its next
    turn). Only rows this call changed are returned."""
    if not run_id:
        return []
    out: List[Dict[str, Any]] = []
    when = _now()
    with db.transaction() as conn:
        rows = conn.execute(
            f"SELECT * FROM run_steering WHERE {_PENDING_WHERE} ORDER BY seq",
            (str(run_id),)).fetchall()
        for row in rows:
            cur = conn.execute(
                "UPDATE run_steering SET status = ?, delivered_at = ? "
                "WHERE msg_id = ? AND status = 'pending'",
                (STATUS_EXPIRED, when, row["msg_id"]))
            if getattr(cur, "rowcount", 1) == 0:
                continue
            item = _row(row)
            item.update(status=STATUS_EXPIRED, delivered_at=when)
            out.append(item)
    return out


def mark(msg_id: str, status: str, *, next_run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Set a message's status (an interrupt's outcome). Returns the row."""
    with db.transaction() as conn:
        conn.execute(
            "UPDATE run_steering SET status = ?, next_run_id = COALESCE(?, next_run_id), "
            "delivered_at = COALESCE(delivered_at, ?) WHERE msg_id = ?",
            (str(status), next_run_id, _now(), str(msg_id)))
    return get(msg_id)


__all__ = [
    "MODES", "MODE_INJECT", "MODE_INTERRUPT", "MAX_BODY_CHARS",
    "STATUS_PENDING", "STATUS_DELIVERED", "STATUS_EXPIRED", "STATUS_INTERRUPTED",
    "STATUS_FAILED",
    "post", "get", "pending_count", "claim_pending", "delivered", "list_for_run",
    "undelivered", "mark_expired", "mark",
]
