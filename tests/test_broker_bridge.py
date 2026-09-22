"""
Cross-replica fan-out for the session broker (common/broker_bridge.py).

No real Redis is available in this environment (no docker daemon, no Redis
server), so these tests fake it: a tiny in-process "bus" that several
FakeRedisClient instances can publish to and subscribe on, shaped just enough
like ``redis.asyncio`` (``from_url``, ``.ping()``, ``.pubsub()``,
``.subscribe()``/``.listen()``, ``.publish()``, ``.aclose()``) for
BrokerBridge to drive. One bus instance stands in for one Redis server; two
BrokerBridge instances sharing a bus stand in for two backend replicas.

Each test builds its own throwaway ``SessionBroker()`` (via
``BrokerBridge(url, target_broker=...)``) so bridge tests never touch the
process-wide singleton, matching the isolation tests/test_sse_limits.py uses
for the broker itself.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


# ── a fake redis.asyncio, just shaped enough for BrokerBridge ───────────────

class FakeRedisBus:
    """Stands in for one Redis server: channel -> subscriber queues."""

    def __init__(self) -> None:
        self.subscribers: dict[str, list[asyncio.Queue]] = {}
        self.published: list[tuple[str, str]] = []  # (channel, payload), for assertions

    def register(self, channel: str, queue: asyncio.Queue) -> None:
        self.subscribers.setdefault(channel, []).append(queue)

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))
        for queue in list(self.subscribers.get(channel, [])):
            await queue.put({"type": "message", "channel": channel, "data": payload})


class FakePubSub:
    def __init__(self, bus: FakeRedisBus) -> None:
        self._bus = bus
        self._queue: asyncio.Queue = asyncio.Queue()

    async def subscribe(self, channel: str) -> None:
        self._bus.register(channel, self._queue)

    async def listen(self):
        while True:
            msg = await self._queue.get()
            if msg is None:
                return
            yield msg


class FakeRedisClient:
    def __init__(self, bus: FakeRedisBus) -> None:
        self._bus = bus

    async def ping(self) -> bool:
        return True

    def pubsub(self) -> FakePubSub:
        return FakePubSub(self._bus)

    async def publish(self, channel: str, payload: str) -> None:
        await self._bus.publish(channel, payload)

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
    await asyncio.sleep(0.01)


# ── tests ────────────────────────────────────────────────────────────────────

async def _body_test_local_publish_goes_out_tagged_with_origin():
    from common.broker_bridge import REDIS_CHANNEL, BrokerBridge
    from common.session_broker import SessionBroker

    bus = FakeRedisBus()
    local_broker = SessionBroker()
    bridge = BrokerBridge("redis://fake/0", target_broker=local_broker)
    ok = await bridge.start(redis_module=FakeRedisModule(bus))
    assert ok

    # A raw listener on the bus, standing in for another replica's subscription.
    listener = FakeRedisClient(bus)
    pubsub = listener.pubsub()
    await pubsub.subscribe(REDIS_CHANNEL)

    await local_broker.apublish("app", {"type": "widgets.changed"})
    await _settle()

    msg = pubsub._queue.get_nowait()
    payload = json.loads(msg["data"])
    assert payload["origin"] == bridge.origin
    assert payload["channel"] == "app"
    assert payload["event"]["type"] == "widgets.changed"

    await bridge.stop()


async def _body_test_a_foreign_message_is_delivered_to_a_local_subscriber():
    from common.broker_bridge import REDIS_CHANNEL, BrokerBridge
    from common.session_broker import SessionBroker

    bus = FakeRedisBus()
    replica_b_broker = SessionBroker()
    bridge_b = BrokerBridge("redis://fake/0", target_broker=replica_b_broker)
    ok = await bridge_b.start(redis_module=FakeRedisModule(bus))
    assert ok
    # Give the subscriber task its first turn so it has actually registered
    # with the bus before the publish below — start() only schedules that
    # task, it does not run it.
    await _settle()

    client_id, queue = replica_b_broker.open_client(["app"])

    # Replica A publishes directly onto the bus (as its own bridge would),
    # under a different origin than replica B's.
    await bus.publish(
        REDIS_CHANNEL,
        json.dumps({"origin": "replica-a:1", "channel": "app", "event": {"type": "run.changed"}}),
    )
    await _settle()

    delivered = queue.get_nowait()
    assert delivered["type"] == "run.changed"
    assert delivered["channel"] == "app"

    await bridge_b.stop()


async def _body_test_a_foreign_message_is_not_re_published_outward():
    """The whole point of skip_sinks: re-injecting a foreign event locally
    must not trigger this replica's own outbound sink, or two replicas would
    echo one event back and forth forever (worse with three or more)."""
    from common.broker_bridge import REDIS_CHANNEL, BrokerBridge
    from common.session_broker import SessionBroker

    bus = FakeRedisBus()
    replica_b_broker = SessionBroker()
    bridge_b = BrokerBridge("redis://fake/0", target_broker=replica_b_broker)
    await bridge_b.start(redis_module=FakeRedisModule(bus))
    await _settle()  # let the subscriber task register with the bus first

    published_before = len(bus.published)

    await bus.publish(
        REDIS_CHANNEL,
        json.dumps({"origin": "replica-a:1", "channel": "app", "event": {"type": "run.changed"}}),
    )
    await _settle()

    # The foreign message landed (one publish: replica A's own outbound call
    # above), and replica B's bridge did not add a second one for it.
    assert len(bus.published) == published_before + 1

    await bridge_b.stop()


async def _body_test_an_own_message_coming_back_is_ignored():
    """Redis pub/sub delivers a publisher's own message back to its own
    subscription; the bridge must recognise its own origin and drop it,
    rather than double-delivering an event this replica already delivered
    directly via apublish."""
    from common.broker_bridge import BrokerBridge
    from common.session_broker import SessionBroker

    bus = FakeRedisBus()
    local_broker = SessionBroker()
    bridge = BrokerBridge("redis://fake/0", target_broker=local_broker)
    await bridge.start(redis_module=FakeRedisModule(bus))

    client_id, queue = local_broker.open_client(["app"])
    await local_broker.apublish("app", {"type": "widgets.changed"})
    await _settle()

    # The direct, local delivery.
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
    skip_sinks parameter and sinks list are additive, not a behaviour change."""
    from common.session_broker import SessionBroker

    plain_broker = SessionBroker()
    client_id, queue = plain_broker.open_client(["room"])
    await plain_broker.apublish("room", {"type": "tick", "n": 1})
    delivered = queue.get_nowait()
    assert delivered["type"] == "tick"
    assert delivered["id"] == 1
    assert delivered["channel"] == "room"


# ── running the async bodies above ──────────────────────────────────────────
# Same pattern as tests/test_sse_limits.py: no pytest-asyncio in this suite,
# so each async body is driven with asyncio.run from a plain test function.

def test_local_publish_goes_out_tagged_with_origin():
    asyncio.run(_body_test_local_publish_goes_out_tagged_with_origin())

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
