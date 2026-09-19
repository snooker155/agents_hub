"""
Teams — a bounded set of agents that know each other.

The hub already has two ways for several agents to work together, and both are
missing the same thing. A **flow** wires agents into a DAG: the order is fixed
at design time and nobody talks. The **orchestrator** routes a task to whichever
agent looks suitable out of the whole registry: nobody knows who else exists.
A team is the middle case, and the one people actually describe when they say
"give this to my team":

* the cast is **fixed and small** — these four agents, not the workspace;
* every member carries a **manifest**: what it commits to doing on this team,
  written so the others can decide when to hand it something;
* every member's prompt carries the **charter** (what this team is for) and the
  **roster** (who else is here and what they are for), so an agent addresses a
  colleague by name rather than guessing;
* they **talk** — a shared board, broadcasts and directed messages — instead of
  being wired output-to-input.

Three modes over one model, differing only in who gets to speak:

* ``centralized`` — a lead assigns the work each round (orchestration with a
  fixed cast);
* ``autonomous``  — nobody acts unless a colleague asked them to. The entry
  member takes the incoming request and either does it or hands it on by name;
* ``parallel``    — every member acts every round against the board (the
  playground's simultaneity aimed at a real goal).

A team is attachable to the same things an agent or a flow is: a task, or a
chat request.

Data lives in SQLite (``teams``, ``team_runs``, ``team_messages``).
"""
from teams.models import (
    BROADCAST, DONE_TOKEN, HANDOFF_MODES, MODES, Team, TeamMember, TeamMessage,
    TeamRun,
)

__all__ = [
    "Team", "TeamMember", "TeamRun", "TeamMessage", "MODES", "HANDOFF_MODES",
    "BROADCAST", "DONE_TOKEN",
]
