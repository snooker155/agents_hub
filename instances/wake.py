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
    from common import remote_state
    if remote_state.is_remote():
        return _wait_on_hub(instance_id, timeout)
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


#: Longest one wait on the hub may take (the carrier asks again after it).
HUB_WAIT_MAX = 25.0


def _wait_on_hub(instance_id: str, timeout: float) -> bool:
    """An agent container's wait: one long request the hub answers when mail
    arrives or the time is up (``GET /api/run-state/wake/<id>``), instead of
    asking for its mailbox every half second."""
    from common import remote_state
    from common.auth import auth_headers
    from common.hostnet import hub_base_url
    seconds = max(0.0, min(float(timeout), HUB_WAIT_MAX))
    try:
        resp = remote_state._http_session().get(
            f"{hub_base_url()}/api/run-state/wake/{instance_id}", params={"timeout": seconds},
            headers=auth_headers(), timeout=seconds + 15)
    except Exception:  # noqa: BLE001 - the hub is away: wait out the time, then look again
        log.debug("wake: the hub could not be asked for %s", instance_id, exc_info=True)
        time.sleep(min(seconds, poll_seconds() * 4))
        return False
    if resp.status_code == 401:
        raise remote_state.TokenRefused("the hub no longer accepts this carrier's token")
    try:
        return bool(resp.json().get("pending"))
    except ValueError:
        return False


async def wait_async(instance_id: str, timeout: float) -> bool:
    """The hub's side of :func:`_wait_on_hub`: wait without holding a thread
    (the Redis signal when there is a broker, else a light poll)."""
    import asyncio
    deadline = time.monotonic() + max(0.0, min(float(timeout), HUB_WAIT_MAX))
    if await asyncio.to_thread(_pending, instance_id):
        return True
    url = _broker_url()
    if url:
        try:
            import redis.asyncio as aredis
            client = aredis.from_url(url)
            try:
                remaining = max(1, int(round(deadline - time.monotonic())))
                got = await client.blpop([WAKE_PREFIX + str(instance_id)], timeout=remaining)
                if got is not None:
                    await client.delete(WAKE_PREFIX + str(instance_id))
                    return True
            finally:
                await client.aclose()
            return await asyncio.to_thread(_pending, instance_id)
        except Exception:  # noqa: BLE001 - fall back to polling for this wait
            log.debug("wake: async blpop failed for %s", instance_id, exc_info=True)
    interval = poll_seconds()
    while time.monotonic() < deadline:
        await asyncio.sleep(min(interval, max(0.0, deadline - time.monotonic())))
        if await asyncio.to_thread(_pending, instance_id):
            return True
    return False
