"""
One vocabulary of run statuses, and the moves a run may make between them.

Every kind of run the hub launches (an agent's task run, a flow, a loop, a
team, a scenario) used to spell its lifecycle a little differently: a flow
was ``pending`` before it ran, a scenario was ``starting``, a team was born
``running``, and a stop request was ``stop`` on an agent run but ``stopping``
on a loop. Readers that wanted "is it still going" (the run groups page, the
watchdog, the health snapshot) each kept their own union of those spellings.

This module is the one place that vocabulary lives:

* :class:`RunStatus` is the set every *entity* run (``entity_runs``,
  common/entity_runs.py) is stored with, and the set new code should use.
* :data:`TRANSITIONS` is the closed table of moves an entity run may make.
  :func:`check_transition` enforces it in the store, so a write that would
  move a run from ``completed`` back to ``running`` without going through a
  resume is refused rather than silently recorded.
* :func:`normalize` maps the spellings the leaf ``runs`` table still uses
  (``stop``, ``queued``, ``assigned``, ``awaiting_approval``, ``done``,
  ``error``) onto the same vocabulary, so a reader can treat a leaf and a
  container alike without renaming what agent runs store. The leaf table is
  hotter and far more numerous than the containers, and its statuses are
  read by the dashboard in a dozen places, so its stored spellings are left
  as they are; only the reading is unified.

The statuses, and what each means for any kind:

``pending``
    The record exists; no process has claimed it yet. A launch waiting for a
    worker (docs/workers.md), or one prepared but not yet spawned.
``running``
    A process is executing it and refreshing its heartbeat.
``stopping``
    Somebody asked it to stop. The process reads this back on its next
    heartbeat (or at its next safe point) and ends itself; the durable
    record then moves to ``stopped``.
``awaiting_input``
    Parked on a question to a person (a flow's ``human_interrupt`` node). Not
    live, not finished: a resume with the answer moves it back to ``running``.
``completed`` / ``stopped`` / ``failed``
    Finished. A ``stopped`` or ``failed`` run may be resumed from its
    checkpoint, which is the one way back into ``running``.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet, Iterable, Optional, Union


class RunStatus(str, Enum):
    pending = "pending"
    running = "running"
    stopping = "stopping"
    awaiting_input = "awaiting_input"
    completed = "completed"
    stopped = "stopped"
    failed = "failed"


#: Still going: a process exists for it, or one is about to.
ACTIVE_STATUSES: FrozenSet[str] = frozenset({
    RunStatus.pending.value, RunStatus.running.value, RunStatus.stopping.value,
})

#: Neither live nor finished: waiting on a person.
PARKED_STATUSES: FrozenSet[str] = frozenset({RunStatus.awaiting_input.value})

#: Over, one way or another.
TERMINAL_STATUSES: FrozenSet[str] = frozenset({
    RunStatus.completed.value, RunStatus.stopped.value, RunStatus.failed.value,
})

#: What a stop request may be applied to.
STOPPABLE_STATUSES: FrozenSet[str] = frozenset({
    RunStatus.pending.value, RunStatus.running.value,
})

#: Statuses a resume may start from. ``running`` is in the set because the
#: watchdog resumes a run whose process died while its record still says
#: running; the record never had a chance to say anything else.
RESUMABLE_STATUSES: FrozenSet[str] = frozenset({
    RunStatus.running.value, RunStatus.stopped.value, RunStatus.failed.value,
    RunStatus.awaiting_input.value,
})


# The closed transition table. Every edge traces to a real writer:
#   pending   -> running   the launcher recorded a pid or a container
#   pending   -> stopping  a stop request before the process started
#   pending   -> failed    the launch itself failed (no worker, bad spec)
#   pending   -> stopped   stopped before it ever started
#   pending   -> completed a run that needed no process (an empty flow)
#   running   -> stopping  a stop request
#   running   -> completed / failed / stopped   the runner's own ending
#   running   -> awaiting_input                 a human_interrupt node
#   stopping  -> stopped / completed / failed   the runner honouring the stop
#                (completed when the stop landed after the last round/tick)
#   awaiting_input -> running   resumed with an answer
#   awaiting_input -> stopped / failed          stopped or closed while parked
#   stopped / failed -> running   a resume from the checkpoint
#   stopped / failed -> pending   a resume handed to the launch queue
# A same-status write is always allowed (progress writes repeat the status).
TRANSITIONS: Dict[str, FrozenSet[str]] = {
    RunStatus.pending.value: frozenset({
        RunStatus.running.value, RunStatus.stopping.value, RunStatus.failed.value,
        RunStatus.stopped.value, RunStatus.completed.value,
    }),
    RunStatus.running.value: frozenset({
        RunStatus.stopping.value, RunStatus.completed.value, RunStatus.failed.value,
        RunStatus.stopped.value, RunStatus.awaiting_input.value,
    }),
    RunStatus.stopping.value: frozenset({
        RunStatus.stopped.value, RunStatus.completed.value, RunStatus.failed.value,
    }),
    RunStatus.awaiting_input.value: frozenset({
        RunStatus.running.value, RunStatus.stopped.value, RunStatus.failed.value,
    }),
    RunStatus.stopped.value: frozenset({RunStatus.running.value, RunStatus.pending.value}),
    RunStatus.failed.value: frozenset({RunStatus.running.value, RunStatus.pending.value}),
    RunStatus.completed.value: frozenset(),
}


class IllegalRunTransition(ValueError):
    """A status change the transition table forbids."""

    def __init__(self, from_status: str, to_status: str, run_id: str = "") -> None:
        self.from_status = from_status
        self.to_status = to_status
        self.run_id = run_id
        where = f" on run {run_id}" if run_id else ""
        super().__init__(
            f"Illegal run status transition {from_status!r} -> {to_status!r}{where}")


def as_status(value: Union[RunStatus, str, None]) -> str:
    """The plain string of a status, ``''`` for None."""
    if value is None:
        return ""
    return value.value if isinstance(value, RunStatus) else str(value)


def can_transition(from_status: Union[RunStatus, str, None],
                   to_status: Union[RunStatus, str]) -> bool:
    """Whether the table allows the move. A same-status write and a write
    onto a record with no status yet are always allowed."""
    src, dst = as_status(from_status), as_status(to_status)
    if not src or src == dst:
        return True
    return dst in TRANSITIONS.get(src, frozenset())


def check_transition(from_status: Union[RunStatus, str, None],
                     to_status: Union[RunStatus, str], *, run_id: str = "") -> None:
    """Raise :class:`IllegalRunTransition` unless the move is allowed."""
    if not can_transition(from_status, to_status):
        raise IllegalRunTransition(as_status(from_status), as_status(to_status), run_id)


def is_active(status: Union[RunStatus, str, None]) -> bool:
    return as_status(status) in ACTIVE_STATUSES


def is_terminal(status: Union[RunStatus, str, None]) -> bool:
    return as_status(status) in TERMINAL_STATUSES


# ── The leaf table's spellings ───────────────────────────────────────────────

# What ``runs.status`` may hold (managers/runs/*, runtime/agent_run.py), each
# mapped onto the shared vocabulary. Anything not listed maps to itself.
_LEAF_ALIASES: Dict[str, str] = {
    "queued": RunStatus.pending.value,         # waiting for a worker
    "assigned": RunStatus.pending.value,       # waiting for a node
    "awaiting_approval": RunStatus.pending.value,
    "stop": RunStatus.stopping.value,          # stop requested, not yet honoured
    "done": RunStatus.completed.value,
    "finished": RunStatus.completed.value,
    "error": RunStatus.failed.value,
    "starting": RunStatus.pending.value,       # scenario runs before this module
}


def normalize(status: Union[RunStatus, str, None]) -> str:
    """The shared spelling of any status the hub stores, leaf or container."""
    raw = as_status(status)
    return _LEAF_ALIASES.get(raw, raw)


def normalize_all(statuses: Iterable[Union[RunStatus, str, None]]) -> FrozenSet[str]:
    return frozenset(normalize(s) for s in statuses)


def leaf_is_active(status: Union[RunStatus, str, None]) -> bool:
    """Whether a leaf run's own status means it is still going. ``stop`` is a
    request the process has not honoured yet, so it counts as active."""
    return normalize(status) in ACTIVE_STATUSES


def display_status(status: Optional[str]) -> str:
    """The status a list page shows: the shared spelling, or the raw value
    when it is not one the table knows (a reader must never blank a row
    over an unfamiliar status)."""
    return normalize(status) or ""


__all__ = [
    "RunStatus", "ACTIVE_STATUSES", "PARKED_STATUSES", "TERMINAL_STATUSES",
    "STOPPABLE_STATUSES", "RESUMABLE_STATUSES", "TRANSITIONS",
    "IllegalRunTransition", "as_status", "can_transition", "check_transition",
    "is_active", "is_terminal", "normalize", "normalize_all", "leaf_is_active",
    "display_status",
]
