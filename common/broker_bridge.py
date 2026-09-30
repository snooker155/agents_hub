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

- Every genuinely local event the broker publishes is appended to one shared
  Redis Stream (``STREAM_KEY``, ``XADD ... MAXLEN ~``) tagged with this
  replica's id (``origin``), its channel and the event itself as JSON. Redis
  hands back the id it assigned the entry, and that id becomes the event's
  ``"id"`` for every subscriber on this replica too, instead of each SSE
  client's own incrementing counter (see "Global ids" below).
- A background task reads that same stream with ``XREAD BLOCK`` from wherever
  it last left off. An entry whose ``origin`` is a DIFFERENT replica is
  re-published into the local broker (``skip_sinks=True``, and carrying the
  same stream id it arrived with) so this replica's own SSE clients get it
  too. An entry whose ``origin`` is this replica's own id is skipped: this
  replica already delivered it directly, at publish time, above.
- :meth:`BrokerBridge.replay` lets a browser that reconnects to a *different*
  replica than the one that served it before catch up anyway: it reads the
  stream with ``XRANGE`` from just after the id the browser last saw, instead
  of the per-process ring buffer ``SessionBroker.resume_client`` uses (which,
  being per-process, cannot help across replicas). See
  ``dashboard/backend/routes/stream.py``.

Why a Stream and not pub/sub
-----------------------------
The previous version of this module used Redis pub/sub: fire-and-forget,
fast, and exactly what "forward this to the other replicas' live
subscribers" needs. What it could not do is replay — pub/sub keeps no
history, so a browser that reconnects to a different replica (or to the same
one after a restart) had no way to ask "what did I miss?" beyond the
per-process ring buffer already covering the single-replica case. A Redis
Stream is an append-only log with the same "fan out to whoever is reading"
behaviour (``XREAD``) *plus* a range query over what already happened
(``XRANGE``), which is exactly the missing half.

Retention is by count, not time: ``XADD ... MAXLEN ~ N`` (``N`` from
``AGENTS_HUB_BROKER_STREAM_MAXLEN``, default 10000) caps the stream at
approximately (the ``~`` lets Redis trim in efficient batches instead of
exactly every write) the last N entries, not the last N seconds. A busy
deployment with many replicas and channels can burn through 10000 events in
well under a minute; a quiet one can hold hours of history in the same
budget. Sizing this by count rather than a TTL is deliberate: what actually
bounds how far ``replay`` can reach back is "how much just happened", which a
count tracks directly and a clock does not. In practice replay only ever
needs to cover a reconnect gap of a few seconds, so the default is generous
by a wide margin.

Global ids
----------
:class:`common.session_broker.SessionBroker` normally numbers every event it
delivers with a plain per-client integer (``1, 2, 3, ...``) — enough to
detect a gap and replay it, as long as the reconnect lands back on the same
process. Once a bridge is running, a browser's ``Last-Event-ID`` needs to
mean the same thing on *every* replica, so ``SessionBroker`` accepts a
"cross-replica id provider" hook (``set_id_provider``): when set, a genuinely
local publish is awaited through it before being delivered to this replica's
own clients, and it is this stream id (a string shaped like
``"1695400000000-0"``, not the old integer) that ends up as the event's
``"id"``. An event re-injected from another replica already carries that id
verbatim (it is threaded through as ``event_id=`` on the re-publish, see
``_handle_inbound``). Bridge off: ``set_id_provider`` is never called, the
hook is ``None``, and ``SessionBroker`` behaves exactly as before — the
integer counter, unchanged, byte for byte.

The alternative considered was: deliver locally first (with whatever id was
at hand), and patch the event's id once the ``XADD`` reply came back. That is
racy — a fast enough consumer can already have serialized the SSE frame,
integer id and all, before the patch lands — and "publish first, learn the
id, then deliver" is not actually expensive (the awaited round trip is to a
Redis instance on the same compose network, sub-millisecond in practice) so
the simpler, non-racy option was the one implemented.

``redis.asyncio`` is imported lazily, inside :meth:`BrokerBridge.start`, so a
deployment that never sets ``AGENTS_HUB_BROKER_URL`` never needs the package
installed. If the URL is set but the package is missing, or the initial
connection fails, one error is logged and the backend keeps running with the
bridge off — a broker outage must never be a backend outage. Once connected,
a dropped subscription retries with capped exponential backoff rather than
giving up, and resumes reading from the stream id it last saw rather than
from "now", so a brief Redis blip does not itself create a gap on top of the
one this module exists to close.

See docs/scaling.md for the operating picture (what is shared, what is not,
the exact commands, and the boundary of what this does and does not fix).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from typing import Any, List, Optional, Tuple

from common.session_broker import SessionBroker, broker as _default_broker

log = logging.getLogger("common.broker_bridge")

#: Single shared Redis Stream every replica appends to and reads from. Not
#: configurable: a deployment has exactly one meaningful topology (every
#: replica of one deployment shares one stream); a second knob here would
#: only invite two replicas silently never seeing each other.
STREAM_KEY = "agents_hub:events"

#: Default MAXLEN (approximate) for the shared stream; override with
#: AGENTS_HUB_BROKER_STREAM_MAXLEN. See the module docstring for why this is
#: a count, not a duration.
DEFAULT_STREAM_MAXLEN = 10000

_INITIAL_BACKOFF = 0.5
_MAX_BACKOFF = 30.0
#: How long an outbound XADD may take before this module gives up on getting
#: a global id for this event and falls back to the plain per-client counter.
#: A live chat token must never wait long on a slow or half-dead Redis.
_PUBLISH_TIMEOUT = 1.0
#: How long one XREAD BLOCK call waits before returning empty-handed, purely
#: so the subscriber loop wakes up often enough to notice ``_stopping``.
_XREAD_BLOCK_MS = 5000
#: Hard ceiling on one catch-up replay, regardless of what the caller asks
#: for — see BrokerBridge.replay.
MAX_REPLAY = 1000


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


def _stream_maxlen() -> int:
    """Read the MAXLEN setting fresh on every publish, the same way
    ``session_broker._queue_maxsize`` re-reads its own env var: a test (or an
    operator) changing it takes effect without a restart."""
    raw = os.environ.get("AGENTS_HUB_BROKER_STREAM_MAXLEN", "").strip()
    if not raw:
        return DEFAULT_STREAM_MAXLEN
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_STREAM_MAXLEN


def _to_str(value: Any) -> Optional[str]:
    """redis-py hands back bytes unless the client was built with
    decode_responses=True (it is not, here, to keep the JSON payload's own
    encoding decisions in this module's hands, not the client library's)."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _stream_id_tuple(value: Any) -> Optional[Tuple[int, int]]:
    """Parse a Redis stream id ("<ms>-<seq>") into a comparable tuple, or
    None if it is not shaped like one. Needed because these ids must be
    compared numerically, not lexicographically: "12-10" sorts before "12-9"
    as plain strings but is the later entry."""
    text = _to_str(value)
    if not text or "-" not in text:
        return None
    ms, _, seq = text.partition("-")
    if not (ms.isdigit() and seq.isdigit()):
        return None
    return (int(ms), int(seq))


def _stream_id_cmp(a: Any, b: Any) -> int:
    """-1/0/1 comparator for two stream ids. Malformed input compares as
    equal rather than raising — this only ever compares ids Redis itself
    handed back, so malformed input would mean a bug elsewhere, not a
    reason to crash a live stream."""
    ta, tb = _stream_id_tuple(a), _stream_id_tuple(b)
    if ta is None or tb is None:
        return 0
    if ta < tb:
        return -1
    if ta > tb:
        return 1
    return 0


class BrokerBridge:
    """Owns the Redis connection, the outbound publish-and-get-an-id hook and
    the inbound subscriber task.

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
        self._redis_module: Optional[Any] = None
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    async def start(self, redis_module: Optional[Any] = None) -> bool:
        """Connect and register the outbound id hook.

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
        except Exception as e:  # noqa: BLE001 - the caller must keep running either way (see docstring)
            log.error(
                "AGENTS_HUB_BROKER_URL=%s is set but the initial Redis connection failed (%s); "
                "running with the cross-replica broker bridge OFF.", self.url, e,
            )
            self._redis = None
            return False

        self._redis_module = redis_module
        self.broker.set_id_provider(self._publish_and_get_id)
        self._task = asyncio.create_task(self._subscribe_loop(), name="broker-bridge-subscribe")
        log.info("Cross-replica broker bridge started (origin=%s)", self.origin)
        return True

    async def stop(self) -> None:
        """Undo :meth:`start`: unhook the id provider, stop the subscriber,
        close the connection. Best-effort throughout — shutdown must not
        raise."""
        self._stopping = True
        self.broker.set_id_provider(None)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown must not raise (see docstring)
                log.debug("broker_bridge: subscribe task raised on cancel", exc_info=True)
            self._task = None
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:  # noqa: BLE001 - shutdown must not raise (see docstring)
                log.debug("broker_bridge: redis close failed", exc_info=True)
            self._redis = None

    # ── outbound: local publish -> Redis, id comes back before delivery ────

    async def _publish_and_get_id(self, channel: str, event: dict) -> Optional[str]:
        """The SessionBroker id-provider hook (see ``set_id_provider``):
        publish this locally-produced event onto the shared stream and hand
        back the id Redis assigned it, so every subscriber on this replica —
        and, once the subscriber loop below picks it up, every other
        replica's subscribers too — sees the same id for it. Returning None
        on any failure or timeout tells the broker to fall back to its own
        per-client counter for this one event instead: a Redis hiccup must
        never delay or drop a locally-delivered event.
        """
        if self._redis is None or self._stopping:
            return None
        fields = {
            "origin": self.origin,
            "channel": channel,
            "event": json.dumps(event, default=str),
        }
        try:
            entry_id = await asyncio.wait_for(
                self._redis.xadd(STREAM_KEY, fields, maxlen=_stream_maxlen(), approximate=True),
                timeout=_PUBLISH_TIMEOUT,
            )
        except Exception:  # noqa: BLE001 - a Redis hiccup must never delay or drop a local event (see docstring)
            log.debug("broker_bridge: outbound publish failed", exc_info=True)
            return None
        return _to_str(entry_id)

    # ── inbound: Redis -> local broker ──────────────────────────────────────

    async def _subscribe_loop(self) -> None:
        """Read the shared stream and re-publish foreign events locally,
        reconnecting with backoff if the connection drops.

        Starts at ``$`` (only entries added from here on — a fresh backend
        has no reason to replay history into its own live subscribers; that
        is what :meth:`replay` is for, on demand, for one reconnecting
        client). After the first entry, the cursor is a concrete id, so a
        reconnect after a dropped connection resumes from there rather than
        skipping back to "now" and losing whatever arrived during the drop.
        """
        cursor: str = "$"
        backoff = _INITIAL_BACKOFF
        while not self._stopping:
            try:
                resp = await self._redis.xread({STREAM_KEY: cursor}, block=_XREAD_BLOCK_MS)
                backoff = _INITIAL_BACKOFF  # a clean read earns a fresh budget
                if not resp:
                    continue
                for _stream_name, entries in resp:
                    for entry_id, fields in entries:
                        cursor = entry_id
                        if self._stopping:
                            break
                        await self._handle_inbound(entry_id, fields)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - background loop, must keep retrying with backoff
                if self._stopping:
                    return
                log.warning(
                    "broker_bridge: Redis stream read dropped, retrying in %.1fs", backoff, exc_info=True
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)
                # The old connection may be wedged; a fresh one is cheap.
                try:
                    self._redis = self._redis_module.from_url(self.url)
                except Exception:  # noqa: BLE001 - reconnect attempt, next loop iteration retries anyway
                    log.debug("broker_bridge: reconnect attempt failed", exc_info=True)

    async def _handle_inbound(self, entry_id: Any, fields: dict) -> None:
        try:
            f = {_to_str(k): v for k, v in fields.items()}
            origin = _to_str(f.get("origin"))
            channel = _to_str(f.get("channel"))
            event = json.loads(_to_str(f.get("event")) or "")
        except (TypeError, ValueError):
            return
        if origin == self.origin:
            return  # our own publish, read back off the stream we just wrote to
        if not channel or not isinstance(event, dict):
            return
        # skip_sinks=True: this event was already appended to the stream by
        # the replica that produced it. Re-publishing it here would write it
        # a second time and, with three or more replicas, multiply without
        # end. event_id carries the id it already has, so this replica's own
        # clients see the exact same id the origin replica's clients did.
        await self.broker.apublish(channel, event, skip_sinks=True, event_id=_to_str(entry_id))

    # ── catch-up: XRANGE for one reconnecting client ────────────────────────

    async def replay(self, since_id: str, channels: List[str], limit: int = MAX_REPLAY) -> Optional[List[dict]]:
        """Catch-up replay for a client the local ring buffer no longer knows
        (a different replica served it before, or this replica restarted).

        Reads the shared stream strictly after ``since_id`` with ``XRANGE``,
        keeps only entries on one of ``channels`` (a channel this client was
        never interested in must not force a refetch on its account), and
        returns each as a plain event dict shaped exactly like one
        ``SessionBroker`` would have delivered directly — ``channel`` and
        ``id`` included — so ``routes/stream.py`` can treat it identically to
        an in-memory replay.

        Returns None, not ``[]``, when ``since_id`` is at or before the
        oldest entry MAXLEN still lets the stream hold: entries between
        ``since_id`` and the truncation point are already gone, so what
        *can* be replayed would be a silently incomplete picture. The caller
        must fall back to ``resumed: false`` and let the browser refetch —
        the same fallback already used when the in-memory ring buffer does
        not know the client either.

        Capped at ``limit`` (1000 by default) so a very old ``since_id`` (or
        a bug) cannot turn one reconnect into replaying the whole stream.
        """
        if self._redis is None:
            return None
        limit = min(max(1, limit), MAX_REPLAY)
        wanted = set(channels)
        try:
            head = await self._redis.xrange(STREAM_KEY, min="-", max="+", count=1)
        except Exception:  # noqa: BLE001 - a Redis hiccup must fall back to a full refetch, not raise
            log.debug("broker_bridge: replay could not read the stream head", exc_info=True)
            return None
        if not head:
            return None  # the stream is empty: nothing to replay, nothing missed either
        oldest_id = _to_str(head[0][0])
        if oldest_id is not None and _stream_id_cmp(since_id, oldest_id) < 0:
            return None
        try:
            entries = await self._redis.xrange(STREAM_KEY, min=f"({since_id}", max="+", count=limit)
        except Exception:  # noqa: BLE001 - a Redis hiccup must fall back to a full refetch, not raise
            log.debug("broker_bridge: replay could not read entries", exc_info=True)
            return None
        replayed: List[dict] = []
        for entry_id, fields in entries:
            f = {_to_str(k): _to_str(v) for k, v in fields.items()}
            channel = f.get("channel")
            if not channel or channel not in wanted:
                continue
            try:
                event = json.loads(f.get("event") or "")
            except (TypeError, ValueError):
                continue
            if not isinstance(event, dict):
                continue
            event = dict(event)
            event["channel"] = channel
            event["id"] = _to_str(entry_id)
            replayed.append(event)
        return replayed


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


def get_bridge() -> Optional[BrokerBridge]:
    """The running bridge, or None when it is off (URL unset, or Redis was
    unreachable at startup). Used by routes/stream.py for the stream
    catch-up replay path, and by common/live_runs.py's Redis mirror, so both
    stay no-ops without needing to know anything about Redis themselves."""
    return _bridge


def get_redis() -> Optional[Any]:
    """The bridge's live Redis connection, or None when there is no bridge.
    A small accessor so other modules (common/live_runs.py's cross-replica
    mirror) can use the same connection without reaching into a private
    attribute or importing redis themselves."""
    return _bridge._redis if _bridge is not None else None


__all__ = [
    "BrokerBridge", "start_bridge", "stop_bridge", "get_bridge", "get_redis",
    "STREAM_KEY", "DEFAULT_STREAM_MAXLEN", "MAX_REPLAY",
]
