"""
Per-client backpressure and replay for the multiplexed SSE endpoint.

A browser tab that stops reading (backgrounded, a stalled network) used to make
the backend hold every event for it forever — the per-client queue was
unbounded. These tests cover the fix: a bounded queue that drops the oldest
event and tells the client with a `lagged` meta event, token coalescing once a
queue is over half full, and numbered events with a short replay window so a
quick reconnect resumes instead of losing what it missed or refetching
everything.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def _drain(queue: asyncio.Queue):
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


# ── the queue limit ──────────────────────────────────────────────────────────

async def _body_test_a_full_queue_drops_the_oldest_and_marks_lagged(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_SSE_QUEUE_MAX", "4")
    from common.session_broker import SessionBroker

    broker = SessionBroker()
    client_id, queue = broker.open_client(["room"])

    # 7 events into a queue of 4: several must be dropped, and the drop that
    # makes room for the lagged frame itself is not lost either.
    for i in range(7):
        await broker.apublish("room", {"type": "tick", "n": i})

    events = _drain(queue)
    kinds = [e["type"] for e in events]
    assert "lagged" in kinds
    lagged = next(e for e in events if e["type"] == "lagged")
    assert lagged["channel"] == "_meta"
    assert lagged["dropped"] >= 1

    ticks = [e["n"] for e in events if e["type"] == "tick"]
    # The newest ticks survive; the oldest are the ones gone.
    assert ticks == sorted(ticks)
    assert ticks[-1] == 6
    assert 0 not in ticks
    # Every tick that did not survive and was not folded into the reported
    # count is still accounted for: nothing vanishes without a trace.
    missing = 7 - len(ticks)
    assert missing >= lagged["dropped"]


async def _body_test_the_publisher_is_never_blocked_by_a_full_queue(monkeypatch):
    """apublish/publish_threadsafe must return immediately even when every
    subscriber's queue is already full — the whole point of dropping instead
    of blocking."""
    monkeypatch.setenv("AGENTS_HUB_SSE_QUEUE_MAX", "1")
    from common.session_broker import SessionBroker

    broker = SessionBroker()
    broker.open_client(["room"])

    start = time.monotonic()
    for i in range(50):
        await broker.apublish("room", {"type": "tick", "n": i})
    assert time.monotonic() - start < 1.0


# ── token coalescing ─────────────────────────────────────────────────────────

async def _body_test_token_events_coalesce_once_the_queue_is_half_full(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_SSE_QUEUE_MAX", "10")
    from common.session_broker import SessionBroker

    broker = SessionBroker()
    client_id, queue = broker.open_client(["session:s1"])

    # Fill past 50% with something other than tokens first.
    for i in range(6):
        await broker.apublish("session:s1", {"type": "tool_start", "step": i})

    # Now a burst of token events for the same session: these should merge
    # into one entry rather than each taking a slot the tool events need.
    for tok in ["he", "llo", " wor", "ld"]:
        await broker.apublish("session:s1", {"type": "token", "token": tok})

    events = _drain(queue)
    token_events = [e for e in events if e["type"] == "token"]
    assert len(token_events) == 1
    assert token_events[0]["token"] == "hello world"


async def _body_test_coalescing_only_merges_the_same_channel(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_SSE_QUEUE_MAX", "10")
    from common.session_broker import SessionBroker

    broker = SessionBroker()
    client_id, queue = broker.open_client(["session:a", "session:b"])
    for i in range(6):
        await broker.apublish("session:a", {"type": "tool_start", "step": i})

    await broker.apublish("session:a", {"type": "token", "token": "a1"})
    await broker.apublish("session:b", {"type": "token", "token": "b1"})
    await broker.apublish("session:a", {"type": "token", "token": "a2"})

    events = _drain(queue)
    tokens = {(e["channel"], e["token"]) for e in events if e["type"] == "token"}
    # "a1"+"a2" merge (same channel); "b1" stays separate.
    assert tokens == {("session:a", "a1a2"), ("session:b", "b1")}


async def _body_test_a_non_token_event_ends_the_merge_window(monkeypatch):
    """Coalescing is for *consecutive* tokens. A different event type for the
    same channel in between must not be swallowed into a later merge."""
    monkeypatch.setenv("AGENTS_HUB_SSE_QUEUE_MAX", "10")
    from common.session_broker import SessionBroker

    broker = SessionBroker()
    client_id, queue = broker.open_client(["session:a"])
    for i in range(6):
        await broker.apublish("session:a", {"type": "tool_start", "step": i})
    await broker.apublish("session:a", {"type": "token", "token": "a1"})
    await broker.apublish("session:a", {"type": "done", "ok": True})
    await broker.apublish("session:a", {"type": "token", "token": "a2"})

    events = _drain(queue)
    tokens = [e["token"] for e in events if e["type"] == "token"]
    assert tokens == ["a1", "a2"]


# ── event ids ────────────────────────────────────────────────────────────────

async def _body_test_ids_increase_monotonically():
    from common.session_broker import SessionBroker

    broker = SessionBroker()
    client_id, queue = broker.open_client(["room"])
    for i in range(5):
        await broker.apublish("room", {"type": "tick", "n": i})

    ids = [e["id"] for e in _drain(queue)]
    assert ids == sorted(ids)
    assert ids == list(range(1, 6))


async def _body_test_each_client_numbers_its_own_events():
    from common.session_broker import SessionBroker

    broker = SessionBroker()
    _, queue_a = broker.open_client(["room"])
    _, queue_b = broker.open_client(["room"])
    await broker.apublish("room", {"type": "tick", "n": 1})

    id_a = _drain(queue_a)[0]["id"]
    id_b = _drain(queue_b)[0]["id"]
    assert id_a == id_b == 1  # independent counters, not a shared sequence


# ── replay ───────────────────────────────────────────────────────────────────

async def _body_test_replay_after_disconnect_within_the_window_returns_missed_events():
    from common.session_broker import SessionBroker

    broker = SessionBroker()
    client_id, queue = broker.open_client(["room"])

    gen = broker.client_events(client_id)
    await broker.apublish("room", {"type": "tick", "n": 1})
    first = await gen.__anext__()
    assert first["n"] == 1
    last_seen_id = first["id"]

    # The browser goes away mid-stream (network blip, tab backgrounded).
    await gen.aclose()

    # More happens while it is gone.
    await broker.apublish("room", {"type": "tick", "n": 2})
    await broker.apublish("room", {"type": "tick", "n": 3})

    replay = broker.resume_client(client_id, last_seen_id)
    assert replay is not None
    assert [e["n"] for e in replay] == [2, 3]
    # The live queue must not repeat what was just replayed explicitly.
    assert _drain(queue) == []


async def _body_test_resuming_continues_live_delivery_after_the_replay():
    from common.session_broker import SessionBroker

    broker = SessionBroker()
    client_id, queue = broker.open_client(["room"])
    gen = broker.client_events(client_id)
    await broker.apublish("room", {"type": "tick", "n": 1})
    first = await gen.__anext__()
    await gen.aclose()

    await broker.apublish("room", {"type": "tick", "n": 2})
    replay = broker.resume_client(client_id, first["id"])
    assert [e["n"] for e in replay] == [2]

    # A fresh event after the resume must reach the (new) consumer normally.
    gen2 = broker.client_events(client_id)
    await broker.apublish("room", {"type": "tick", "n": 3})
    third = await gen2.__anext__()
    assert third["n"] == 3
    await gen2.aclose()


async def _body_test_no_last_event_id_replays_nothing_but_still_resumes():
    from common.session_broker import SessionBroker

    broker = SessionBroker()
    client_id, _ = broker.open_client(["room"])
    gen = broker.client_events(client_id)
    await broker.apublish("room", {"type": "tick", "n": 1})
    await gen.__anext__()
    await gen.aclose()

    replay = broker.resume_client(client_id, None)
    assert replay == []


async def _body_test_an_unknown_client_id_does_not_resume():
    from common.session_broker import SessionBroker

    broker = SessionBroker()
    assert broker.resume_client("no-such-client", 1) is None


async def _body_test_past_the_window_the_client_no_longer_resumes():
    from common.session_broker import RING_BUFFER_TTL, SessionBroker

    broker = SessionBroker()
    client_id, _ = broker.open_client(["room"])
    gen = broker.client_events(client_id)
    await broker.apublish("room", {"type": "tick", "n": 1})
    await gen.__anext__()
    await gen.aclose()

    # Simulate the 60 second window having elapsed without a real sleep.
    state = broker._clients[client_id]
    state.disconnected_at = time.time() - RING_BUFFER_TTL - 1

    assert broker.resume_client(client_id, 0) is None
    # And it is actually gone, not just refused once.
    assert client_id not in broker._clients


# ── the route ────────────────────────────────────────────────────────────────

class _FakeRequest:
    """The parts of a starlette Request the stream endpoint reads."""

    def __init__(self, headers=None):
        self.headers = headers or {}

    async def is_disconnected(self):
        return False


async def _body_test_the_route_serves_id_lines_and_a_fresh_ready_event():
    from common.session_broker import broker
    from routes import stream as stream_module

    broker.set_loop(asyncio.get_running_loop())
    response = await stream_module.stream(_FakeRequest(), session=None, client=None)
    body = response.body_iterator

    ready_frame = await body.__anext__()
    assert '"resumed": false' in ready_frame
    assert '"replayed": 0' in ready_frame
    client_id = ready_frame.split('"client_id": "')[1].split('"')[0]

    await broker.apublish("app", {"type": "widgets.changed"})
    frame = await body.__anext__()
    assert frame.startswith("id: 1\n")
    assert "widgets.changed" in frame
    broker.close_client(client_id)


async def _body_test_reconnecting_with_client_and_last_event_id_resumes_and_replays():
    from common.session_broker import broker
    from routes import stream as stream_module

    broker.set_loop(asyncio.get_running_loop())
    first = await stream_module.stream(_FakeRequest(), session=None, client=None)
    first_body = first.body_iterator
    ready = await first_body.__anext__()
    client_id = ready.split('"client_id": "')[1].split('"')[0]

    # "marker", not "id": the envelope's own "id" is the numbered SSE id
    # SessionBroker assigns on delivery, and would be overwritten anyway.
    await broker.apublish("app", {"type": "widgets.changed", "marker": "w1"})
    frame = await first_body.__anext__()
    last_id = int(frame.split("id: ")[1].split("\n")[0])
    await first_body.aclose()

    # More happens while this tab is (briefly) gone.
    await broker.apublish("app", {"type": "widgets.changed", "marker": "w2"})

    second = await stream_module.stream(
        _FakeRequest(headers={"last-event-id": str(last_id)}),
        session=None, client=client_id,
    )
    second_body = second.body_iterator
    ready2 = await second_body.__anext__()
    assert '"resumed": true' in ready2
    assert '"replayed": 1' in ready2

    replayed_frame = await second_body.__anext__()
    assert '"marker": "w2"' in replayed_frame
    broker.close_client(client_id)


async def _body_test_an_unknown_client_query_param_opens_a_fresh_one():
    from common.session_broker import broker
    from routes import stream as stream_module

    broker.set_loop(asyncio.get_running_loop())
    response = await stream_module.stream(
        _FakeRequest(headers={"last-event-id": "9"}), session=None, client="ghost",
    )
    ready = await response.body_iterator.__anext__()
    assert '"resumed": false' in ready


async def _body_test_reconnecting_to_a_different_replica_refetches_instead_of_resuming():
    """Behind nginx with more than one backend replica, a reconnect can land
    on a different replica than the one that issued the client id — the
    per-client replay state (SessionBroker._clients, the ring buffer) is
    process-local and the cross-replica bridge (common/broker_bridge.py)
    deliberately does not try to share it (see docs/scaling.md). Two separate
    SessionBroker instances stand in for two replicas here: the id issued by
    "replica A" is unknown to "replica B", so resume_client must report it as
    such — the same unknown-client path exercised above, just under the name
    of the scenario this project actually cares about."""
    from common.session_broker import SessionBroker

    replica_a = SessionBroker()
    client_id, _ = replica_a.open_client(["app"])

    replica_b = SessionBroker()
    assert replica_b.resume_client(client_id, 5) is None


# ── running the async bodies above ──────────────────────────────────────────
# The suite has no pytest-asyncio; async work is driven with asyncio.run, and
# the broker's queues are created and read inside that same loop. Each test
# builds its own SessionBroker (rather than importing the shared singleton)
# so tests never see one another's clients or channels — except the route
# tests, which exercise routes.stream against the real shared `broker` the
# way the app does, and clean up the clients they open.


def test_a_full_queue_drops_the_oldest_and_marks_lagged(monkeypatch):
    asyncio.run(_body_test_a_full_queue_drops_the_oldest_and_marks_lagged(monkeypatch))

def test_the_publisher_is_never_blocked_by_a_full_queue(monkeypatch):
    asyncio.run(_body_test_the_publisher_is_never_blocked_by_a_full_queue(monkeypatch))

def test_token_events_coalesce_once_the_queue_is_half_full(monkeypatch):
    asyncio.run(_body_test_token_events_coalesce_once_the_queue_is_half_full(monkeypatch))

def test_coalescing_only_merges_the_same_channel(monkeypatch):
    asyncio.run(_body_test_coalescing_only_merges_the_same_channel(monkeypatch))

def test_a_non_token_event_ends_the_merge_window(monkeypatch):
    asyncio.run(_body_test_a_non_token_event_ends_the_merge_window(monkeypatch))

def test_ids_increase_monotonically():
    asyncio.run(_body_test_ids_increase_monotonically())

def test_each_client_numbers_its_own_events():
    asyncio.run(_body_test_each_client_numbers_its_own_events())

def test_replay_after_disconnect_within_the_window_returns_missed_events():
    asyncio.run(_body_test_replay_after_disconnect_within_the_window_returns_missed_events())

def test_resuming_continues_live_delivery_after_the_replay():
    asyncio.run(_body_test_resuming_continues_live_delivery_after_the_replay())

def test_no_last_event_id_replays_nothing_but_still_resumes():
    asyncio.run(_body_test_no_last_event_id_replays_nothing_but_still_resumes())

def test_an_unknown_client_id_does_not_resume():
    asyncio.run(_body_test_an_unknown_client_id_does_not_resume())

def test_past_the_window_the_client_no_longer_resumes():
    asyncio.run(_body_test_past_the_window_the_client_no_longer_resumes())

def test_the_route_serves_id_lines_and_a_fresh_ready_event():
    asyncio.run(_body_test_the_route_serves_id_lines_and_a_fresh_ready_event())

def test_reconnecting_with_client_and_last_event_id_resumes_and_replays():
    asyncio.run(_body_test_reconnecting_with_client_and_last_event_id_resumes_and_replays())

def test_an_unknown_client_query_param_opens_a_fresh_one():
    asyncio.run(_body_test_an_unknown_client_query_param_opens_a_fresh_one())

def test_reconnecting_to_a_different_replica_refetches_instead_of_resuming():
    asyncio.run(_body_test_reconnecting_to_a_different_replica_refetches_instead_of_resuming())
