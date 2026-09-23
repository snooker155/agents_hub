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
       async for event in broker.client_events(client_id):
           yield f"data: {json.dumps(event)}\\n\\n"
       # broker.add_channel(client_id, "logs:abc") / remove_channel(...)

Every delivered event carries a ``"channel"`` field so the client can route it.

Publish
-------
In-process async:        await broker.apublish(channel, {...})
From a sync thread:      broker.publish_threadsafe(channel, {...})
Resource invalidation:   notify_change("tasks", task_id=...)
Subprocess → HTTP POST → /api/sessions/{id}/events → apublish().

This hub is single-process: an event published here only ever reaches
subscribers connected to this same replica. Running more than one backend
replica (docker compose --scale backend=N) needs the events fanned out across
replicas too; see common/broker_bridge.py and docs/scaling.md for the optional
Redis-backed bridge that does this, off by default.

Per-client backpressure
------------------------
A browser tab that stops reading (a backgrounded tab, a stalled network) must
never make the backend hold events for it forever. Each multiplexed client's
queue is bounded (``AGENTS_HUB_SSE_QUEUE_MAX``, default 1000); once full, the
*oldest* queued event is dropped to make room for the newest one, and the drop
is counted. The next event actually delivered is preceded by one
``{"channel": "_meta", "type": "lagged", "dropped": n}`` frame, so the client
knows it missed something and can refetch instead of trusting a stale view.

Consecutive ``token`` events for the same channel (the high-volume per-session
chat stream) are merged into the copy already sitting in the queue once that
queue is over half full, the same coalescing idea ``_relay_notify`` uses for a
burst of subprocess events — a slow client gets fewer, larger frames instead of
falling further behind. None of this ever blocks the publisher: dropping,
merging and enqueueing are all non-blocking.

Every event delivered to a client is numbered (its ``"id"`` field), and the
last 500 delivered events are kept for 60 seconds after that client's
connection drops. A browser reconnecting with the same client id and the
``Last-Event-ID`` it already has (both handled automatically by
``EventSource``) replays exactly what it missed instead of losing it or
re-fetching everything.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Deque, Dict, List, Optional, Set, Tuple

#: Default per-client queue depth; override with AGENTS_HUB_SSE_QUEUE_MAX.
DEFAULT_SSE_QUEUE_MAX = 1000
#: How many delivered events a client's replay buffer keeps.
RING_BUFFER_MAX = 500
#: How long a disconnected client stays resumable before it is forgotten.
RING_BUFFER_TTL = 60.0  # seconds


def _queue_maxsize() -> int:
    """Read the per-client queue limit fresh each time a client is opened, so a
    test (or an operator) changing the env var takes effect for new clients
    without a restart."""
    raw = os.environ.get("AGENTS_HUB_SSE_QUEUE_MAX", "").strip()
    if not raw:
        return DEFAULT_SSE_QUEUE_MAX
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_SSE_QUEUE_MAX


def _stream_id_tuple(value: Any) -> Optional[Tuple[int, int]]:
    """Parse a Redis stream id ("<ms>-<seq>") into a comparable tuple, or
    None if ``value`` is not shaped like one (the plain per-client integers
    used when the cross-replica bridge is off)."""
    if not isinstance(value, str) or "-" not in value:
        return None
    ms, _, seq = value.partition("-")
    if not (ms.isdigit() and seq.isdigit()):
        return None
    return (int(ms), int(seq))


def _id_greater(a: Any, b: Any) -> bool:
    """True if event id ``a`` is after event id ``b``. Both are either plain
    ints (bridge off) or Redis stream id strings (bridge on): a stream id
    must be compared numerically, not lexicographically ("12-10" sorts
    before "12-9" as plain strings but is the later entry), which is the
    only reason this is not just ``a > b``. Mixed types (a client whose very
    first delivered event happened to be a synthetic frame, before the
    bridge ever assigned it a real id) fall back to plain comparison, which
    itself falls back to False rather than raising if the types disagree."""
    ta, tb = _stream_id_tuple(a), _stream_id_tuple(b)
    if ta is not None and tb is not None:
        return ta > tb
    try:
        return a > b
    except TypeError:
        return False


@dataclass
class _ClientState:
    """Everything the broker keeps for one multiplexed (or legacy single-
    channel) subscriber: its queue, the channels it is fanned into, and the
    bookkeeping behind the queue limit, coalescing and replay."""

    queue: asyncio.Queue
    maxsize: int
    channels: Set[str] = field(default_factory=set)
    next_id: int = 1
    # The most recently assigned id (int normally, a Redis stream id string
    # once the cross-replica bridge is on) — see _enqueue and _deliver's
    # handling of the synthetic "lagged" frame.
    last_id: Optional[Any] = None
    ring: Deque[dict] = field(default_factory=lambda: deque(maxlen=RING_BUFFER_MAX))
    # channel → the token event currently sitting in the queue for it, eligible
    # to absorb the next token instead of getting a frame of its own.
    pending_token: Dict[str, dict] = field(default_factory=dict)
    # Events dropped since the last one actually delivered; flushed as a single
    # `lagged` meta frame ahead of the next delivery.
    dropped: int = 0
    # Set when the browser goes away; cleared on a resumed reconnect. A state
    # past RING_BUFFER_TTL past this mark is no longer resumable.
    disconnected_at: Optional[float] = None


class SessionBroker:
    """Fan-out pub/sub broker keyed by channel, with multiplexed clients."""

    HEARTBEAT_INTERVAL = 20.0  # seconds between keep-alive pings

    def __init__(self) -> None:
        # channel → list of subscriber states
        self._queues: Dict[str, List[_ClientState]] = defaultdict(list)
        # client_id → state, for multiplexed connections
        self._clients: Dict[str, _ClientState] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Cross-replica id hook (see common/broker_bridge.py): when set, a
        # genuinely local publish (skip_sinks=False, no explicit event_id) is
        # awaited through this before being delivered, and the id it returns
        # becomes the event's "id" for every subscriber on this replica,
        # instead of each client's own counter below. It also doubles as the
        # outbound side of the bridge: the hook's job is to publish the event
        # onto the shared Redis stream and hand back the id Redis assigned
        # it, so this is the only place that needs to know the bridge
        # exists. None (the default) leaves this a single process's
        # in-memory hub, ids and all, exactly as before the bridge existed.
        self._id_provider: Optional[Callable[[str, dict], Any]] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once at FastAPI startup to store the running event loop."""
        self._loop = loop

    def set_id_provider(self, provider: Optional[Callable[[str, dict], Any]]) -> None:
        """Register (``provider``) or clear (``None``) the cross-replica id
        hook described above. ``provider(channel, tagged_event)`` must be an
        awaitable returning the id to use, or None to fall back to this
        client's own counter for that one event."""
        self._id_provider = provider

    # ── publish ──────────────────────────────────────────────────────────────

    def publish_threadsafe(self, channel: str, event: dict, *, skip_sinks: bool = False) -> None:
        """Publish from a sync thread (e.g. a LangChain callback in a thread pool).

        Safe to call from any thread; uses call_soon_threadsafe to hand off
        to the event loop without blocking. ``skip_sinks`` is set by
        common/broker_bridge.py when re-injecting an event another replica
        already fanned out, so it is delivered here but not published there
        again.

        When a cross-replica id provider is registered (the bridge is on)
        and this is a genuinely local publish, the whole publish is instead
        handed to the event loop as one ``apublish`` coroutine:  assigning a
        global id needs an awaited Redis round trip, and this calling thread
        must not block on that, but delivery still has to happen after the
        id comes back, not before — so it cannot stay the two independent
        call_soon_threadsafe hops below, one for the (now nonexistent) sink
        notification and one for delivery.
        """
        loop = self._loop
        if not loop or not loop.is_running():
            return
        if not skip_sinks and self._id_provider is not None:
            asyncio.run_coroutine_threadsafe(
                self.apublish(channel, event, skip_sinks=skip_sinks), loop
            )
            return
        tagged = {**event, "channel": channel}
        for state in list(self._queues.get(channel, [])):
            loop.call_soon_threadsafe(self._deliver, state, tagged)

    async def apublish(self, channel: str, event: dict, *, skip_sinks: bool = False,
                        event_id: Optional[Any] = None) -> None:
        """Publish from an async context. ``skip_sinks`` is set by
        common/broker_bridge.py when re-injecting an event another replica
        already fanned out, so it is delivered here but not published there
        again. ``event_id`` lets a caller (again, only the bridge) hand this
        event the id it already has — an event read back off the Redis
        stream — instead of asking the id provider for a fresh one.
        """
        tagged = {**event, "channel": channel}
        if event_id is None and not skip_sinks and self._id_provider is not None:
            try:
                event_id = await self._id_provider(channel, tagged)
            except Exception:
                # The id provider's own failure (Redis briefly down, a
                # timeout) must never break delivery to this replica's own
                # clients — it just falls back to this client's own counter.
                event_id = None
        for state in list(self._queues.get(channel, [])):
            self._deliver(state, tagged, event_id=event_id)

    def has_subscribers(self, channel: str) -> bool:
        """True if at least one connection is listening on ``channel``.

        Lets the periodic external-state publisher skip idle channels.
        """
        return bool(self._queues.get(channel))

    def active_channels(self, prefix: str = "") -> List[str]:
        """Currently-subscribed channels, optionally filtered by ``prefix``."""
        return [ch for ch, states in self._queues.items() if states and ch.startswith(prefix)]

    # ── delivery: bounded, non-blocking, never lost silently ────────────────────

    def _make_room(self, state: _ClientState) -> None:
        """Drop the oldest queued event so the newest one always has a slot,
        instead of blocking the publisher or discarding what just arrived."""
        if not state.queue.full():
            return
        try:
            dropped_entry = state.queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        state.dropped += 1
        channel = dropped_entry.get("channel")
        if state.pending_token.get(channel) is dropped_entry:
            state.pending_token.pop(channel, None)

    def _enqueue(self, state: _ClientState, entry: dict, *, event_id: Optional[Any] = None) -> None:
        """Number, buffer and queue one event for one client. Caller must have
        made room for it first. ``entry`` must already be a per-client copy —
        callers must not pass a dict shared with another client's delivery.

        ``event_id``, when given (the cross-replica bridge is on and this
        event has a stream id), is used as-is instead of this client's own
        counter, so the id means the same thing on every replica. Whichever
        kind of id this entry gets, it is remembered as ``state.last_id`` so
        a synthetic frame this client generates for itself (the ``lagged``
        meta event just below) can reuse it rather than mixing id spaces
        within one client's ring buffer.
        """
        entry["id"] = state.next_id if event_id is None else event_id
        if event_id is None:
            state.next_id += 1
        state.last_id = entry["id"]
        state.ring.append(entry)
        channel = entry.get("channel")
        if entry.get("type") == "token":
            state.pending_token[channel] = entry
        else:
            state.pending_token.pop(channel, None)
        state.queue.put_nowait(entry)

    def _deliver(self, state: _ClientState, source_event: dict, *, event_id: Optional[Any] = None) -> None:
        """Hand one published event to one subscriber. Runs on the event loop
        thread (directly from ``apublish``, or scheduled via
        ``call_soon_threadsafe``/``run_coroutine_threadsafe`` from
        ``publish_threadsafe``), so the coalescing and drop bookkeeping below
        never races a concurrent call for the same client."""
        channel = source_event.get("channel")
        if source_event.get("type") == "token" and state.queue.qsize() * 2 >= state.maxsize:
            pending = state.pending_token.get(channel)
            if pending is not None:
                # Merge into the copy still waiting in the queue rather than
                # adding a second frame — the client sees one bigger token
                # event instead of falling further behind. The merged copy
                # keeps the id it already had.
                pending["token"] = str(pending.get("token", "")) + str(source_event.get("token", ""))
                return
        if state.dropped:
            # Make room for the lag frame and finalize its count in the same
            # step: a drop caused by fitting the lag frame itself belongs in
            # this report, not silently left for a later one. This frame is
            # this client's own bookkeeping, not something the bridge ever
            # saw, so it has no stream id of its own. With the bridge on
            # (state.last_id is a stream-id string) it reuses that last real
            # id rather than mixing id spaces — a plain incrementing integer
            # next to Redis stream ids would break the numeric ordering
            # resume_client and replay both rely on. With the bridge off
            # (state.last_id is a plain int, or nothing has been delivered
            # yet) this is None and the frame gets its own next_id exactly
            # as it always has — the per-client counter is unaffected.
            self._make_room(state)
            dropped_count, state.dropped = state.dropped, 0
            lagged_id = state.last_id if isinstance(state.last_id, str) else None
            self._enqueue(state, {"channel": "_meta", "type": "lagged", "dropped": dropped_count},
                          event_id=lagged_id)
        # Any drop caused by fitting the event itself is new and is reported
        # ahead of the next delivery instead.
        self._make_room(state)
        self._enqueue(state, dict(source_event), event_id=event_id)

    # ── single-channel subscribe (legacy) ──────────────────────────────────────

    async def subscribe(self, channel: str) -> AsyncGenerator[dict, None]:
        """Async generator that yields events for a single ``channel``.

        Sends a heartbeat every HEARTBEAT_INTERVAL seconds to keep the
        HTTP connection alive through proxies/load-balancers.

        Exits when:
        - A ``{"type": "session_done"}`` event is received.
        - The client disconnects (GeneratorExit from the caller).
        """
        maxsize = _queue_maxsize()
        state = _ClientState(queue=asyncio.Queue(maxsize=maxsize), maxsize=maxsize)
        self._queues[channel].append(state)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(state.queue.get(), timeout=self.HEARTBEAT_INTERVAL)
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
            self._detach(channel, state)

    # ── multiplexed client ──────────────────────────────────────────────────────

    def open_client(self, channels: Optional[List[str]] = None) -> Tuple[str, asyncio.Queue]:
        """Register a single queue under multiple channels. Returns (client_id, queue)."""
        self._sweep_expired()
        client_id = uuid.uuid4().hex
        maxsize = _queue_maxsize()
        state = _ClientState(queue=asyncio.Queue(maxsize=maxsize), maxsize=maxsize)
        self._clients[client_id] = state
        for ch in channels or []:
            self.add_channel(client_id, ch)
        return client_id, state.queue

    def resume_client(self, client_id: str, last_event_id: Optional[Any]) -> Optional[List[dict]]:
        """Reattach to a client that may have briefly disconnected, replaying
        what it missed.

        Returns the buffered events with an id greater than ``last_event_id``
        (or ``[]`` when ``last_event_id`` is ``None``, meaning the browser has
        not yet seen any numbered event) when ``client_id`` is still known and
        within its replay window. Returns ``None`` when it is unknown or has
        aged out — the caller must then open a fresh client, and the browser's
        own view is stale and needs a refetch.

        ``last_event_id`` is a plain int with the bridge off, or a Redis
        stream id string ("1695400000000-0") with it on — see
        ``_id_greater``, which compares either kind correctly.
        """
        state = self._clients.get(client_id)
        if not state:
            return None
        if state.disconnected_at is not None and time.time() - state.disconnected_at > RING_BUFFER_TTL:
            self.close_client(client_id)
            return None
        state.disconnected_at = None
        replay = [] if last_event_id is None else [
            e for e in state.ring if _id_greater(e.get("id", 0), last_event_id)
        ]
        # The live queue holds the same events the ring buffer is about to
        # replay explicitly. Drain it (and forget any in-flight coalescing
        # target) so the resumed stream does not repeat them.
        while not state.queue.empty():
            try:
                state.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        state.pending_token.clear()
        return replay

    def add_channel(self, client_id: str, channel: str) -> bool:
        state = self._clients.get(client_id)
        if not state:
            return False
        if channel not in state.channels:
            state.channels.add(channel)
            self._queues[channel].append(state)
        return True

    def remove_channel(self, client_id: str, channel: str) -> bool:
        state = self._clients.get(client_id)
        if not state:
            return False
        if channel in state.channels:
            state.channels.discard(channel)
            self._detach(channel, state)
        return True

    def close_client(self, client_id: str) -> None:
        state = self._clients.pop(client_id, None)
        if not state:
            return
        for ch in list(state.channels):
            self._detach(ch, state)

    async def client_events(self, client_id: str) -> AsyncGenerator[dict, None]:
        """Yield events for a multiplexed client until it is closed/disconnected."""
        state = self._clients.get(client_id)
        if not state:
            return
        try:
            while True:
                try:
                    event = await asyncio.wait_for(state.queue.get(), timeout=self.HEARTBEAT_INTERVAL)
                except asyncio.TimeoutError:
                    yield {"type": "heartbeat", "channel": "_meta"}
                    continue
                if event is None:
                    break
                # This event has left the queue: forget it as a coalescing
                # target now, before any later await gives another callback a
                # chance to run. A merge started after this point must create
                # a fresh event rather than mutate one already handed out.
                channel = event.get("channel")
                if state.pending_token.get(channel) is event:
                    state.pending_token.pop(channel, None)
                yield event
        except GeneratorExit:
            pass
        finally:
            self._schedule_disconnect(client_id)

    # ── close ───────────────────────────────────────────────────────────────────

    async def close_session(self, channel: str) -> None:
        """Send a sentinel to all single-channel subscribers so they exit cleanly."""
        for state in list(self._queues.get(channel, [])):
            await state.queue.put(None)

    # ── internal ─────────────────────────────────────────────────────────────────

    def _detach(self, channel: str, state: _ClientState) -> None:
        try:
            self._queues[channel].remove(state)
        except (ValueError, KeyError):
            pass
        if channel in self._queues and not self._queues[channel]:
            del self._queues[channel]

    def _schedule_disconnect(self, client_id: str) -> None:
        """Mark a client as disconnected rather than tearing it down: it stays
        subscribed (still queueing, still bounded) for RING_BUFFER_TTL seconds
        so a quick reconnect resumes it. A running loop also gets a one-shot
        timer to actually free it if nobody reconnects; without one (e.g. a
        generator driven directly in a test) it is swept lazily, the next time
        ``open_client`` or ``resume_client`` runs."""
        state = self._clients.get(client_id)
        if not state:
            return
        marked_at = time.time()
        state.disconnected_at = marked_at
        loop = self._loop
        if loop and loop.is_running():
            loop.call_later(RING_BUFFER_TTL, self._expire_if_unclaimed, client_id, marked_at)

    def _expire_if_unclaimed(self, client_id: str, marked_at: float) -> None:
        state = self._clients.get(client_id)
        if state and state.disconnected_at == marked_at:
            self.close_client(client_id)

    def _sweep_expired(self) -> None:
        """Best-effort cleanup for clients nobody ever reconnected to. Called
        opportunistically when a new connection comes in, so memory does not
        grow unbounded even without a running-loop timer."""
        now = time.time()
        stale = [cid for cid, state in self._clients.items()
                 if state.disconnected_at is not None and now - state.disconnected_at > RING_BUFFER_TTL]
        for cid in stale:
            self.close_client(cid)


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
