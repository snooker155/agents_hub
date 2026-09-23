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
until approved and shows that estimate. `POST /api/teams/{id}/run` returns at
once with a `pending` record; the rounds run in their own process, polling and
stamping a heartbeat every 15 seconds. `POST /api/teams/runs/{team_run_id}/resume`
relaunches a `stopped` or `failed` run from its checkpoint (400 when there is
nothing to resume).

After every round a checkpoint is written with the round number, the board
state, spend, final answer, and timestamps. Member turns have deterministic run
ids (uuid5 of team run id, round and speaker) and carry `parent_run_id`; on
resume a member whose message for that round is already on the board is not
re-run or re-billed. The leader's decision call and the synthesis call are not
deduplicated, so a resumed round re-asks the leader.

A team started from chat runs inside the API process (it streams the board live)
but now beats a heartbeat too. A team run claims its task as executor kind `team`.
`GET /api/teams/runs/{id}` includes `has_checkpoint`, `host`, `heartbeat_at`,
`resume_attempts`, `pid`, `log_file`.

## Gotchas

- Every member must exist and be available in the workspace.
- More members is not more thinking; it is more rounds of everyone talking.
  Three focused members usually beat eight.
- A centralized team with a weak leader degenerates into the leader doing all
  the work and paying for an audience.

Related: [flows](flows.md), [agents](agents.md), [costs](costs.md).
