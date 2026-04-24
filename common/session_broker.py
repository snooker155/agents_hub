"""
In-process async pub/sub broker for per-session SSE streaming.

Any code path (in-process agent run or via HTTP from a subprocess) can publish
events here. Subscribers (SSE connections) receive them in real time.

Usage
-----
In-process (from async context):
    from common.session_broker import broker
    await broker.apublish(session_id, {"type": "token", "token": "..."})

In-process (from sync thread / LangChain callback):
    broker.publish_threadsafe(session_id, {"type": "token", "token": "..."})

SSE subscriber (FastAPI route):
    async for event in broker.subscribe(session_id):
        yield f"data: {json.dumps(event)}\\n\\n"

Subprocess → HTTP POST to /api/sessions/{session_id}/events → apublish().
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import AsyncGenerator, Dict, List, Optional


class SessionBroker:
    """Fan-out pub/sub broker keyed by session_id."""

    HEARTBEAT_INTERVAL = 20.0  # seconds between keep-alive pings

    def __init__(self) -> None:
        # session_id → list of subscriber queues
        self._queues: Dict[str, List[asyncio.Queue]] = defaultdict(list)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once at FastAPI startup to store the running event loop."""
        self._loop = loop

    # ── publish ──────────────────────────────────────────────────────────────

    def publish_threadsafe(self, session_id: str, event: dict) -> None:
        """Publish from a sync thread (e.g. LangChain callback running in a thread pool).

        Safe to call from any thread; uses call_soon_threadsafe to hand off
        to the event loop without blocking.
        """
        loop = self._loop
        if not loop or not loop.is_running():
            return
        for q in list(self._queues.get(session_id, [])):
            loop.call_soon_threadsafe(q.put_nowait, event)

    async def apublish(self, session_id: str, event: dict) -> None:
        """Publish from an async context."""
        for q in list(self._queues.get(session_id, [])):
            await q.put(event)

    # ── subscribe ─────────────────────────────────────────────────────────────

    async def subscribe(self, session_id: str) -> AsyncGenerator[dict, None]:
        """Async generator that yields events for session_id.

        Sends a heartbeat every HEARTBEAT_INTERVAL seconds to keep the
        HTTP connection alive through proxies/load-balancers.

        Exits when:
        - A ``{"type": "session_done"}`` event is received.
        - The client disconnects (GeneratorExit from the caller).
        """
        queue: asyncio.Queue = asyncio.Queue()
        self._queues[session_id].append(queue)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=self.HEARTBEAT_INTERVAL)
                except asyncio.TimeoutError:
                    yield {"type": "heartbeat"}
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
            try:
                self._queues[session_id].remove(queue)
            except ValueError:
                pass
            # Clean up empty list
            if session_id in self._queues and not self._queues[session_id]:
                del self._queues[session_id]

    # ── close ─────────────────────────────────────────────────────────────────

    async def close_session(self, session_id: str) -> None:
        """Send a sentinel to all subscribers so they exit cleanly."""
        for q in list(self._queues.get(session_id, [])):
            await q.put(None)


# Module-level singleton — imported everywhere.
broker = SessionBroker()
