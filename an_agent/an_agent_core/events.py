from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional

from .types import Event, EventType


Subscriber = Callable[[Event], None]


@dataclass
class _Sub:
    event_type: EventType
    fn: Subscriber


class EventBus:
    """
    Минималистичный in-proc event bus.
    - publish: кладёт событие в очередь
    - drain: доставляет события подписчикам
    """

    def __init__(self, max_queue: int = 10_000) -> None:
        self._queue: Deque[Event] = deque(maxlen=max_queue)
        self._subs: List[_Sub] = []

    def subscribe(self, event_type: EventType, fn: Subscriber) -> None:
        self._subs.append(_Sub(event_type=event_type, fn=fn))

    def publish(self, event: Event) -> None:
        self._queue.append(event)

    def drain(self, limit: int = 1_000) -> int:
        delivered = 0
        for _ in range(min(limit, len(self._queue))):
            ev = self._queue.popleft()
            for sub in self._subs:
                if sub.event_type == ev.type:
                    sub.fn(ev)
            delivered += 1
        return delivered

    def size(self) -> int:
        return len(self._queue)
