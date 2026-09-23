"""Outbound delivery: webhooks and Slack incoming-webhooks.

Delivery never blocks the caller that raised the event, and it survives the
process that raised it. :func:`dispatch` writes the (endpoint, event) pair as
a row in the ``outbox`` table (``common/migrations/0007_scale.sql``) and
wakes the drainer thread; the drainer, on whichever process holds the
``outbox`` lease (``common/leases.py``), delivers every due row through
:func:`deliver` and either marks it delivered or schedules a retry with
backoff, up to :data:`MAX_ATTEMPTS` per row. :func:`deliver` itself retries
once on a 5xx status or a connection error and never raises: a broken
endpoint must never break the run or notification that triggered it.

With one backend replica this is the same thread and the same timing as
before, plus a row that outlives a crash. With several, exactly one of them
drains the table, so an event is delivered once, and a replica dying with
deliveries pending loses none of them.

See ``docs/notifications.md`` for the event shape, headers and signature
scheme this module produces.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

import requests

from notify.inbound import sign_payload

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 5
RETRY_BACKOFF_SECONDS = 0.5

#: Attempts per outbox row before it is given up on (each attempt is one
#: :func:`deliver`, which itself tries the endpoint twice).
MAX_ATTEMPTS = 5
#: Backoff between outbox attempts: 30s, 60s, 120s, ... capped at ten minutes.
RETRY_BASE_SECONDS = 30.0
RETRY_MAX_SECONDS = 600.0
#: How often the drainer looks for rows whose retry time has come when
#: nothing woke it. Also the cadence at which a replica that does not hold
#: the lease checks whether it can take it.
POLL_SECONDS = 15.0
#: The lease role the drainer runs under.
LEASE_ROLE = "outbox"

# A sentinel distinct from any real item, so the drainer loop can tell "stop"
# from a wake-up without a second queue.
_SHUTDOWN = object()
_WAKE = object()

_queue: "queue.Queue[Any]" = queue.Queue()
_worker: Optional[threading.Thread] = None
_worker_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_worker() -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_worker_loop, name="notify-outbound", daemon=True)
        _worker.start()


def _worker_loop() -> None:
    while True:
        try:
            item = _queue.get(timeout=POLL_SECONDS)
        except queue.Empty:
            # Nothing woke us: look for rows whose retry time has come.
            try:
                drain()
            except Exception:
                log.debug("notify.outbound: periodic drain failed", exc_info=True)
            continue
        try:
            if item is _SHUTDOWN:
                return
            drain()
        except Exception:
            log.debug("notify.outbound: drain failed", exc_info=True)
        finally:
            _queue.task_done()


def dispatch(endpoint: Dict[str, Any], event: Dict[str, Any]) -> None:
    """Record one delivery and return immediately; the drainer does the work."""
    try:
        enqueue(endpoint, event)
    except Exception:
        # No database (a bare test harness, a CLI without state): fall back to
        # delivering from the thread directly, best-effort, as before.
        log.debug("notify.outbound: outbox write failed, delivering directly", exc_info=True)
        _ensure_worker()
        _queue.put((endpoint, event))
        return
    _ensure_worker()
    _queue.put(_WAKE)


def start() -> None:
    """Start the drainer without an event to deliver (backend startup), so
    rows left behind by an earlier process go out promptly."""
    _ensure_worker()
    _queue.put(_WAKE)


def shutdown(timeout: float = 2.0) -> None:
    """Best-effort drain of whatever is still queued. Never raises.

    Called from the app's lifespan shutdown. A delivery still in flight when
    the process exits stays in the outbox and is retried by the next holder
    of the lease.
    """
    try:
        _queue.put(_SHUTDOWN)
        _queue.join()
    except Exception:
        log.debug("notify.outbound: shutdown drain failed", exc_info=True)


# ── the outbox table ─────────────────────────────────────────────────────────

def enqueue(endpoint: Dict[str, Any], event: Dict[str, Any]) -> int:
    """Write one delivery row; returns its id."""
    from common import db

    now = _now().isoformat()
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO outbox (endpoint, event, attempts, next_attempt_at, created_at) "
            "VALUES (?, ?, 0, ?, ?) RETURNING id",
            (json.dumps(endpoint, default=str), json.dumps(event, default=str), now, now))
        row = cur.fetchone()
        return int(row[0])


def pending(limit: int = 100) -> List[Dict[str, Any]]:
    """Undelivered rows whose retry time has come, oldest first."""
    from common import db

    rows = db.get_conn().execute(
        "SELECT id, endpoint, event, attempts, next_attempt_at, created_at, last_error "
        "FROM outbox WHERE delivered_at IS NULL AND next_attempt_at <= ? "
        "ORDER BY id ASC LIMIT ?", (_now().isoformat(), int(limit))).fetchall()
    out = []
    for row in rows:
        rec = dict(row)
        rec["endpoint"] = json.loads(rec["endpoint"] or "{}")
        rec["event"] = json.loads(rec["event"] or "{}")
        out.append(rec)
    return out


def stats() -> Dict[str, int]:
    """Undelivered and given-up counts, for health and metrics."""
    from common import db

    conn = db.get_conn()
    waiting = conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE delivered_at IS NULL AND attempts < ?",
        (MAX_ATTEMPTS,)).fetchone()[0]
    dead = conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE delivered_at IS NULL AND attempts >= ?",
        (MAX_ATTEMPTS,)).fetchone()[0]
    return {"pending": int(waiting or 0), "dead": int(dead or 0)}


def prune(older_than_days: int = 7) -> int:
    """Drop delivered rows, and given-up rows, older than the window."""
    from common import db

    cutoff = (_now() - timedelta(days=older_than_days)).isoformat()
    with db.transaction() as conn:
        cur = conn.execute(
            "DELETE FROM outbox WHERE created_at < ? AND (delivered_at IS NOT NULL OR attempts >= ?)",
            (cutoff, MAX_ATTEMPTS))
        return int(getattr(cur, "rowcount", 0) or 0)


def _record_attempt(row_id: int, ok: bool, attempts: int, error: Optional[str]) -> None:
    from common import db

    now = _now()
    with db.transaction() as conn:
        if ok:
            conn.execute(
                "UPDATE outbox SET attempts = ?, delivered_at = ?, last_error = NULL WHERE id = ?",
                (attempts, now.isoformat(), row_id))
            return
        backoff = min(RETRY_BASE_SECONDS * (2 ** max(attempts - 1, 0)), RETRY_MAX_SECONDS)
        conn.execute(
            "UPDATE outbox SET attempts = ?, next_attempt_at = ?, last_error = ? WHERE id = ?",
            (attempts, (now + timedelta(seconds=backoff)).isoformat(),
             (error or "delivery failed")[:500], row_id))


def drain(*, require_lease: bool = True) -> int:
    """Deliver every due row. Returns how many were delivered.

    Runs only on the holder of the ``outbox`` lease when ``require_lease`` is
    set (the default): with several replicas, one drains and the rest wait.
    """
    if require_lease:
        from common import leases
        if not leases.hold(LEASE_ROLE, ttl_seconds=max(POLL_SECONDS * 4, 60.0)):
            return 0
    delivered = 0
    for row in pending():
        attempts = int(row.get("attempts") or 0)
        if attempts >= MAX_ATTEMPTS:
            continue
        ok = deliver(row["endpoint"], row["event"])
        _record_attempt(int(row["id"]), bool(ok), attempts + 1,
                        None if ok else "endpoint did not accept the delivery")
        if ok:
            delivered += 1
    return delivered


# ── the HTTP call ────────────────────────────────────────────────────────────

def deliver(endpoint: Dict[str, Any], event: Dict[str, Any]) -> bool:
    """Deliver one event to one endpoint. Best-effort: never raises. Returns
    whether the endpoint accepted it."""
    try:
        kind = endpoint.get("kind")
        if kind == "webhook":
            return _deliver_webhook(endpoint, event)
        if kind == "slack":
            return _deliver_slack(endpoint, event)
        return False
    except Exception:
        log.debug("notify.outbound: delivery failed for endpoint %s", endpoint.get("id"), exc_info=True)
        return False


def _deliver_webhook(endpoint: Dict[str, Any], event: Dict[str, Any]) -> bool:
    body = json.dumps(event, ensure_ascii=False, default=str).encode("utf-8")
    secret = endpoint.get("secret") or ""
    headers = {
        "Content-Type": "application/json",
        "X-AgentsHub-Event": str(event.get("type") or ""),
        "X-AgentsHub-Delivery": str(uuid4()),
        "X-AgentsHub-Timestamp": str(int(time.time())),
    }
    if secret:
        headers["X-AgentsHub-Signature"] = sign_payload(secret, body)
    return _post_with_retry(endpoint.get("url") or "", body, headers)


def _deliver_slack(endpoint: Dict[str, Any], event: Dict[str, Any]) -> bool:
    payload = _slack_payload(event)
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    return _post_with_retry(endpoint.get("url") or "", body, headers)


def _slack_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    """A Slack incoming-webhook payload summarizing one event."""
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    title = str(data.get("title") or event.get("type") or "Notification")
    body_text = str(data.get("body") or "")
    severity = str(data.get("severity") or "info")
    workspace = str(event.get("workspace") or "default")

    lines = [f"*{title}*"]
    if body_text:
        lines.append(body_text)
    text = "\n".join(lines)

    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*"}}]
    if body_text:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": body_text}})
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"{severity}, {workspace}"}],
    })
    return {"text": text, "blocks": blocks}


def _post_with_retry(url: str, body: bytes, headers: Dict[str, str]) -> bool:
    if not url:
        return False
    for attempt in (1, 2):
        try:
            resp = requests.post(url, data=body, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
            if resp.status_code < 500:
                return True
        except requests.exceptions.RequestException:
            pass
        if attempt == 1:
            time.sleep(RETRY_BACKOFF_SECONDS)
    log.debug("notify.outbound: delivery to %s did not succeed after one retry", url)
    return False
