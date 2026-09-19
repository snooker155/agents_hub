"""
Cancellation and external triggers for a simulation — the two things that have
to reach a run that is already in flight.

Stopping used to be one row in the database, read between ticks. That makes
"stop" mean "after every agent of the current tick has finished thinking",
which on an eight-agent scenario is eight full model calls the user already
said they did not want. So a stop is now three things at once:

* an in-memory :class:`threading.Event` per run, set the moment the request
  arrives. Every decision carries :class:`SimStopCallback`, which reads the
  event on every LangChain hook and raises out of the model call, and the
  streaming loop in :mod:`playground.runner` checks it between chunks;
* the cancel flag on each decision in flight, which is what a decision streaming
  from a client that does not run callbacks reads;
* the durable ``stopping`` status in the database, which is the cross-process
  fallback and the record a reloaded page reads. Each interrupted decision then
  closes its own run record as stopped, so the runs UI agrees with what the
  playground page shows.

The same registry carries **external triggers**: a message injected from
outside the world (the UI, another service) addressed to one agent. A triggered
scenario is only interesting if something outside can poke it, and the poke has
to reach a loop that is already running — the same problem as a stop, so the
same place.
"""
from __future__ import annotations

import threading
from typing import Dict, List, Set

from langchain_core.callbacks import BaseCallbackHandler

_lock = threading.Lock()
#: sim_run_id → the event that is set when that run is asked to stop.
_events: Dict[str, threading.Event] = {}
#: sim_run_id → the agent run ids currently executing for it.
_active: Dict[str, Set[str]] = {}
#: sim_run_id → externally injected triggers waiting to be delivered.
_triggers: Dict[str, List[Dict[str, str]]] = {}
#: sim_run_id → set when a trigger arrives, so an idle loop wakes immediately
#: instead of sleeping out its grace period.
_arrivals: Dict[str, threading.Event] = {}


def register(sim_run_id: str) -> threading.Event:
    """Start tracking a run. Returns its (unset) stop event."""
    with _lock:
        event = _events.get(sim_run_id)
        if event is None:
            event = _events[sim_run_id] = threading.Event()
        _active.setdefault(sim_run_id, set())
        _triggers.setdefault(sim_run_id, [])
        _arrivals.setdefault(sim_run_id, threading.Event())
    return event


def release(sim_run_id: str) -> None:
    """Forget a finished run — the process would otherwise hold every event it
    ever created for as long as it lives."""
    with _lock:
        _events.pop(sim_run_id, None)
        _active.pop(sim_run_id, None)
        _triggers.pop(sim_run_id, None)
        _arrivals.pop(sim_run_id, None)


def is_registered(sim_run_id: str) -> bool:
    """Whether this process is the one running the sim."""
    with _lock:
        return sim_run_id in _events


def is_stopped(sim_run_id: str) -> bool:
    """Whether this run has been asked to stop, in this process."""
    with _lock:
        event = _events.get(sim_run_id)
    return bool(event and event.is_set())


def request_stop(sim_run_id: str) -> bool:
    """Stop a run now: set its event so every decision in flight aborts.

    Returns whether anything was actually running here. The caller still writes
    the durable status — a run started before this process was restarted has no
    event to set, and only the database knows about it.

    Note what this deliberately does *not* do: call ``stop_run_by_id`` on the
    decisions in flight. Those runs carry this process's own pid, because that
    is whose threads they run on, and the generic stop path signals the pid.
    The decisions abort by reading this event and then close their own records.
    """
    with _lock:
        event = _events.get(sim_run_id)
        arrival = _arrivals.get(sim_run_id)
    if event is not None:
        event.set()
    if arrival is not None:
        # An idle loop waiting for a trigger has to notice the stop too.
        arrival.set()
    return event is not None


def track(sim_run_id: str, run_id: str) -> None:
    """Record that ``run_id`` is executing for this simulation."""
    with _lock:
        _active.setdefault(sim_run_id, set()).add(run_id)


def untrack(sim_run_id: str, run_id: str) -> None:
    with _lock:
        _active.get(sim_run_id, set()).discard(run_id)


# ── External triggers ────────────────────────────────────────────────────────

def push_trigger(sim_run_id: str, agent: str, text: str,
                 sender: str = "(external)") -> bool:
    """Queue a message from outside the world for one agent.

    Returns whether the run is live in this process. The runner drains the
    queue at the top of each tick and delivers it through the environment's own
    inbox, so an injected message is indistinguishable from one an agent sent —
    it lands in the observation and, in triggered mode, wakes the recipient.
    """
    with _lock:
        queue = _triggers.get(sim_run_id)
        arrival = _arrivals.get(sim_run_id)
        if queue is None:
            return False
        queue.append({"agent": agent, "text": text, "sender": sender})
    if arrival is not None:
        arrival.set()
    return True


def drain_triggers(sim_run_id: str) -> List[Dict[str, str]]:
    with _lock:
        queue = _triggers.get(sim_run_id)
        if not queue:
            return []
        pending, _triggers[sim_run_id] = queue, []
        arrival = _arrivals.get(sim_run_id)
    if arrival is not None:
        arrival.clear()
    return pending


def wait_for_trigger(sim_run_id: str, timeout: float) -> bool:
    """Block up to ``timeout`` for an external trigger (or a stop).

    This is what makes a triggered scenario a live sandbox rather than a batch
    job: with nothing left to react to, the world waits to be poked instead of
    ending or spinning through empty ticks.
    """
    with _lock:
        arrival = _arrivals.get(sim_run_id)
    if arrival is None:
        return False
    return arrival.wait(timeout=max(0.0, timeout))


def pending_trigger_count(sim_run_id: str) -> int:
    with _lock:
        return len(_triggers.get(sim_run_id) or ())


class SimRunStopped(InterruptedError):
    """Raised inside a decision when its simulation was stopped."""


class SimStopCallback(BaseCallbackHandler):
    """Abort one agent's decision as soon as its simulation is stopped.

    ``raise_error = True`` is required for LangChain to propagate the exception
    out of its callback dispatch instead of logging and continuing — the same
    contract ``RunStopCallback`` relies on.
    """

    def __init__(self, sim_run_id: str) -> None:
        super().__init__()
        self.raise_error = True
        self.sim_run_id = sim_run_id

    def _check(self) -> None:
        if is_stopped(self.sim_run_id):
            raise SimRunStopped(f"Simulation {self.sim_run_id} was stopped by the user")

    # Start hooks abort before the next model call is made; the token and end
    # hooks catch a stop that arrived while one was already in flight.
    def on_chat_model_start(self, serialized, messages, **kwargs) -> None:  # noqa: ARG002
        self._check()

    def on_llm_start(self, serialized, prompts, **kwargs) -> None:  # noqa: ARG002
        self._check()

    def on_llm_new_token(self, token, **kwargs) -> None:  # noqa: ARG002
        self._check()

    def on_llm_end(self, response, **kwargs) -> None:  # noqa: ARG002
        self._check()

    def on_chain_start(self, serialized, inputs, **kwargs) -> None:  # noqa: ARG002
        self._check()


__all__ = [
    "register", "release", "is_registered", "is_stopped", "request_stop",
    "track", "untrack", "push_trigger", "drain_triggers", "wait_for_trigger",
    "pending_trigger_count", "SimStopCallback", "SimRunStopped",
]

