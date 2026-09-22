"""Outbound delivery: webhooks and Slack incoming-webhooks.

Delivery never blocks the caller that raised the event. :func:`dispatch` puts
the (endpoint, event) pair on a small in-process queue; a single worker
thread drains it and calls :func:`deliver`, which does the actual HTTP call.
:func:`deliver` retries once on a 5xx status or a connection error, and never
raises — a broken endpoint must never break the run or notification that
triggered it.

See ``docs/notifications.md`` for the event shape, headers and signature
scheme this module produces.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any, Dict, Optional
from uuid import uuid4

import requests

from notify.inbound import sign_payload

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 5
RETRY_BACKOFF_SECONDS = 0.5

# A sentinel distinct from any real (endpoint, event) tuple, so the worker
# loop can tell "stop" from "nothing to deliver" without a second queue.
_SHUTDOWN = object()

_queue: "queue.Queue[Any]" = queue.Queue()
_worker: Optional[threading.Thread] = None
_worker_lock = threading.Lock()


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
        item = _queue.get()
        try:
            if item is _SHUTDOWN:
                return
            endpoint, event = item
            deliver(endpoint, event)
        finally:
            _queue.task_done()


def dispatch(endpoint: Dict[str, Any], event: Dict[str, Any]) -> None:
    """Queue one delivery. Returns immediately; the worker thread does the work."""
    _ensure_worker()
    _queue.put((endpoint, event))


def shutdown(timeout: float = 2.0) -> None:
    """Best-effort drain of whatever is still queued. Never raises.

    Called from the app's lifespan shutdown; a delivery still in flight when
    the process exits is simply lost, the same way an in-memory queue always
    is — outbound delivery here is best-effort, not at-least-once.
    """
    try:
        _queue.put(_SHUTDOWN)
        _queue.join()
    except Exception:
        log.debug("notify.outbound: shutdown drain failed", exc_info=True)


def deliver(endpoint: Dict[str, Any], event: Dict[str, Any]) -> None:
    """Deliver one event to one endpoint. Best-effort: never raises."""
    try:
        kind = endpoint.get("kind")
        if kind == "webhook":
            _deliver_webhook(endpoint, event)
        elif kind == "slack":
            _deliver_slack(endpoint, event)
    except Exception:
        log.debug("notify.outbound: delivery failed for endpoint %s", endpoint.get("id"), exc_info=True)


def _deliver_webhook(endpoint: Dict[str, Any], event: Dict[str, Any]) -> None:
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
    _post_with_retry(endpoint.get("url") or "", body, headers)


def _deliver_slack(endpoint: Dict[str, Any], event: Dict[str, Any]) -> None:
    payload = _slack_payload(event)
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    _post_with_retry(endpoint.get("url") or "", body, headers)


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


def _post_with_retry(url: str, body: bytes, headers: Dict[str, str]) -> None:
    if not url:
        return
    for attempt in (1, 2):
        try:
            resp = requests.post(url, data=body, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
            if resp.status_code < 500:
                return
        except requests.exceptions.RequestException:
            pass
        if attempt == 1:
            time.sleep(RETRY_BACKOFF_SECONDS)
    log.debug("notify.outbound: delivery to %s did not succeed after one retry", url)
