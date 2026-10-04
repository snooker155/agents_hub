"""
Watchers: cheap background observers that wake an agent when something
outside the hub changes (docs/watchers.md).

A watcher polls one source on its own interval (a mailbox over IMAP, an HTTP
resource), remembers the last state it saw, and only when the state changes
hands an event to the proactive agents whose profile lists the watcher as a
trigger (``{"kind": "watch", "watcher_id": ...}``, proactive/events.py). No
model runs for a poll; the agent still only runs on a tick, so the cost of
watching is a network request every few minutes, not a run.

* :mod:`watchers.models`: the record, its kinds and their configuration.
* :mod:`watchers.kinds`: one ``probe`` per kind, returning the current state
  and the events since the last one.
* :mod:`watchers.service`: CRUD, the due-poll pass, auto pause on repeated
  errors, the header summary.
* :mod:`watchers.runner`: the asyncio loop next to the plan scheduler, held
  by one replica through a lease.
"""
