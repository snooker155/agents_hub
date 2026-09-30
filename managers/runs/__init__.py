"""
The run subsystem, split by concern.

``managers.run_manager`` is the public face and re-exports everything here; this
package is where the code actually lives. The modules are layered, and imports
only ever point down the list:

- :mod:`notifications` — instance bookkeeping, inbox notifications, SSE deltas
- :mod:`store`         — the ``runs`` / ``run_payloads`` tables
- :mod:`lifecycle`     — open / close / status / stop of a single run
- :mod:`task_finalize` — what a finished run does to its task
- :mod:`groups`        — one interface over flow, loop, team and container runs

Nothing is imported eagerly here: importing the package must not drag in the
whole subsystem, and ``groups`` in particular reaches into flow, loop and team
stores that have no business loading whenever a run record is written.
"""
