# Teams

A team is a bounded roster of agents that know each other and work over a shared
message board.

Where a [flow](flows.md) fixes the order of work in a graph, a team leaves it to
the members: they see each other's messages and respond.

## Modes

- **Centralized** — a leader agent directs the others. Needs a leader.
- **Autonomous** — no leader; members act on the board as they see fit.
- **Parallel** — members work the same request independently.

An **entry agent** receives the request first. It must be one of the members.

## The manifest

Each member has a line in the roster: which agent, what it is called here, and
its role. The name matters because members address each other by it, so two
members cannot share one.

## Running

A team run is members times rounds of model calls, so `run_team_tool` refuses
until approved and shows that estimate. It returns a run id and runs in the
background. `get_team_run_tool` reports how it went; `stop_team_run_tool` ends
it.

## Gotchas

- Every member must exist and be available in the workspace.
- More members is not more thinking; it is more rounds of everyone talking.
  Three focused members usually beat eight.
- A centralized team with a weak leader degenerates into the leader doing all
  the work and paying for an audience.

Related: [flows](flows.md), [agents](agents.md), [costs](costs.md).
