"""
The cross-replica mirror in common/live_runs.py.

common/live_runs.py is normally pure in-process memory (see its module
docstring) — fine for one backend replica, a gap once
``docker compose --scale backend=N`` is in play: a run relayed from a
subprocess can be watched from a browser tab pinned to a different replica
than the one running it. These tests cover the fix: with the cross-replica
broker bridge on, every turn that has a run_id is also mirrored into Redis
(throttled), and :func:`by_run_async` reads that mirror back when this
process's own memory has nothing — while :func:`by_run` itself, and the
throttle-free path when the bridge is off, stay exactly as they always were.

No real Redis is available here (see tests/test_broker_bridge.py's own note),
so a tiny fake stands in for the one thing common/broker_bridge.get_redis()
would otherwise hand back: an object with async get/set.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


class FakeRedis:
    """Just enough of redis.asyncio's client for the mirror: get/set with a
    TTL, and a record of every call so the throttle can be checked directly."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.calls: list[tuple[str, str, int]] = []

    async def set(self, key: str, value: str, ex: int = None) -> None:
        self.store[key] = value
        self.calls.append((key, value, ex))

    async def get(self, key: str):
        value = self.store.get(key)
        return value.encode() if isinstance(value, str) else value


@pytest.fixture(autouse=True)
def clean_live_runs():
    from common import live_runs

    live_runs.reset()
    yield
    live_runs.reset()


async def _settle() -> None:
    """The actual Redis write is a fire-and-forget task scheduled on the
    running loop (see live_runs._schedule), not run inline — give it a couple
    of ticks to finish before a test inspects the fake's call log."""
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.fixture
def fake_redis(monkeypatch):
    """Wires a FakeRedis in as if the bridge were on, by patching the same
    accessor common/live_runs.py itself calls (common.broker_bridge.get_redis)
    — the module under test never needs to know it is a fake."""
    import common.broker_bridge as broker_bridge_module

    redis_double = FakeRedis()
    monkeypatch.setattr(broker_bridge_module, "get_redis", lambda: redis_double)
    return redis_double


# ── the mirror is a no-op with the bridge off ───────────────────────────────

async def _body_test_mirror_is_a_no_op_when_the_bridge_is_off():
    """No fake_redis fixture here: common.broker_bridge.get_redis() is the
    real one, returns None (no bridge configured in this test process), and
    nothing must raise or block over it."""
    from common import live_runs

    live_runs.record_run_event({"type": "token", "token": "hi", "run_id": "r1"})
    assert live_runs.by_run("r1")["text"] == "hi"
    # by_run_async falls back to Redis only when the local copy is missing;
    # it is not missing here, so this must equal the plain in-process read,
    # bridge or no bridge.
    assert await live_runs.by_run_async("r1") == live_runs.by_run("r1")


# ── writing the mirror ──────────────────────────────────────────────────────

async def _body_test_record_run_event_mirrors_to_redis_keyed_by_run_id(fake_redis, monkeypatch):
    from common import live_runs

    # Isolate this test from the throttle (covered on its own below): each
    # call here should land so the mirrored text reflects both events.
    monkeypatch.setattr(live_runs, "MIRROR_MIN_INTERVAL", 0.0)

    live_runs.record_run_event({"type": "meta", "run_id": "r9", "agent_id": "worker"})
    live_runs.record_run_event({"type": "token", "token": "working", "run_id": "r9"})
    await _settle()

    assert len(fake_redis.calls) >= 1
    key, payload, ex = fake_redis.calls[-1]
    assert key == "agents_hub:live_run:r9"
    mirrored = json.loads(payload)
    assert mirrored["run_id"] == "r9"
    assert mirrored["text"] == "working"
    assert mirrored["agent_id"] == "worker"


async def _body_test_the_ttl_is_keep_finished_plus_a_margin(fake_redis):
    from common import live_runs

    live_runs.record_run_event({"type": "meta", "run_id": "r1"})
    await _settle()

    _, _, ex = fake_redis.calls[-1]
    assert ex == int(live_runs.KEEP_FINISHED_SECONDS + live_runs.MIRROR_TTL_MARGIN)


async def _body_test_repeated_records_are_throttled(fake_redis):
    """A token stream folds an event every few milliseconds; mirroring every
    one of them would flood Redis. Two record_run_event calls back to back
    (microseconds apart) must produce exactly one write, not two."""
    from common import live_runs

    live_runs.record_run_event({"type": "token", "token": "a", "run_id": "r1"})
    live_runs.record_run_event({"type": "token", "token": "b", "run_id": "r1"})
    await _settle()

    assert len(fake_redis.calls) == 1


async def _body_test_finish_writes_even_inside_the_throttle_window(fake_redis):
    """finish() is called once per turn, not at token-stream frequency, so it
    always mirrors the final state rather than risking it land inside the
    throttle window right after a record() and never get written before the
    in-memory turn itself is later swept away."""
    from common import live_runs

    turn_id = live_runs.start_turn(conversation_id="c1")
    live_runs.record(turn_id, {"type": "meta", "run_id": "r1"})
    await _settle()
    calls_after_record = len(fake_redis.calls)
    assert calls_after_record >= 1

    live_runs.finish(turn_id, status="finished")
    await _settle()

    assert len(fake_redis.calls) == calls_after_record + 1
    _, payload, _ = fake_redis.calls[-1]
    assert json.loads(payload)["status"] == "finished"


async def _body_test_a_turn_without_a_run_id_is_not_mirrored(fake_redis):
    """A pure chat turn never bound to a run_id (see _bind_run) has nothing
    to key a mirror entry by, and is silently skipped — the mirror's scope
    is run ids, matching what by_run_async reads back."""
    from common import live_runs

    turn_id = live_runs.start_turn(conversation_id="c1", user_message="hello")
    live_runs.record(turn_id, {"type": "token", "token": "hi, no run id here"})

    assert fake_redis.calls == []


# ── reading it back ──────────────────────────────────────────────────────────

async def _body_test_by_run_async_falls_back_to_the_mirror(fake_redis):
    """This process has never heard of "foreign-run" (by_run returns None),
    but another replica mirrored it into the shared Redis — the fallback
    read must find it."""
    from common import live_runs

    mirrored = {
        "turn_id": "t-other", "run_id": "foreign-run", "text": "from another replica",
        "status": "running", "thinking": [], "tools": [],
    }
    fake_redis.store["agents_hub:live_run:foreign-run"] = json.dumps(mirrored)

    assert live_runs.by_run("foreign-run") is None
    result = await live_runs.by_run_async("foreign-run")
    assert result["text"] == "from another replica"


async def _body_test_by_run_async_prefers_the_local_copy(fake_redis):
    """When this process DOES have the turn in memory, that is always more
    current than whatever is sitting in Redis (which might be a stale write
    that has not caught up yet) — by_run_async must not prefer the mirror."""
    from common import live_runs

    live_runs.record_run_event({"type": "token", "token": "fresh, local", "run_id": "r1"})
    fake_redis.store["agents_hub:live_run:r1"] = json.dumps({
        "turn_id": "stale", "run_id": "r1", "text": "STALE", "status": "running",
        "thinking": [], "tools": [],
    })

    result = await live_runs.by_run_async("r1")
    assert result["text"] == "fresh, local"


async def _body_test_by_run_async_with_no_mirror_and_no_local_copy_is_none(fake_redis):
    from common import live_runs

    assert await live_runs.by_run_async("never-heard-of-it") is None


# ── running the async bodies above ──────────────────────────────────────────
# Same pattern as tests/test_chat_broadcast.py: no pytest-asyncio in this
# suite, so each async body is driven with asyncio.run from a plain test
# function; the fake_redis/clean_live_runs fixtures are still resolved by
# pytest and just handed in as plain arguments.

def test_mirror_is_a_no_op_when_the_bridge_is_off():
    asyncio.run(_body_test_mirror_is_a_no_op_when_the_bridge_is_off())

def test_record_run_event_mirrors_to_redis_keyed_by_run_id(fake_redis, monkeypatch):
    asyncio.run(_body_test_record_run_event_mirrors_to_redis_keyed_by_run_id(fake_redis, monkeypatch))

def test_the_ttl_is_keep_finished_plus_a_margin(fake_redis):
    asyncio.run(_body_test_the_ttl_is_keep_finished_plus_a_margin(fake_redis))

def test_repeated_records_are_throttled(fake_redis):
    asyncio.run(_body_test_repeated_records_are_throttled(fake_redis))

def test_finish_writes_even_inside_the_throttle_window(fake_redis):
    asyncio.run(_body_test_finish_writes_even_inside_the_throttle_window(fake_redis))

def test_a_turn_without_a_run_id_is_not_mirrored(fake_redis):
    asyncio.run(_body_test_a_turn_without_a_run_id_is_not_mirrored(fake_redis))

def test_by_run_async_falls_back_to_the_mirror(fake_redis):
    asyncio.run(_body_test_by_run_async_falls_back_to_the_mirror(fake_redis))

def test_by_run_async_prefers_the_local_copy(fake_redis):
    asyncio.run(_body_test_by_run_async_prefers_the_local_copy(fake_redis))

def test_by_run_async_with_no_mirror_and_no_local_copy_is_none(fake_redis):
    asyncio.run(_body_test_by_run_async_with_no_mirror_and_no_local_copy_is_none(fake_redis))
