"""
What a background run has streamed so far, for a page that opens (or comes
back) in the middle of it.

A run's live channel (common/run_stream.py: ``run:<flow_run_id>``,
``team:<team_run_id>``) only reaches the tabs following it when an event
goes past. A chat left for the flow page and opened again, or a flow page
opened halfway through a node, would see nothing of what was already written
until the next event, and never the text before it.

So the backend keeps each such channel's recent events here, numbered in
order (``_seq``, stamped on the event as it is published), and
``GET /api/stream/replay`` hands them out. A page folds the replay first and
then the live events past its last number, so nothing is missed and nothing
is counted twice.

Kept small: when a node or a member's turn ends, the streamed text of it
(``token``, ``think_delta``) is dropped, since its end event carries the
answer; its steps stay. A channel nobody has written to for a while is
forgotten. Process memory only: a backend restart starts the replay afresh.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Mapping

#: The channels kept: a flow run's and a team run's.
PREFIXES = ("run:", "team:")

#: The events of a turn dropped once the turn has ended.
_TEXT_TYPES = frozenset({"token", "think_delta"})

#: An end event, and the field naming whose turn ended.
_END_KEYS = {"node_done": "node_id", "turn_done": "run_id"}

#: Events kept per channel at most, and how long a quiet channel is kept.
MAX_EVENTS = 4000
TTL_SECONDS = 15 * 60

_lock = threading.Lock()
_channels: Dict[str, Dict[str, Any]] = {}


def kept(channel: str) -> bool:
    return bool(channel) and channel.startswith(PREFIXES)


def _sweep(now: float) -> None:
    for channel in [c for c, b in _channels.items() if now - b["touched"] > TTL_SECONDS]:
        del _channels[channel]


def record(channel: str, event: Mapping[str, Any]) -> Dict[str, Any]:
    """Keep ``event`` for ``channel`` and return it stamped with its number;
    an event of any other channel comes back unchanged."""
    if not kept(channel):
        return dict(event)
    now = time.time()
    with _lock:
        buf = _channels.setdefault(channel, {"seq": 0, "events": [], "touched": now})
        buf["seq"] += 1
        stamped = {**event, "_seq": buf["seq"]}
        events: List[Dict[str, Any]] = buf["events"]
        events.append(stamped)
        field = _END_KEYS.get(str(event.get("type") or ""))
        if field and event.get(field):
            whose = event.get(field)
            buf["events"] = events = [
                e for e in events if not (e.get("type") in _TEXT_TYPES and e.get(field) == whose)
            ]
        if len(events) > MAX_EVENTS:
            buf["events"] = events[-MAX_EVENTS:]
        buf["touched"] = now
        _sweep(now)
    return stamped


def replay(channel: str) -> Dict[str, Any]:
    """``{"events": [...], "seq": n}``: what the channel has kept, and the
    number of its last event (0 for a channel with none)."""
    with _lock:
        buf = _channels.get(channel)
        if not buf:
            return {"events": [], "seq": 0}
        return {"events": list(buf["events"]), "seq": buf["seq"]}


def reset() -> None:
    with _lock:
        _channels.clear()


__all__ = ["MAX_EVENTS", "PREFIXES", "kept", "record", "replay", "reset"]
