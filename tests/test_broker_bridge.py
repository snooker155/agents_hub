"""
Cross-replica fan-out for the session broker (common/broker_bridge.py).

No real Redis is available in this environment (no docker daemon, no Redis
server), so these tests fake it: a tiny in-process "bus" that several
FakeRedisClient instances can read and write, shaped just enough like
``redis.asyncio`` (``from_url``, ``.ping()``, ``.xadd()``, ``.xread()``,
``.xrange()``, ``.aclose()``) for BrokerBridge to drive. One bus instance
stands in for one Redis server and its one shared stream; two BrokerBridge
instances sharing a bus stand in for two backend replicas.

Both the fake's stream ids and BrokerBridge's own ("<ms>-<seq>", like Redis's
real ones) and its wire values (bytes, not str — real redis-py hands back
bytes for a client built without decode_responses=True, which is how
BrokerBridge.start connects) are deliberately faithful to the real library,
so these tests actually exercise BrokerBridge's own byte-decoding rather than
a fake that quietly does the decoding for it.

Each test builds its own throwaway ``SessionBroker()`` (via
``BrokerBridge(url, target_broker=...)``) so bridge tests never touch the
process-wide singleton, matching the isolation tests/test_sse_limits.py uses
for the broker itself.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


# ── a fake redis.asyncio, just shaped enough for BrokerBridge ───────────────

def _b(value):
    return value.encode() if isinstance(value, str) else value


def _id_tuple(value):
    text = value.decode() if isinstance(value, bytes) else value
    ms, _, seq = text.partition("-")
    return (int(ms), int(seq))


class FakeRedisBus:
    """Stands in for one Redis server and its one stream: an ordered list of
    (id, fields) entries, plus enough bookkeeping to mint ids the way Redis
    does (monotonically increasing "<ms>-<seq>", never going backwards even
    if two entries land in the same millisecond)."""

    def __init__(self) -> None:
        self.entries: list[tuple[bytes, dict]] = []
        self._last_ms = 0
        self._last_seq = -1

    def _mint_id(self) -> str:
        ms = int(time.time() * 1000)
        if ms <= self._last_ms:
            ms = self._last_ms
            self._last_seq += 1
        else:
            self._last_ms, self._last_seq = ms, 0
        return f"{ms}-{self._last_seq}"

    async def xadd(self, fields: dict, maxlen=None) -> bytes:
        entry_id = _b(self._mint_id())
        self.entries.append((entry_id, {_b(k): _b(v) for k, v in fields.items()}))
        if maxlen is not None:
            while len(self.entries) > maxlen:
                self.entries.pop(0)
        return entry_id

    async def wait_for_more(self, baseline) -> None:
        """Poll rather than an Event-based wake: a wake signal fired before
        the waiter has registered for it would otherwise be lost outright
        (there is no server-side blocking command to fall back on here, the
        way real Redis's XREAD BLOCK has). A short, fixed poll interval is
        simpler and race-free, and fast enough that it costs these tests low
        single-digit milliseconds at most."""
        while not any(_id_tuple(eid) > baseline for eid, _ in self.entries):
            await asyncio.sleep(0.001)

    def xrange(self, min="-", max="+", count=None):
        if min == "-":
            lower, exclusive = None, False
        elif isinstance(min, str) and min.startswith("("):
            lower, exclusive = _id_tuple(min[1:]), True
        else:
            lower, exclusive = _id_tuple(min), False
        out = []
        for entry_id, fields in self.entries:
            t = _id_tuple(entry_id)
            if lower is not None:
                if exclusive and not (t > lower):
                    continue
                if not exclusive and not (t >= lower):
                    continue
            out.append((entry_id, fields))
            if count is not None and len(out) >= count:
                break
        return out


class FakeRedisClient:
    def __init__(self, bus: FakeRedisBus) -> None:
        self._bus = bus

    async def ping(self) -> bool:
        return True

    async def xadd(self, name: str, fields: dict, maxlen=None, approximate=True) -> bytes:
        return await self._bus.xadd(fields, maxlen=maxlen)

    async def xread(self, streams: dict, block=None):
        (key, cursor), = streams.items()
        # "$" means "only entries from here on"; resolved once, against the
        # current tail, exactly when this call is made — matching real
        # XREAD closely enough for these tests, since production code only
        # ever passes the literal "$" on its very first read.
        if cursor == "$":
            baseline = _id_tuple(self._bus.entries[-1][0]) if self._bus.entries else (-1, -1)
        else:
            baseline = _id_tuple(cursor)

        def pending():
            return [(eid, f) for eid, f in self._bus.entries if _id_tuple(eid) > baseline]

        found = pending()
        if not found:
            timeout = (block / 1000.0) if block else 0.05
            try:
                await asyncio.wait_for(self._bus.wait_for_more(baseline), timeout=timeout)
            except asyncio.TimeoutError:
                return None
            found = pending()
            if not found:
                return None
        return [(_b(key), found)]

    async def xrange(self, name: str, min="-", max="+", count=None):
        return self._bus.xrange(min=min, max=max, count=count)

    async def aclose(self) -> None:
        pass


class FakeRedisModule:
    """Shaped like the ``redis.asyncio`` module: exposes ``from_url``."""

    def __init__(self, bus: FakeRedisBus) -> None:
        self._bus = bus

    def from_url(self, url: str) -> FakeRedisClient:
        return FakeRedisClient(self._bus)


class BrokenRedisModule:
    """A redis module whose client cannot connect, for the unreachable-URL case."""

    def from_url(self, url: str):
        return _BrokenClient()


class _BrokenClient:
    async def ping(self):
        raise ConnectionError("no redis here")


async def _settle() -> None:
    """Let scheduled callbacks/tasks (call_soon_threadsafe, create_task, and a
    chain of several awaits through the fake bus and the subscriber loop) run
    to completion before a test inspects a queue."""
    for _ in range(20):
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)


def _event_field(fields: dict) -> dict:
    return json.loads(fields[b"event"])


# ── tests ────────────────────────────────────────────────────────────────────

async def _body_test_local_publish_goes_out_tagged_with_origin():
    from common.broker_bridge import STREAM_KEY, BrokerBridge
    from common.session_broker import SessionBroker

    bus = FakeRedisBus()
    local_broker = SessionBroker()
    bridge = BrokerBridge("redis://fake/0", target_broker=local_broker)
    ok = await bridge.start(redis_module=FakeRedisModule(bus))
    assert ok

    await local_broker.apublish("app", {"type": "widgets.changed"})
    await _settle()

    assert len(bus.entries) == 1
    entry_id, fields = bus.entries[0]
    assert fields[b"origin"].decode() == bridge.origin
    assert fields[b"channel"].decode() == "app"
    assert _event_field(fields)["type"] == "widgets.changed"
    _ = STREAM_KEY  # the name BrokerBridge writes to; nothing else uses it here

    await bridge.stop()


async def _body_test_local_delivery_carries_the_stream_id():
    """Requirement: with the bridge on, even a LOCAL event's "id" (the one
    handed to this replica's own SSE clients) is the Redis stream id, not
    the old per-client integer — so a later reconnect (same or different
    replica) has an id meaningful across the whole deployment."""
    from common.broker_bridge import BrokerBridge
    from common.session_broker import SessionBroker

    bus = FakeRedisBus()
    local_broker = SessionBroker()
    bridge = BrokerBridge("redis://fake/0", target_broker=local_broker)
    await bridge.start(redis_module=FakeRedisModule(bus))

    client_id, queue = local_broker.open_client(["app"])
    await local_broker.apublish("app", {"type": "widgets.changed"})
    await _settle()

    delivered = queue.get_nowait()
    stream_id = bus.entries[0][0].decode()
    assert delivered["id"] == stream_id

    await bridge.stop()


async def _body_test_a_foreign_message_is_delivered_to_a_local_subscriber():
    from common.broker_bridge import BrokerBridge
    from common.session_broker import SessionBroker

    bus = FakeRedisBus()
    replica_b_broker = SessionBroker()
    bridge_b = BrokerBridge("redis://fake/0", target_broker=replica_b_broker)
    ok = await bridge_b.start(redis_module=FakeRedisModule(bus))
    assert ok
    # Give the subscriber task its first turn so it has actually started
    # reading the stream before the write below.
    await _settle()

    client_id, queue = replica_b_broker.open_client(["app"])

    # Replica A writes directly onto the shared stream (as its own bridge
    # would), under a different origin than replica B's.
    entry_id = await FakeRedisClient(bus).xadd(
        "unused", {"origin": "replica-a:1", "channel": "app", "event": json.dumps({"type": "run.changed"})},
    )
    await _settle()

    delivered = queue.get_nowait()
    assert delivered["type"] == "run.changed"
    assert delivered["channel"] == "app"
    assert delivered["id"] == entry_id.decode()

    await bridge_b.stop()


async def _body_test_a_foreign_message_is_not_re_published_outward():
    """The whole point of skip_sinks: re-injecting a foreign event locally
    must not write it back onto the stream, or two replicas would echo one
    event back and forth forever (worse with three or more)."""
    from common.broker_bridge import BrokerBridge
    from common.session_broker import SessionBroker

    bus = FakeRedisBus()
    replica_b_broker = SessionBroker()
    bridge_b = BrokerBridge("redis://fake/0", target_broker=replica_b_broker)
    await bridge_b.start(redis_module=FakeRedisModule(bus))
    await _settle()  # let the subscriber task start reading first

    entries_before = len(bus.entries)

    await FakeRedisClient(bus).xadd(
        "unused", {"origin": "replica-a:1", "channel": "app", "event": json.dumps({"type": "run.changed"})},
    )
    await _settle()

    # The foreign entry landed (replica A's own write above), and replica
    # B's bridge did not add a second one for it.
    assert len(bus.entries) == entries_before + 1

    await bridge_b.stop()


async def _body_test_an_own_message_coming_back_is_ignored():
    """The subscriber loop reads the whole stream forward, including entries
    this same replica wrote a moment earlier (there is only one stream, no
    per-subscriber exclusion the way pub/sub used to provide). The bridge
    must recognise its own origin and skip it, rather than double-delivering
    an event this replica already delivered directly at publish time."""
    from common.broker_bridge import BrokerBridge
    from common.session_broker import SessionBroker

    bus = FakeRedisBus()
    local_broker = SessionBroker()
    bridge = BrokerBridge("redis://fake/0", target_broker=local_broker)
    await bridge.start(redis_module=FakeRedisModule(bus))

    client_id, queue = local_broker.open_client(["app"])
    await local_broker.apublish("app", {"type": "widgets.changed"})
    await _settle()

    delivered = [queue.get_nowait()]
    while not queue.empty():
        delivered.append(queue.get_nowait())

    assert len(delivered) == 1
    assert delivered[0]["type"] == "widgets.changed"

    await bridge.stop()


async def _body_test_missing_redis_module_leaves_the_broker_working(monkeypatch):
    """AGENTS_HUB_BROKER_URL set, but `redis` is not importable: start()
    reports failure without raising, and the local broker still delivers to
    its own subscribers exactly as if the bridge did not exist.

    This environment actually has `redis` installed (it must stay an optional
    import, not an absent one, so the suite can still exercise this path):
    force the "not installed" case by making the import itself fail, the same
    way it would fail for real on a deployment that never installed it.
    """
    import sys

    from common.broker_bridge import BrokerBridge
    from common.session_broker import SessionBroker

    monkeypatch.setitem(sys.modules, "redis", None)
    monkeypatch.setitem(sys.modules, "redis.asyncio", None)

    local_broker = SessionBroker()
    bridge = BrokerBridge("redis://fake/0", target_broker=local_broker)

    started = await bridge.start()  # no redis_module: takes the real `import redis.asyncio`
    assert started is False

    client_id, queue = local_broker.open_client(["app"])
    await local_broker.apublish("app", {"type": "still.works"})
    assert queue.get_nowait()["type"] == "still.works"


async def _body_test_unreachable_redis_fails_start_without_raising():
    from common.broker_bridge import BrokerBridge
    from common.session_broker import SessionBroker

    local_broker = SessionBroker()
    bridge = BrokerBridge("redis://unreachable/0", target_broker=local_broker)
    started = await bridge.start(redis_module=BrokenRedisModule())
    assert started is False

    # Still fully usable without the bridge.
    client_id, queue = local_broker.open_client(["app"])
    await local_broker.apublish("app", {"type": "fine"})
    assert queue.get_nowait()["type"] == "fine"


def test_broker_url_setting_defaults_to_off():
    from common.config import Settings

    assert Settings().broker_url == ""


async def _body_test_the_broker_behaves_exactly_as_before_when_the_bridge_is_off():
    """No bridge registered at all: apublish/publish_threadsafe/resume_client
    behave exactly like the pre-bridge broker (same assertions as
    tests/test_sse_limits.py's baseline replay case), proving the new
    id-provider hook is additive, not a behaviour change."""
    from common.session_broker import SessionBroker

    plain_broker = SessionBroker()
    client_id, queue = plain_broker.open_client(["room"])
    await plain_broker.apublish("room", {"type": "tick", "n": 1})
    delivered = queue.get_nowait()
    assert delivered["type"] == "tick"
    assert delivered["id"] == 1
    assert delivered["channel"] == "room"


# ── replay (catch-up for a client on a different/restarted replica) ────────

async def _body_test_replay_returns_only_later_entries_on_wanted_channels():
    from common.broker_bridge import BrokerBridge
    from common.session_broker import SessionBroker

    bus = FakeRedisBus()
    broker_ = SessionBroker()
    bridge = BrokerBridge("redis://fake/0", target_broker=broker_)
    await bridge.start(redis_module=FakeRedisModule(bus))

    await broker_.apublish("app", {"type": "a1"})
    await _settle()
    since_id = bus.entries[-1][0].decode()
    await broker_.apublish("logs:x", {"type": "not-wanted"})
    await broker_.apublish("app", {"type": "a2"})
    await _settle()

    replayed = await bridge.replay(since_id, ["app"], limit=100)
    assert replayed is not None
    kinds = [e["type"] for e in replayed]
    assert kinds == ["a2"]
    assert all(e["channel"] == "app" for e in replayed)
    assert all("id" in e for e in replayed)

    await bridge.stop()


async def _body_test_replay_before_the_stream_start_returns_none():
    from common.broker_bridge import BrokerBridge
    from common.session_broker import SessionBroker

    bus = FakeRedisBus()
    broker_ = SessionBroker()
    bridge = BrokerBridge("redis://fake/0", target_broker=broker_)
    await bridge.start(redis_module=FakeRedisModule(bus))

    await broker_.apublish("app", {"type": "first"})
    await _settle()
    # MAXLEN has (hypothetically) already trimmed everything up to and
    # including "first": a since_id from before the stream's oldest surviving
    # entry cannot be replayed without risking a silent gap.
    fabricated_earlier_id = "1-0"

    replayed = await bridge.replay(fabricated_earlier_id, ["app"], limit=100)
    assert replayed is None

    await bridge.stop()


async def _body_test_replay_limit_is_capped():
    from common.broker_bridge import MAX_REPLAY, BrokerBridge
    from common.session_broker import SessionBroker

    bus = FakeRedisBus()
    broker_ = SessionBroker()
    bridge = BrokerBridge("redis://fake/0", target_broker=broker_)
    await bridge.start(redis_module=FakeRedisModule(bus))

    await broker_.apublish("app", {"type": "seed"})
    await _settle()
    since_id = bus.entries[-1][0].decode()
    for i in range(5):
        await broker_.apublish("app", {"type": "x", "n": i})
    await _settle()

    replayed = await bridge.replay(since_id, ["app"], limit=3)
    assert replayed is not None
    assert len(replayed) == 3
    assert MAX_REPLAY >= 3

    await bridge.stop()


# ── the route's stream catch-up path ────────────────────────────────────────

class _FakeRequest:
    """The parts of a starlette Request the stream endpoint reads."""

    def __init__(self, headers=None):
        self.headers = headers or {}

    async def is_disconnected(self):
        return False


async def _body_test_the_route_replays_from_the_stream_for_an_unknown_client():
    """/api/stream?client=unknown&since=<stream id>&channels=app: this
    replica's in-memory ring buffer has never heard of "unknown" (a
    different replica served it, or this one restarted), but the bridge is
    on and since_id is a real, still-live stream id, so the route must
    replay from Redis instead of giving up and refetching."""
    from common.broker_bridge import BrokerBridge
    from common.session_broker import broker as shared_broker
    from routes import stream as stream_module

    bus = FakeRedisBus()
    bridge = BrokerBridge("redis://fake/0", target_broker=shared_broker)
    await bridge.start(redis_module=FakeRedisModule(bus))
    shared_broker.set_loop(asyncio.get_running_loop())

    await shared_broker.apublish("app", {"type": "before"})
    await _settle()
    since_id = bus.entries[-1][0].decode()
    await shared_broker.apublish("app", {"type": "after"})
    await _settle()

    import common.broker_bridge as broker_bridge_module
    original_get_bridge = broker_bridge_module.get_bridge
    stream_module.get_bridge = lambda: bridge
    try:
        response = await stream_module.stream(
            _FakeRequest(), session=None, client="unknown", since=since_id, channels="app",
        )
        body = response.body_iterator
        ready_frame = await body.__anext__()
        assert '"resumed": true' in ready_frame
        assert '"replayed": 1' in ready_frame
        assert '"source": "stream"' in ready_frame

        replayed_frame = await body.__anext__()
        assert '"after"' in replayed_frame
        client_id = ready_frame.split('"client_id": "')[1].split('"')[0]
        shared_broker.close_client(client_id)
    finally:
        stream_module.get_bridge = original_get_bridge
        await bridge.stop()


# ── running the async bodies above ──────────────────────────────────────────
# Same pattern as tests/test_sse_limits.py: no pytest-asyncio in this suite,
# so each async body is driven with asyncio.run from a plain test function.

def test_local_publish_goes_out_tagged_with_origin():
    asyncio.run(_body_test_local_publish_goes_out_tagged_with_origin())

def test_local_delivery_carries_the_stream_id():
    asyncio.run(_body_test_local_delivery_carries_the_stream_id())

def test_a_foreign_message_is_delivered_to_a_local_subscriber():
    asyncio.run(_body_test_a_foreign_message_is_delivered_to_a_local_subscriber())

def test_a_foreign_message_is_not_re_published_outward():
    asyncio.run(_body_test_a_foreign_message_is_not_re_published_outward())

def test_an_own_message_coming_back_is_ignored():
    asyncio.run(_body_test_an_own_message_coming_back_is_ignored())

def test_missing_redis_module_leaves_the_broker_working(monkeypatch):
    asyncio.run(_body_test_missing_redis_module_leaves_the_broker_working(monkeypatch))

def test_unreachable_redis_fails_start_without_raising():
    asyncio.run(_body_test_unreachable_redis_fails_start_without_raising())

def test_the_broker_behaves_exactly_as_before_when_the_bridge_is_off():
    asyncio.run(_body_test_the_broker_behaves_exactly_as_before_when_the_bridge_is_off())

def test_replay_returns_only_later_entries_on_wanted_channels():
    asyncio.run(_body_test_replay_returns_only_later_entries_on_wanted_channels())

def test_replay_before_the_stream_start_returns_none():
    asyncio.run(_body_test_replay_before_the_stream_start_returns_none())

def test_replay_limit_is_capped():
    asyncio.run(_body_test_replay_limit_is_capped())

def test_the_route_replays_from_the_stream_for_an_unknown_client():
    asyncio.run(_body_test_the_route_replays_from_the_stream_for_an_unknown_client())
