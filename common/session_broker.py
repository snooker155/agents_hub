"""
In-process async pub/sub broker for SSE streaming.

Originally per-session; now a general channel-multiplexed hub. Any code path
(in-process agent run or via HTTP from a subprocess) can publish events to a
channel. Subscribers receive them in real time.

Channels
--------
- ``"app"``                 global app events: notifications, ``*.changed``
                            resource invalidations, periodic external snapshots.
- ``"session:<id>"`` / ``<session_id>``   per-session chat token/tool/usage events.
- ``"logs:<resource>"``     on-demand log tails for an open detail page.

Two subscriber styles
----------------------
1. **Single-channel generator** (legacy) — one connection, one channel::

       async for event in broker.subscribe(session_id):
           yield f"data: {json.dumps(event)}\\n\\n"

2. **Multiplexed client** — one connection, many channels, dynamic interest::

       client_id, _ = broker.open_client(["app", f"session:{sid}"])
       async for event in broker.client_events(client_id, request):
           yield f"data: {json.dumps(event)}\\n\\n"
       # broker.add_channel(client_id, "logs:abc") / remove_channel(...)

Every delivered event carries a ``"channel"`` field so the client can route it.

Publish
-------
In-process async:        await broker.apublish(channel, {...})
From a sync thread:      broker.publish_threadsafe(channel, {...})
Resource invalidation:   notify_change("tasks", task_id=...)
Subprocess → HTTP POST → /api/sessions/{id}/events → apublish().
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from collections import defaultdict
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, Tuple


class SessionBroker:
    """Fan-out pub/sub broker keyed by channel, with multiplexed clients."""

    HEARTBEAT_INTERVAL = 20.0  # seconds between keep-alive pings

    def __init__(self) -> None:
        # channel → list of subscriber queues
        self._queues: Dict[str, List[asyncio.Queue]] = defaultdict(list)
        # client_id → (queue, set of channels) for multiplexed connections
        self._clients: Dict[str, Tuple[asyncio.Queue, Set[str]]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once at FastAPI startup to store the running event loop."""
        self._loop = loop

    # ── publish ──────────────────────────────────────────────────────────────

    def publish_threadsafe(self, channel: str, event: dict) -> None:
        """Publish from a sync thread (e.g. a LangChain callback in a thread pool).

        Safe to call from any thread; uses call_soon_threadsafe to hand off
        to the event loop without blocking.
        """
        loop = self._loop
        if not loop or not loop.is_running():
            return
        tagged = {**event, "channel": channel}
        for q in list(self._queues.get(channel, [])):
            loop.call_soon_threadsafe(q.put_nowait, tagged)

    async def apublish(self, channel: str, event: dict) -> None:
        """Publish from an async context."""
        tagged = {**event, "channel": channel}
        for q in list(self._queues.get(channel, [])):
            await q.put(tagged)

    def has_subscribers(self, channel: str) -> bool:
        """True if at least one connection is listening on ``channel``.

        Lets the periodic external-state publisher skip idle channels.
        """
        return bool(self._queues.get(channel))

    def active_channels(self, prefix: str = "") -> List[str]:
        """Currently-subscribed channels, optionally filtered by ``prefix``."""
        return [ch for ch, qs in self._queues.items() if qs and ch.startswith(prefix)]

    # ── single-channel subscribe (legacy) ──────────────────────────────────────

    async def subscribe(self, channel: str) -> AsyncGenerator[dict, None]:
        """Async generator that yields events for a single ``channel``.

        Sends a heartbeat every HEARTBEAT_INTERVAL seconds to keep the
        HTTP connection alive through proxies/load-balancers.

        Exits when:
        - A ``{"type": "session_done"}`` event is received.
        - The client disconnects (GeneratorExit from the caller).
        """
        queue: asyncio.Queue = asyncio.Queue()
        self._queues[channel].append(queue)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=self.HEARTBEAT_INTERVAL)
                except asyncio.TimeoutError:
                    yield {"type": "heartbeat", "channel": channel}
                    continue

                if event is None:
                    # Sentinel — publisher signalled end of session
                    break
                yield event
                if event.get("type") == "session_done":
                    break
        except GeneratorExit:
            pass
        finally:
            self._detach(channel, queue)

    # ── multiplexed client ──────────────────────────────────────────────────────

    def open_client(self, channels: Optional[List[str]] = None) -> Tuple[str, asyncio.Queue]:
        """Register a single queue under multiple channels. Returns (client_id, queue)."""
        client_id = uuid.uuid4().hex
        queue: asyncio.Queue = asyncio.Queue()
        chans: Set[str] = set()
        self._clients[client_id] = (queue, chans)
        for ch in channels or []:
            self.add_channel(client_id, ch)
        return client_id, queue

    def add_channel(self, client_id: str, channel: str) -> bool:
        entry = self._clients.get(client_id)
        if not entry:
            return False
        queue, chans = entry
        if channel not in chans:
            chans.add(channel)
            self._queues[channel].append(queue)
        return True

    def remove_channel(self, client_id: str, channel: str) -> bool:
        entry = self._clients.get(client_id)
        if not entry:
            return False
        queue, chans = entry
        if channel in chans:
            chans.discard(channel)
            self._detach(channel, queue)
        return True

    def close_client(self, client_id: str) -> None:
        entry = self._clients.pop(client_id, None)
        if not entry:
            return
        queue, chans = entry
        for ch in list(chans):
            self._detach(ch, queue)

    async def client_events(self, client_id: str) -> AsyncGenerator[dict, None]:
        """Yield events for a multiplexed client until it is closed/disconnected."""
        entry = self._clients.get(client_id)
        if not entry:
            return
        queue, _ = entry
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=self.HEARTBEAT_INTERVAL)
                except asyncio.TimeoutError:
                    yield {"type": "heartbeat", "channel": "_meta"}
                    continue
                if event is None:
                    break
                yield event
        except GeneratorExit:
            pass
        finally:
            self.close_client(client_id)

    # ── close ───────────────────────────────────────────────────────────────────

    async def close_session(self, channel: str) -> None:
        """Send a sentinel to all single-channel subscribers so they exit cleanly."""
        for q in list(self._queues.get(channel, [])):
            await q.put(None)

    # ── internal ─────────────────────────────────────────────────────────────────

    def _detach(self, channel: str, queue: asyncio.Queue) -> None:
        try:
            self._queues[channel].remove(queue)
        except (ValueError, KeyError):
            pass
        if channel in self._queues and not self._queues[channel]:
            del self._queues[channel]


# Module-level singleton — imported everywhere.
broker = SessionBroker()

# Global app channel: notifications, resource-change invalidations, snapshots.
APP_CHANNEL = "app"


# Pending relay events, per debounce key: the timer that will send it, and the
# callable that does the sending. The callable is kept alongside so a flush can
# fire it directly rather than reaching into the Timer for its function.
_relay_timers: Dict[str, Tuple[threading.Timer, Any]] = {}
_relay_lock = threading.Lock()
_RELAY_DEBOUNCE = 0.25  # seconds — coalesce bursts of subprocess events


def _relay_notify(resource: str, meta: dict, *, delta: bool = False,
                  debounce_key: Optional[str] = None) -> None:
    """Forward a change to the backend over HTTP (used from agent/flow subprocesses
    where this process has no event loop). Trailing-debounced per key so a
    burst of log events collapses to a single POST while still delivering the last.

    ``debounce_key`` defaults to the resource. Delta publishers pass a key that
    includes the row id, so two instances updating at once never cancel each
    other's event — only repeated updates *of the same row* collapse.
    """
    key = debounce_key or resource

    def _send() -> None:
        with _relay_lock:
            _relay_timers.pop(key, None)
        try:
            import requests
            from common.auth import auth_headers
            port = os.environ.get("DASHBOARD_PORT", "8000")
            requests.post(
                f"http://localhost:{port}/api/stream/notify",
                json={"resource": resource, "meta": meta, "delta": delta},
                headers=auth_headers(),
                timeout=1.0,
            )
        except Exception:
            pass

    with _relay_lock:
        existing = _relay_timers.get(key)
        if existing:
            existing[0].cancel()
        timer = threading.Timer(_RELAY_DEBOUNCE, _send)
        timer.daemon = True
        _relay_timers[key] = (timer, _send)
        timer.start()


def flush_relayed_notifications(timeout: float = 2.0) -> int:
    """Send the debounced relay events now instead of waiting out the window.

    The debounce assumes a long-lived process. An agent subprocess runs for
    minutes, so its timers fire; a process that does one thing and exits — the
    CLI running a single command — is gone well inside the 250 ms window, and
    because the timers are daemon threads they die with it. The change is still
    on disk, but nothing told the dashboard, so an open tab would not see it
    until its next fetch.

    Call this before such a process exits. Returns how many events were sent.
    ``timeout`` bounds the whole flush: each POST already caps itself at a
    second, and a command must not hang on the way out because a backend went
    unreachable mid-flush.
    """
    deadline = time.monotonic() + max(timeout, 0.0)
    # Take the pending set and clear it under the lock, then send outside it:
    # _send acquires the same non-reentrant lock itself.
    with _relay_lock:
        pending = list(_relay_timers.values())
        _relay_timers.clear()

    sent = 0
    for timer, send in pending:
        timer.cancel()
        if time.monotonic() >= deadline:
            break
        send()
        sent += 1
    return sent


def notify_change(resource: str, **meta) -> None:
    """Publish a coarse ``<resource>.changed`` invalidation on the app channel.

    Frontend listeners refetch the affected list/detail. In the backend process
    we publish directly; in agent/flow subprocesses (no event loop) we relay the
    event to the backend over HTTP so live updates still reach the UI.

    Prefer :func:`notify_delta` for anything that can be in flight by the
    hundreds — a coarse invalidation makes every open tab refetch a whole list.
    """
    loop = broker._loop
    if loop is not None and loop.is_running():
        broker.publish_threadsafe(APP_CHANNEL, {"type": f"{resource}.changed", **meta})
    else:
        _relay_notify(resource, meta)


# ── Row deltas ───────────────────────────────────────────────────────────────
# A coarse ``<resource>.changed`` costs every open tab a full list refetch. That
# is fine for a handful of rows and ruinous for a thousand live instances, so
# high-cardinality resources publish *deltas*: the changed fields of one row.
# Deltas are merged per row and flushed as ONE ``<resource>.delta`` event
# carrying an array, so a burst of 1000 updates becomes one frame, not 1000.

_DELTA_WINDOW = 0.25  # seconds
_delta_lock = threading.Lock()
_delta_buffer: Dict[str, Dict[str, dict]] = defaultdict(dict)
_delta_pending: Set[str] = set()


def _flush_deltas(resource: str) -> None:
    with _delta_lock:
        _delta_pending.discard(resource)
        items = list(_delta_buffer.pop(resource, {}).values())
    if items:
        broker.publish_threadsafe(APP_CHANNEL, {"type": f"{resource}.delta", "items": items})


def notify_delta(resource: str, key: str, item: dict) -> None:
    """Publish a merged per-row delta for ``resource`` on the app channel.

    ``key`` identifies the row (its id); repeated updates to the same row inside
    the flush window collapse into their merged latest state. The frontend
    patches the row in place instead of refetching.
    """
    payload = {**item}
    loop = broker._loop
    if loop is None or not loop.is_running():
        _relay_notify(resource, payload, delta=True,
                      debounce_key=f"{resource}:{key}")
        return
    with _delta_lock:
        bucket = _delta_buffer[resource]
        bucket[key] = {**bucket.get(key, {}), **payload}
        schedule = resource not in _delta_pending
        if schedule:
            _delta_pending.add(resource)
    if schedule:
        loop.call_soon_threadsafe(loop.call_later, _DELTA_WINDOW, _flush_deltas, resource)
