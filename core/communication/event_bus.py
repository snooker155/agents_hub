from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Any, Awaitable, Union

from core.models.telecom_types import Event, EventType

Subscriber = Callable[[Event], Union[None, Awaitable[None]]]

@dataclass
class _Sub:
    event_type: EventType
    fn: Subscriber

class EventBus:
    """
    Unified Event Bus for Operational and Evolutionary domains.
    Supports both sync and async subscribers.
    """

    def __init__(self, max_queue: int = 10_000) -> None:
        self._queue: Deque[Event] = deque(maxlen=max_queue)
        self._subs: List[_Sub] = []

    def subscribe(self, event_type: EventType, fn: Subscriber) -> None:
        self._subs.append(_Sub(event_type=event_type, fn=fn))

    def publish(self, event: Event) -> None:
        self._queue.append(event)
        # Optionally trigger drain if in async loop
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.create_task(self.drain_async())
        except RuntimeError:
            pass

    def drain(self, limit: int = 1_000) -> int:
        delivered = 0
        for _ in range(min(limit, len(self._queue))):
            ev = self._queue.popleft()
            for sub in self._subs:
                if sub.event_type == ev.type:
                    if asyncio.iscoroutinefunction(sub.fn):
                        # Warning: sync drain cannot await async subscribers properly
                        # In a real system, we'd have a better bridge
                        pass
                    else:
                        sub.fn(ev)
            delivered += 1
        return delivered

    async def drain_async(self, limit: int = 1_000) -> int:
        delivered = 0
        for _ in range(min(limit, len(self._queue))):
            ev = self._queue.popleft()
            for sub in self._subs:
                if sub.event_type == ev.type:
                    if asyncio.iscoroutinefunction(sub.fn):
                        await sub.fn(ev)
                    else:
                        sub.fn(ev)
            delivered += 1
        return delivered

    def size(self) -> int:
        return len(self._queue)
