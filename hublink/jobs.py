"""
The mailbox between this hub and its nodes (tables ``node_jobs`` and
``node_job_chunks``, migration 0042).

A node accepts no connection, so every request this hub wants a node to
answer is a row here: :func:`submit` writes it, the node's long poll
(:func:`take`) hands it over, the node posts the response head
(:func:`set_head`), the body in pieces (:func:`append_chunk`) and the end
(:func:`finish`), and the caller that submitted it reads them back
(:func:`wait_head`, :func:`iter_chunks`). Pieces are base64 text so a
streamed model answer and a binary body travel the same way.

Any API replica can submit and any can serve the poll: the database is the
meeting point, nothing is pinned to a process. Rows are deleted once the
caller has read them (:func:`discard`) and swept when they go stale
(:func:`sweep`), so the tables stay a few rows long.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

from common import db

log = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_TAKEN = "taken"
STATUS_HEADED = "headed"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

POLL_SECONDS = 0.15
#: A job nobody took within this many seconds fails: the node is away.
TAKE_TIMEOUT_SECONDS = 30.0
#: A finished job the caller did not discard is swept after this.
KEEP_FINISHED_SECONDS = 600.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _row(row: Any) -> Dict[str, Any]:
    rec = dict(row)
    rec["request"] = db.loads(rec.get("request"), {}) or {}
    rec["head"] = db.loads(rec.get("head"), None)
    return rec


# ── the hub's side ───────────────────────────────────────────────────────────

def submit(node_id: str, *, method: str, path: str, headers: Optional[Dict[str, str]] = None,
           body: bytes = b"", timeout: float = 120.0) -> str:
    job_id = f"nj-{uuid.uuid4().hex[:20]}"
    request = {
        "method": method.upper(), "path": path, "headers": dict(headers or {}),
        "body_b64": base64.b64encode(body or b"").decode("ascii"),
        "timeout": float(timeout),
    }
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO node_jobs (job_id, node_id, status, request, timeout_s, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, str(node_id), STATUS_PENDING, db.dumps(request), float(timeout), _iso(_now())))
    return job_id


def get(job_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute("SELECT * FROM node_jobs WHERE job_id = ?", (str(job_id),)).fetchone()
    return _row(row) if row is not None else None


def discard(job_id: str) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM node_job_chunks WHERE job_id = ?", (str(job_id),))
        conn.execute("DELETE FROM node_jobs WHERE job_id = ?", (str(job_id),))


def fail(job_id: str, error: str) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE node_jobs SET status = ?, error = ?, finished_at = ? "
            "WHERE job_id = ? AND status NOT IN (?, ?)",
            (STATUS_FAILED, error[:2000], _iso(_now()), str(job_id), STATUS_DONE, STATUS_FAILED))


async def wait_head(job_id: str, *, timeout: float) -> Dict[str, Any]:
    """The response head once the node posted it. Raises :class:`JobFailed`
    when the job failed, was never taken, or ``timeout`` ran out."""
    deadline = time.monotonic() + max(1.0, timeout)
    take_deadline = time.monotonic() + TAKE_TIMEOUT_SECONDS
    while True:
        rec = await asyncio.to_thread(get, job_id)
        if rec is None:
            raise JobFailed("the job disappeared")
        status = str(rec.get("status") or "")
        if status in (STATUS_HEADED, STATUS_DONE) and rec.get("head") is not None:
            return rec["head"]
        if status == STATUS_FAILED:
            raise JobFailed(str(rec.get("error") or "the node reported a failure"))
        if status == STATUS_DONE:
            # Closed without a head: an error end before anything was sent.
            raise JobFailed(str(rec.get("error") or "the node closed the job without answering"))
        now = time.monotonic()
        if status == STATUS_PENDING and now > take_deadline:
            await asyncio.to_thread(fail, job_id, "the node did not pick the request up")
            raise JobFailed("the node did not pick the request up (it may be offline)")
        if now > deadline:
            await asyncio.to_thread(fail, job_id, "timed out waiting for the node")
            raise JobFailed("timed out waiting for the node")
        await asyncio.sleep(POLL_SECONDS)


def _chunks_after(job_id: str, seq: int) -> List[Any]:
    rows = db.get_conn().execute(
        "SELECT seq, data FROM node_job_chunks WHERE job_id = ? AND seq > ? ORDER BY seq",
        (str(job_id), int(seq))).fetchall()
    return [(int(r["seq"]), str(r["data"])) for r in rows]


def _status(job_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT status, error FROM node_jobs WHERE job_id = ?", (str(job_id),)).fetchone()
    return dict(row) if row is not None else None


async def iter_chunks(job_id: str, *, timeout: float) -> AsyncIterator[bytes]:
    """The body pieces as they land, until the node closes the job."""
    deadline = time.monotonic() + max(1.0, timeout)
    seq = 0
    while True:
        pieces = await asyncio.to_thread(_chunks_after, job_id, seq)
        for s, data in pieces:
            seq = s
            yield base64.b64decode(data)
        state = await asyncio.to_thread(_status, job_id)
        if state is None:
            return
        status = str(state.get("status") or "")
        if status in (STATUS_DONE, STATUS_FAILED):
            # Pieces that landed between the read and the status change.
            for s, data in await asyncio.to_thread(_chunks_after, job_id, seq):
                seq = s
                yield base64.b64decode(data)
            if status == STATUS_FAILED and state.get("error"):
                log.debug("node job %s ended with error: %s", job_id, state.get("error"))
            return
        if time.monotonic() > deadline:
            await asyncio.to_thread(fail, job_id, "timed out streaming from the node")
            return
        await asyncio.sleep(POLL_SECONDS)


async def collect(job_id: str, *, timeout: float) -> bytes:
    parts: List[bytes] = []
    async for piece in iter_chunks(job_id, timeout=timeout):
        parts.append(piece)
    return b"".join(parts)


class JobFailed(RuntimeError):
    pass


# ── the node's side (served by routes/hublink.py) ───────────────────────────

def take(node_id: str, *, limit: int = 8) -> List[Dict[str, Any]]:
    """Hand the node its pending requests, oldest first, marking them taken.
    One read-modify-write, so two polls (two replicas, or a retry) never
    hand the same request out twice."""
    now = _iso(_now())
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM node_jobs WHERE node_id = ? AND status = ? "
            "ORDER BY created_at ASC LIMIT ?", (str(node_id), STATUS_PENDING, int(limit))).fetchall()
        out = []
        for row in rows:
            conn.execute("UPDATE node_jobs SET status = ?, taken_at = ? WHERE job_id = ?",
                         (STATUS_TAKEN, now, row["job_id"]))
            rec = _row(row)
            rec["status"] = STATUS_TAKEN
            out.append({"job_id": rec["job_id"], **rec["request"]})
        return out


def pending_count(node_id: str) -> int:
    row = db.get_conn().execute(
        "SELECT COUNT(*) AS n FROM node_jobs WHERE node_id = ? AND status = ?",
        (str(node_id), STATUS_PENDING)).fetchone()
    return int(row["n"] if row is not None else 0)


def owner_of(job_id: str) -> Optional[str]:
    row = db.get_conn().execute("SELECT node_id FROM node_jobs WHERE job_id = ?", (str(job_id),)).fetchone()
    return str(row["node_id"]) if row is not None else None


def set_head(job_id: str, *, status: int, headers: Dict[str, str]) -> bool:
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE node_jobs SET status = ?, head = ? WHERE job_id = ? AND status = ?",
            (STATUS_HEADED, db.dumps({"status": int(status), "headers": dict(headers or {})}),
             str(job_id), STATUS_TAKEN))
        return int(getattr(cur, "rowcount", 0) or 0) > 0


def append_chunk(job_id: str, data_b64: str) -> int:
    """Append one base64 piece; returns its sequence number."""
    with db.transaction() as conn:
        row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM node_job_chunks WHERE job_id = ?",
                           (str(job_id),)).fetchone()
        seq = int(row["m"] if row is not None else 0) + 1
        conn.execute("INSERT INTO node_job_chunks (job_id, seq, data) VALUES (?, ?, ?)",
                     (str(job_id), seq, str(data_b64)))
        return seq


def finish(job_id: str, *, error: Optional[str] = None) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE node_jobs SET status = ?, error = ?, finished_at = ? WHERE job_id = ?",
            (STATUS_FAILED if error else STATUS_DONE, (error or "")[:2000] or None,
             _iso(_now()), str(job_id)))


# ── housekeeping ─────────────────────────────────────────────────────────────

def sweep() -> Dict[str, int]:
    """Fail jobs whose timeout ran out and drop finished ones nobody read."""
    now = _now()
    failed = dropped = 0
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT job_id, status, timeout_s, created_at, finished_at FROM node_jobs").fetchall()
        for row in rows:
            status = str(row["status"])
            created = _parse(row["created_at"]) or now
            if status in (STATUS_DONE, STATUS_FAILED):
                finished = _parse(row["finished_at"]) or created
                if finished + timedelta(seconds=KEEP_FINISHED_SECONDS) < now:
                    conn.execute("DELETE FROM node_job_chunks WHERE job_id = ?", (row["job_id"],))
                    conn.execute("DELETE FROM node_jobs WHERE job_id = ?", (row["job_id"],))
                    dropped += 1
                continue
            per_job = row["timeout_s"]
            limit = float(120.0 if per_job is None else per_job) + TAKE_TIMEOUT_SECONDS
            if created + timedelta(seconds=limit) < now:
                conn.execute(
                    "UPDATE node_jobs SET status = ?, error = ?, finished_at = ? WHERE job_id = ?",
                    (STATUS_FAILED, "swept: no answer from the node in time", _iso(now), row["job_id"]))
                failed += 1
    return {"failed": failed, "dropped": dropped}


__all__ = [
    "JobFailed", "KEEP_FINISHED_SECONDS", "STATUS_DONE", "STATUS_FAILED", "STATUS_HEADED",
    "STATUS_PENDING", "STATUS_TAKEN", "TAKE_TIMEOUT_SECONDS", "append_chunk", "collect",
    "discard", "fail", "finish", "get", "iter_chunks", "owner_of", "pending_count", "set_head",
    "submit", "sweep", "take", "wait_head",
]
