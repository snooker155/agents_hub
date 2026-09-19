"""
Per-instance mailbox.

Writing to a *live* instance is not the same as starting a new run: the copy
already exists, holds its own context and is sitting in its poll loop, so the
message has to reach that process rather than spawn a rival. The mailbox is the
handoff — the API writes a row, the instance's own loop claims it on its next
tick and answers in its own run.

Finished instances have no loop to claim anything; the API delivers their
messages directly through the chat pipeline instead (see the instances route),
with the history rebuilt by :mod:`instances.history`.

Claiming is atomic: ``claim_next`` marks the row delivered in the same statement
that selects it, so two pollers on the same instance can never answer one
message twice.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from common import db
from instances.store import utc_iso


def enqueue(instance_id: str, body: str, *, origin: str = "web") -> str:
    """Queue a message for an instance. Returns the message id."""
    msg_id = "msg_" + uuid.uuid4().hex[:16]
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO instance_inbox (msg_id, instance_id, body, origin, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (msg_id, str(instance_id), str(body), origin, utc_iso()),
        )
    return msg_id


def has_pending(instance_id: str) -> bool:
    row = db.get_conn().execute(
        "SELECT 1 FROM instance_inbox WHERE instance_id = ? AND delivered_at IS NULL LIMIT 1",
        (str(instance_id),),
    ).fetchone()
    return row is not None


def pending(instance_id: str) -> List[Dict[str, Any]]:
    rows = db.get_conn().execute(
        "SELECT * FROM instance_inbox WHERE instance_id = ? AND delivered_at IS NULL "
        "ORDER BY created_at",
        (str(instance_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def claim_next(instance_id: str) -> Optional[Dict[str, Any]]:
    """Take the oldest undelivered message, marking it delivered atomically."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM instance_inbox WHERE instance_id = ? AND delivered_at IS NULL "
            "ORDER BY created_at LIMIT 1",
            (str(instance_id),),
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE instance_inbox SET delivered_at = ? WHERE msg_id = ?",
                     (utc_iso(), row["msg_id"]))
        return {**dict(row), "delivered_at": utc_iso()}


def claim_next_for_any(instance_ids: List[str]) -> Optional[Dict[str, Any]]:
    """``claim_next`` across several instances — one query for a node that
    carries more than one copy."""
    ids = [str(i) for i in instance_ids if i]
    if not ids:
        return None
    with db.transaction() as conn:
        row = conn.execute(
            f"SELECT * FROM instance_inbox WHERE instance_id IN ({', '.join('?' * len(ids))}) "
            "AND delivered_at IS NULL ORDER BY created_at LIMIT 1",
            ids,
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE instance_inbox SET delivered_at = ? WHERE msg_id = ?",
                     (utc_iso(), row["msg_id"]))
        return {**dict(row), "delivered_at": utc_iso()}


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
