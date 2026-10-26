"""
The live stream of a flow, loop or team run, for every page that shows it.

A run started in the background (by an agent's tool, a schedule, a loop
pass) runs in a process of its own, with no event loop and no broker that
reaches a browser. Its pages still show it live: the run publishes what it
does on channels, and :func:`common.session_broker.publish_event` hands each
event to the backend, which fans it out to every tab following the channel.

**Channels.** A flow run streams on ``run:<flow_run_id>``; a loop on its own
``loop:<loop_run_id>`` and a team on ``team:<team_run_id>``, the channels
their pages already follow. A loop's pass is a flow run, so its nodes stream
on both the flow run's channel and the loop's.

**Events.** An agent's work is the vocabulary a chat turn streams
(agents/callbacks/chat_stream.py: ``token``, ``think``, ``tool_start``,
``tool_end``...), stamped with what it belongs to (``node_id``, the agent's
``run_id``), so every page folds it with the reducer it already has.

**Cost.** Every event is an HTTP post from a subprocess, so streamed text
(answer tokens, the deltas of a thought) is batched (:class:`ChannelRelay`),
as the session publisher does; any other event sends the batch first, so the
order on the page is the order of the run.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional

log = logging.getLogger(__name__)

#: Batch bounds for streamed text: small enough to read as live typing,
#: large enough to keep the posts in single digits per second.
TOKEN_FLUSH_CHARS = 160
TOKEN_FLUSH_SECONDS = 0.2

#: The streamed-text events a relay batches, and the field holding the text.
BATCHED = {"token": "token", "think_delta": "delta"}


def run_channel(run_id: Optional[str]) -> Optional[str]:
    """The channel a flow run streams on."""
    return f"run:{run_id}" if run_id else None


def publish(channels: Iterable[Optional[str]], event: Mapping[str, Any]) -> None:
    """Publish ``event`` on each of ``channels`` from wherever this runs. Never fatal."""
    from common.session_broker import publish_event
    for channel in channels:
        if not channel:
            continue
        try:
            publish_event(channel, dict(event))
        except Exception:  # noqa: BLE001 - a live view is an extra, never the run
            log.debug("run stream publish failed on %s", channel, exc_info=True)


class ChannelRelay:
    """A run's event sink for :class:`ChatStreamCallback` (its ``publish``):
    every event goes to ``channels`` with ``stamp`` laid over it, tokens
    batched. Thread-safe: the agent's callbacks fire from its worker thread.
    """

    def __init__(self, channels: Iterable[Optional[str]], stamp: Optional[Mapping[str, Any]] = None):
        self.channels: List[str] = [c for c in channels if c]
        self.stamp: Dict[str, Any] = dict(stamp or {})
        self._buf: List[str] = []
        self._buf_len = 0
        self._buf_event: Dict[str, Any] = {}
        self._buf_type = ""
        self._last_flush = 0.0
        self._lock = threading.Lock()

    def __call__(self, event: Mapping[str, Any]) -> None:
        if not self.channels:
            return
        kind = str(event.get("type") or "")
        field = BATCHED.get(kind)
        if field:
            text = str(event.get(field) or "")
            if not text:
                return
            if self._buf_type and self._buf_type != kind:
                self.flush()
            with self._lock:
                self._buf_type = kind
                self._buf.append(text)
                self._buf_len += len(text)
                self._buf_event = {k: v for k, v in event.items() if k != field}
                now = time.monotonic()
                if not self._last_flush:
                    self._last_flush = now
                due = (self._buf_len >= TOKEN_FLUSH_CHARS
                       or now - self._last_flush >= TOKEN_FLUSH_SECONDS)
            if due:
                self.flush()
            return
        self.flush()
        publish(self.channels, {**event, **self.stamp})

    def flush(self) -> None:
        """Send the batched text as one event of its kind."""
        with self._lock:
            if not self._buf:
                return
            kind = self._buf_type
            event = {**self._buf_event, "type": kind, BATCHED[kind]: "".join(self._buf)}
            self._buf.clear()
            self._buf_len = 0
            self._buf_type = ""
            self._last_flush = time.monotonic()
        publish(self.channels, {**event, **self.stamp})


__all__ = ["ChannelRelay", "TOKEN_FLUSH_CHARS", "TOKEN_FLUSH_SECONDS", "publish", "run_channel"]
