"""
A chat turn driven on a task of its own, relayed to whoever is reading.

The web chat's pipeline (``chat.pipelines.run_chat_pipeline``) is an async
generator: it runs as fast as its consumer pulls. Iterated straight from an
HTTP response, a consumer that goes away (a visitor pressing stop, an SDK
timing out) cancels it at an ``await`` and leaves the agent task it started
running on with nobody watching. The relay puts the pipeline on its own task
instead and hands its events over through a queue, so:

* the reader can leave at any time; :meth:`TurnRelay.stop` then asks the run
  to stop the way the chat's stop button does (``stop_run_by_id``), and the
  pipeline winds down normally, finalizing the run record and emitting its
  ``done``;
* whatever has to happen at the end of a turn (the widget writes the reply
  into its thread) runs in ``on_event`` on the relay's task, so it happens
  whether or not anybody is still reading;
* the acting user is bound on that task (``common.identity``), so the runs,
  sessions and instances the turn creates are stamped with the principal the
  caller acts as (the widget's owner, the ``/v1`` key's owner); when the
  caller presented a personal API key, its id is bound the same way
  (``common.api_keys``), so the run also carries ``key_id`` for the key's
  money quota and the accounting report (docs/costs.md "Attribution").

Used by the widget's message endpoint and by ``/v1``'s agent models.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional, Set

log = logging.getLogger(__name__)

#: Strong references to relay tasks in flight: asyncio only keeps weak ones,
#: and a turn nobody reads any more must still run to its end.
_TASKS: Set[asyncio.Task] = set()

_END = object()

EventHook = Callable[[Dict[str, Any]], Optional[Awaitable[None]]]


class TurnRelay:
    """One chat turn on its own task. Start it, read :meth:`events`, and call
    :meth:`stop` if the reader leaves before ``done``."""

    def __init__(self, request: Any, *, user_id: Optional[str] = None,
                 key_id: Optional[str] = None,
                 on_event: Optional[EventHook] = None) -> None:
        self.request = request
        self.user_id = user_id
        self.key_id = key_id
        self.on_event = on_event
        #: The run answering right now: the first ``meta``'s run, then the
        #: next agent's after a handoff.
        self.run_id: Optional[str] = None
        #: The turn's ``done`` event once it arrived.
        self.done: Optional[Dict[str, Any]] = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> "TurnRelay":
        self._task = asyncio.create_task(self._drive())
        _TASKS.add(self._task)
        self._task.add_done_callback(_TASKS.discard)
        return self

    @property
    def finished(self) -> bool:
        return self.done is not None

    def stop(self) -> bool:
        """Ask the running turn to stop. False when there is nothing to stop."""
        if self.finished or not self.run_id:
            return False
        try:
            from managers.run_manager import stop_run_by_id
            return bool(stop_run_by_id(self.run_id))
        except Exception:  # noqa: BLE001 - a stop that cannot be delivered leaves the turn to finish on its own
            log.warning("relay: could not stop run %s", self.run_id, exc_info=True)
            return False

    async def wait(self) -> Optional[Dict[str, Any]]:
        """Drain the turn to its end; the ``done`` event."""
        async for _event in self.events():
            pass
        return self.done

    # ── the reader's side ────────────────────────────────────────────────────

    async def events(self) -> AsyncIterator[Dict[str, Any]]:
        """Every event of the turn, ending with its one ``done``. Closing this
        generator early does not stop the turn; call :meth:`stop` for that."""
        while True:
            event = await self._queue.get()
            if event is _END:
                return
            yield event

    async def next_event(self, timeout: float) -> Optional[Dict[str, Any]]:
        """The next event, or None after ``timeout`` seconds of quiet (a
        caller keeping a connection alive sends a ping then). Raises
        ``StopAsyncIteration`` once the turn is over."""
        try:
            event = await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        if event is _END:
            raise StopAsyncIteration
        return event

    # ── the relay's own task ─────────────────────────────────────────────────

    async def _emit(self, event: Dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "meta" and event.get("run_id") and not self.run_id:
            self.run_id = str(event["run_id"])
        elif kind == "handoff" and event.get("next_run_id"):
            self.run_id = str(event["next_run_id"])
        elif kind == "done":
            self.done = event
        if self.on_event is not None:
            try:
                result = self.on_event(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001 - a failing hook must not lose the turn for the reader
                log.warning("relay: event hook failed on %s", kind, exc_info=True)
        await self._queue.put(event)

    async def _drive(self) -> None:
        from chat import pipelines
        from common import identity
        from common import api_keys

        token = identity.set_current_user(self.user_id) if self.user_id else None
        key_token = api_keys.set_current_key_id(self.key_id) if self.key_id else None
        try:
            async for event in pipelines.run_chat_pipeline(self.request):
                # One done per turn: anything after it is not this turn's.
                # The generator is still drained to its end, so whatever the
                # pipeline does after its last event (the broadcast's
                # bookkeeping) runs as it does for the web chat.
                if isinstance(event, dict) and self.done is None:
                    await self._emit(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - any failure becomes the turn's one done event
            status = getattr(exc, "status_code", None)
            detail = getattr(exc, "detail", None) or str(exc) or exc.__class__.__name__
            if status is None:
                log.warning("relay: the turn failed", exc_info=True)
            if self.done is None:
                await self._emit({"type": "done", "ok": False, "error": str(detail),
                                  "status": status, "run_id": self.run_id})
        finally:
            if self.done is None:
                await self._emit({"type": "done", "ok": False, "error": "the turn ended without an answer",
                                  "run_id": self.run_id})
            if token is not None:
                identity.reset_current_user(token)
            if key_token is not None:
                api_keys.reset_current_key_id(key_token)
            await self._queue.put(_END)


__all__ = ["TurnRelay"]
