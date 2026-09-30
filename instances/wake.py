"""
Waking a carrier the moment its mailbox gets a message.

A resident instance (instances/carrier.py) waits on its mailbox between runs.
Polling the table every few seconds made a conversation feel like email, so
:func:`signal` (called by ``inbox.enqueue``) and :func:`wait` (called by the
carrier's loop) cut the wait to the time it takes to claim the row:

- with ``AGENTS_HUB_BROKER_URL`` set (the Redis the broker bridge already
  uses), ``signal`` pushes onto a per-instance list and ``wait`` blocks on it
  with ``BLPOP``, so the carrier wakes the instant the API writes, on any host;
- without it, ``wait`` checks the mailbox's indexed pending query every
  ``AGENTS_HUB_INSTANCE_POLL_SECONDS`` (default 0.5 s) and ``signal`` is a
  no-op. One indexed lookup per half second per live instance is cheap next to
  a model call, and needs nothing beyond the database every process shares.

Either way the mailbox row is the truth: a lost signal costs one poll
interval, never a message.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

log = logging.getLogger(__name__)

WAKE_PREFIX = "agents_hub:instance_wake:"
DEFAULT_POLL_SECONDS = 0.5

_client_lock = threading.Lock()
_client: Any = None
_client_url: Optional[str] = None


def poll_seconds() -> float:
    try:
        value = float(os.environ.get("AGENTS_HUB_INSTANCE_POLL_SECONDS", DEFAULT_POLL_SECONDS))
    except ValueError:
        value = DEFAULT_POLL_SECONDS
    return min(max(value, 0.05), 10.0)


def _broker_url() -> str:
    try:
        from common.config import settings
        return (settings.broker_url or "").strip()
    except Exception:  # noqa: BLE001 - no settings means no Redis: fall back to polling
        return ""


def _redis() -> Any:
    """A sync Redis client for the broker URL, or None without one."""
    global _client, _client_url
    url = _broker_url()
    if not url:
        return None
    with _client_lock:
        if _client is not None and _client_url == url:
            return _client
        try:
            import redis  # type: ignore[import-untyped]
            _client = redis.Redis.from_url(url, socket_timeout=35, socket_connect_timeout=5)
            _client_url = url
        except Exception:  # noqa: BLE001 - an unreachable Redis degrades to polling
            log.debug("wake: redis client unavailable", exc_info=True)
            _client = None
        return _client


def signal(instance_id: str) -> None:
    """Tell the instance's carrier to look at its mailbox now."""
    client = _redis()
    if client is None:
        return
    key = WAKE_PREFIX + str(instance_id)
    try:
        pipe = client.pipeline()
        pipe.lpush(key, "1")
        # A carrier that is gone never pops it; do not leave keys behind.
        pipe.expire(key, 120)
        pipe.execute()
    except Exception:  # noqa: BLE001 - the poll fallback still finds the message
        log.debug("wake: signal failed for %s", instance_id, exc_info=True)


def _pending(instance_id: str) -> bool:
    from instances import inbox
    try:
        return inbox.has_pending(instance_id)
    except Exception:  # noqa: BLE001 - a failed read is retried on the next tick
        return False


def wait(instance_id: str, timeout: float) -> bool:
    """Block up to ``timeout`` seconds for mail. True when a message may be
    waiting (woken, or the mailbox has one), False on a quiet timeout."""
    if _pending(instance_id):
        return True
    deadline = time.monotonic() + max(0.0, float(timeout))
    client = _redis()
    if client is not None:
        key = WAKE_PREFIX + str(instance_id)
        try:
            remaining = max(1, int(round(deadline - time.monotonic())))
            got = client.blpop([key], timeout=remaining)
            if got is not None:
                # Several signals collapse into one wake-up.
                client.delete(key)
                return True
            return _pending(instance_id)
        except Exception:  # noqa: BLE001 - fall through to polling for this wait
            log.debug("wake: blpop failed for %s", instance_id, exc_info=True)
    interval = poll_seconds()
    while True:
        now = time.monotonic()
        if now >= deadline:
            return False
        time.sleep(min(interval, deadline - now))
        if _pending(instance_id):
            return True
