"""
Proactive agents: an agent with a pulse of its own (docs/proactive.md).

An agent whose profile is switched on wakes up on a schedule, looks at its
sources, decides whether there is anything to do, acts within a budget,
writes to the person only when it has something to say, and remembers
between ticks what it has already seen.

A tick is an ordinary scheduled job of kind ``heartbeat`` (plans/), so it
shows up among the jobs and the runs, is priced like any run, and obeys the
budget, the environment and the capability guard. The state between ticks
lives in the agent's memory (a core block) and in the scheduler's firing
journal, which carries the tick's outcome. Nothing new is stored elsewhere.

* :mod:`proactive.profile`: the profile record on the agent, its defaults
  and validation, the schedule, quiet hours and the answer schema.
* :mod:`proactive.service`: the job the profile owns, the gates a tick
  passes before it starts, the tick's prompt, and what happens when its run
  finishes.
"""
