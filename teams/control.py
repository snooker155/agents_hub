"""
Cancellation for a team run — the half of "stop" that has to be immediate.

Stopping used to be one row in the database, read between rounds. That makes
"stop" mean "after every member of the current round has finished talking",
which on a four-member team is four full agent runs the user already said they
did not want — and each of those keeps calling the model.

So a stop is now two things at once:

* an in-memory :class:`threading.Event` per run, set the moment the request
  arrives. Every member turn carries :class:`TeamStopCallback`, which reads that
  event on every LangChain hook and raises out of the agent loop, so no further
  model or tool call is issued. The request already in flight is abandoned —
  the runner stops waiting on it rather than joining the thread.
* a stop on each member's own run record, so the existing
  ``RunStopCallback`` path and the runs UI agree with what the team page shows.

The database row stays the durable record (and the cross-process fallback);
this module is what makes pressing the button feel like pressing the button.
"""
from __future__ import annotations

import threading
from typing import Dict, Set

from langchain_core.callbacks import BaseCallbackHandler

_lock = threading.Lock()
#: team_run_id → the event that is set when that run is asked to stop.
_events: Dict[str, threading.Event] = {}
#: team_run_id → the agent run ids currently executing for it.
_active: Dict[str, Set[str]] = {}


def register(team_run_id: str) -> threading.Event:
    """Start tracking a run. Returns its (unset) stop event."""
    with _lock:
        event = _events.get(team_run_id)
        if event is None:
            event = _events[team_run_id] = threading.Event()
        _active.setdefault(team_run_id, set())
    return event


def release(team_run_id: str) -> None:
    """Forget a finished run — the process would otherwise hold every event it
    ever created for as long as it lives."""
    with _lock:
        _events.pop(team_run_id, None)
        _active.pop(team_run_id, None)


def is_stopped(team_run_id: str) -> bool:
    """Whether this run has been asked to stop, in this process."""
    with _lock:
        event = _events.get(team_run_id)
    return bool(event and event.is_set())


def request_stop(team_run_id: str) -> bool:
    """Stop a run now: set its event and stop every member turn in flight.

    Returns whether anything was actually running here. The caller still writes
    the durable status — a run started before this process was restarted has no
    event to set, and only the database knows about it.
    """
    with _lock:
        event = _events.get(team_run_id)
        run_ids = set(_active.get(team_run_id) or ())
    if event is not None:
        event.set()
    for run_id in run_ids:
        try:
            from managers.run_manager import stop_run_by_id
            stop_run_by_id(run_id)
        except Exception:
            pass
    return event is not None


def track(team_run_id: str, run_id: str) -> None:
    """Record that ``run_id`` is executing for this team run."""
    with _lock:
        _active.setdefault(team_run_id, set()).add(run_id)


def untrack(team_run_id: str, run_id: str) -> None:
    with _lock:
        _active.get(team_run_id, set()).discard(run_id)


class TeamRunStopped(InterruptedError):
    """Raised inside a member's agent loop when its team run was stopped."""


class TeamStopCallback(BaseCallbackHandler):
    """Abort a member's turn as soon as its team run is stopped.

    ``raise_error = True`` is required for LangChain to propagate the exception
    out of its callback dispatch instead of logging and continuing — the same
    contract ``RunStopCallback`` relies on.
    """

    def __init__(self, team_run_id: str) -> None:
        super().__init__()
        self.raise_error = True
        self.team_run_id = team_run_id

    def _check(self) -> None:
        if is_stopped(self.team_run_id):
            raise TeamRunStopped(f"Team run {self.team_run_id} was stopped by the user")

    # Start hooks abort before the next model/tool call is made; the end and
    # token hooks catch a stop that arrived while one was in flight.
    def on_chat_model_start(self, serialized, messages, **kwargs) -> None:  # noqa: ARG002
        self._check()

    def on_llm_start(self, serialized, prompts, **kwargs) -> None:  # noqa: ARG002
        self._check()

    def on_llm_new_token(self, token, **kwargs) -> None:  # noqa: ARG002
        self._check()

    def on_llm_end(self, response, **kwargs) -> None:  # noqa: ARG002
        self._check()

    def on_tool_start(self, serialized, input_str, **kwargs) -> None:  # noqa: ARG002
        self._check()

    def on_tool_end(self, output, **kwargs) -> None:  # noqa: ARG002
        self._check()

    def on_chain_start(self, serialized, inputs, **kwargs) -> None:  # noqa: ARG002
        self._check()


__all__ = [
    "register", "release", "is_stopped", "request_stop", "track", "untrack",
    "TeamStopCallback", "TeamRunStopped",
]
