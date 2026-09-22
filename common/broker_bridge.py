"""
Optional cross-replica fan-out for the session broker.

``common/session_broker.py`` is an in-process pub/sub hub: a browser tab's SSE
connection is served by whichever backend replica accepted it, and an event
published on one replica is only ever delivered to that replica's own
subscribers. Running more than one replica (``docker compose --scale
backend=N``) behind nginx therefore splits live updates across replicas: a run
that finishes on replica A never reaches a tab whose stream happens to be
open on replica B.

This module bridges that gap, and only when asked to. Configuration is one
setting, ``AGENTS_HUB_BROKER_URL`` (``common.config.Settings.broker_url``).
Empty (the default) means off: nothing in this module runs, and the broker
behaves exactly as it does today, single-replica or not. Set to a Redis URL
(e.g. ``redis://redis:6379/0``) and, once started:

- Every event the local broker publishes (``apublish`` / ``publish_threadsafe``)
  is also published onto one shared Redis channel (``REDIS_CHANNEL``), tagged
  with ``{"origin": <this replica's id>, "channel": ..., "event": ...}``.
- A background task subscribes to that same channel and, for every message
  whose ``origin`` is a DIFFERENT replica, re-publishes the event into the
  local broker (``skip_sinks=True``) so this replica's own SSE clients get it
  too. Events re-injected this way are never sent back out: only genuinely
  local publishes are forwarded, so three or more replicas do not echo a
  single event around forever.
- A message whose ``origin`` is this replica's own id is dropped: Redis
  pub/sub delivers a publisher's own message back to its own subscription
  same as anyone else's, and re-injecting it locally would double-deliver an
  event this replica already delivered directly.

``redis.asyncio`` is imported lazily, inside :meth:`BrokerBridge.start`, so a
deployment that never sets ``AGENTS_HUB_BROKER_URL`` never needs the package
installed. If the URL is set but the package is missing, or the initial
connection fails, one error is logged and the backend keeps running with the
bridge off — a broker outage must never be a backend outage. Once connected,
a dropped subscription retries with capped exponential backoff rather than
giving up.

See docs/scaling.md for the operating picture (what is shared, what is not,
the exact commands, and the boundary of what this does and does not fix).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from typing import Any, Optional, Set

from common.session_broker import SessionBroker, broker as _default_broker

log = logging.getLogger("common.broker_bridge")

#: Single shared Redis pub/sub channel every replica publishes to and
#: subscribes on. Not configurable: a deployment has exactly one meaningful
#: topology (every replica of one deployment shares one channel); a second
#: knob here would only invite two replicas silently never seeing each other.
REDIS_CHANNEL = "agents_hub:events"

_INITIAL_BACKOFF = 0.5
_MAX_BACKOFF = 30.0


def _instance_id() -> str:
    """This replica's identity for the outbound ``origin`` field.

    ``AGENTS_HUB_INSTANCE_ID`` lets an operator pin a stable, readable name;
    absent that, hostname:pid mirrors the identity the plan scheduler's job
    leases already use (``plans.service._default_owner``) — unique enough
    across containers sharing a host network namespace and across processes
    on the same host.
    """
    explicit = os.environ.get("AGENTS_HUB_INSTANCE_ID", "").strip()
    if explicit:
        return explicit
    return f"{socket.gethostname()}:{os.getpid()}"


class BrokerBridge:
    """Owns the Redis connection, the outbound sink and the inbound subscriber task.

    Kept as an instance (rather than module-level state) so tests can spin up
    an isolated bridge against a fake Redis client without touching process
    globals. ``target_broker`` defaults to the shared session-broker singleton
    (what the running backend actually wants); tests pass in a throwaway
    ``SessionBroker()`` instead, the same way tests/test_sse_limits.py does
    for the broker itself, so a bridge test never touches process-wide state.
    """

    def __init__(self, url: str, *, target_broker: Optional[SessionBroker] = None) -> None:
        self.url = url
        self.broker = target_broker if target_broker is not None else _default_broker
        self.origin = _instance_id()
        self._redis: Optional[Any] = None
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        # Outbound publishes are fire-and-forget (see _on_local_publish); keep
        # a reference to each task until it finishes so it is not garbage
        # collected mid-flight, a known asyncio footgun for untracked tasks.
        self._pending: Set[asyncio.Task] = set()

    async def start(self, redis_module: Optional[Any] = None) -> bool:
        """Connect and register the outbound sink.

        Returns False (after logging once) if ``redis`` is not importable or
        the initial connection fails; the caller must keep running either
        way. ``redis_module`` lets tests inject a fake ``redis.asyncio``
        without the real package installed.
        """
        if redis_module is None:
            try:
                import redis.asyncio as redis_module  # type: ignore[no-redef]
            except ImportError:
                log.error(
                    "AGENTS_HUB_BROKER_URL is set but the 'redis' package is not installed; "
                    "running with the cross-replica broker bridge OFF. Install redis>=5,<6 to enable it."
                )
                return False

        try:
            self._redis = redis_module.from_url(self.url)
            await self._redis.ping()
        except Exception as e:
            log.error(
                "AGENTS_HUB_BROKER_URL=%s is set but the initial Redis connection failed (%s); "
                "running with the cross-replica broker bridge OFF.", self.url, e,
            )
            self._redis = None
            return False

        self._redis_module = redis_module
        self.broker.add_sink(self._on_local_publish)
        self._task = asyncio.create_task(self._subscribe_loop(), name="broker-bridge-subscribe")
        log.info("Cross-replica broker bridge started (origin=%s)", self.origin)
        return True

    async def stop(self) -> None:
        """Undo :meth:`start`: unhook the sink, stop the subscriber, close the
        connection. Best-effort throughout — shutdown must not raise."""
        self._stopping = True
        self.broker.remove_sink(self._on_local_publish)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        for task in list(self._pending):
            task.cancel()
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None

    # ── outbound: local publish -> Redis ────────────────────────────────────

    def _on_local_publish(self, channel: str, event: dict) -> None:
        """Sink registered on the local broker (see SessionBroker.add_sink):
        fan every locally-produced event out onto the shared Redis channel.

        Runs on the event loop thread (apublish calls it directly;
        publish_threadsafe schedules it there via call_soon_threadsafe), so
        creating a task here is safe. Never awaited: a slow or unreachable
        Redis must not stall whatever in-process code just called
        apublish/publish_threadsafe.
        """
        if self._redis is None or self._stopping:
            return
        payload = json.dumps(
            {"origin": self.origin, "channel": channel, "event": event}, default=str
        )
        task = asyncio.create_task(self._publish_safe(payload))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _publish_safe(self, payload: str) -> None:
        try:
            await self._redis.publish(REDIS_CHANNEL, payload)
        except Exception:
            log.debug("broker_bridge: outbound publish failed", exc_info=True)

    # ── inbound: Redis -> local broker ──────────────────────────────────────

    async def _subscribe_loop(self) -> None:
        """Subscribe to the shared channel and re-publish foreign events
        locally, reconnecting with backoff if the connection drops."""
        backoff = _INITIAL_BACKOFF
        while not self._stopping:
            try:
                pubsub = self._redis.pubsub()
                await pubsub.subscribe(REDIS_CHANNEL)
                backoff = _INITIAL_BACKOFF  # a clean subscribe earns a fresh budget
                async for message in pubsub.listen():
                    if self._stopping:
                        break
                    if message.get("type") != "message":
                        continue
                    await self._handle_inbound(message.get("data"))
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._stopping:
                    return
                log.warning(
                    "broker_bridge: Redis subscription dropped, retrying in %.1fs", backoff, exc_info=True
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)
                # The old connection may be wedged; a fresh one is cheap.
                try:
                    self._redis = self._redis_module.from_url(self.url)
                except Exception:
                    pass

    async def _handle_inbound(self, raw: Any) -> None:
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(msg, dict):
            return
        if msg.get("origin") == self.origin:
            return  # our own publish, echoed back by Redis pub/sub to our own subscription
        channel = msg.get("channel")
        event = msg.get("event")
        if not channel or not isinstance(event, dict):
            return
        # skip_sinks=True: this event was already fanned out by the replica
        # that produced it. Re-forwarding it here would echo it back onto
        # Redis and, with three or more replicas, multiply without end.
        await self.broker.apublish(channel, event, skip_sinks=True)


_bridge: Optional[BrokerBridge] = None


async def start_bridge() -> None:
    """Start the bridge if ``AGENTS_HUB_BROKER_URL`` is configured.

    Safe to call unconditionally from the app lifespan: a no-op when the URL
    is empty, and any failure is logged rather than raised (see
    BrokerBridge.start), so a bad or unreachable Redis never takes the
    backend down with it.
    """
    global _bridge
    from common.config import settings
    url = (settings.broker_url or "").strip()
    if not url:
        return
    bridge = BrokerBridge(url)
    ok = await bridge.start()
    if ok:
        _bridge = bridge


async def stop_bridge() -> None:
    """Stop the bridge started by :func:`start_bridge`, if any. Idempotent."""
    global _bridge
    if _bridge is not None:
        await _bridge.stop()
        _bridge = None


__all__ = ["BrokerBridge", "start_bridge", "stop_bridge", "REDIS_CHANNEL"]
