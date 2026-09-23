# Sessions and runs

These two records are how everything in the product becomes observable.

## Run

One agent invocation. The atom. Every chat message, every flow node, every
scenario tick, every delegation is a run, and each carries:

- which agent, model and provider
- status: `running`, `completed`, `failed`, `stopped`
- input and output
- token counts and duration
- an error, when it failed
- a log file with the full trace

## Entity runs

Flows, loops, teams and scenarios run as processes of their own, spawned
where the request landed or by a worker, and all four share one lifecycle. An entity run is one execution of any of these four kinds, recorded in
the `entity_runs` table alongside agent runs in the `runs` table.

Entity runs share one status vocabulary:

| Status | Meaning |
| --- | --- |
| `pending` | Record exists, no process yet; also a launch waiting for a worker |
| `running` | The process is alive |
| `stopping` | A stop was requested; the process will end at its next safe point |
| `awaiting_input` | A flow parked on a human interrupt node, waiting for an answer |
| `completed` | Done, succeeded |
| `stopped` | Done, stopped by request |
| `failed` | Done, failed |

A closed transition table is enforced: a finished run (completed, stopped, failed)
cannot move back to running except by a resume. Every entity run beats a heartbeat
every 15 seconds, and a stop requested from anywhere is read back on the next
beat. A process that ignores the stop for about two minutes is terminated.

Agent runs in the `runs` table keep their own spellings (queued, assigned,
awaiting_approval, stop, done), which readers map onto this vocabulary.

## Session

A conversation: the runs that belong together. A chat session groups the turns;
a task session groups the work on one task; a flow session groups its nodes.

Stopping a session stops every run still going inside it.

## Run group

A session says which runs belong together. A **run group** says what set them
going. Five things own a set of runs:

| Kind | What it is | Its children |
| --- | --- | --- |
| `flow` | one execution of a flow | the runs of its nodes |
| `loop` | a flow re-run until an agent judges it good enough | one flow group per iteration |
| `team` | one execution of a team against a goal | the members' turns |
| `scenario` | one execution of a scenario | the runs of its decisions per tick |
| `container` | a parent task executing its subtasks | the runs on the subtasks |

Whichever kind you ask about, the answers have the same shape: a status, when it
started and finished, an error if there was one, the children, and the total
cost. The cost is always the same sum, the catalog price of the group's own
agent runs, so a flow, a loop, a team and a scenario are priced by one rule and
a loop's spend is the spend of the flow runs inside it.

Stopping a group stops the work it owns (recursive stop). A flow group signals
its orchestrator and stops the node runs still in flight; a loop, team or
scenario asks its entity run to stop at the next safe point, between iterations,
rounds or ticks; a container pauses and re-queues the subtask it was on. Every
entity run nested under the group is stopped through `parent_run_id`, then every
leaf agent run they own.

From the API: `GET /api/runs/groups?kind=&workspace=` lists them,
`GET /api/runs/groups/{kind}/{id}` reads one, `POST .../stop` stops it. Each
kind also keeps its own richer views under `/api/flows`, `/api/loops`, `/api/teams`
and `/api/playground`; the group endpoints are for when you want all five at
once.

## Reading a failure

1. Find the run. Filter by status `failed` and a time window.
2. Read its `error` — often enough on its own.
3. Open its log when it is not.

Errors group usefully: the same message across several agents is a model or
infrastructure problem, while one agent failing repeatedly with different
messages is an agent problem.

## Stale runs

A run row saying `running` long after anything could still be running is an
orphan left by a process that died, not live work. They are counted separately
from real failures because they mean something different.

A running agent run stamps `heartbeat_at` every 15 seconds; a running entity
run (flow, loop, team, scenario) does the same. The watchdog reads heartbeats
first: a run quiet for its kind's threshold (default 180 seconds, 900 for loops)
is dead whatever its pid says, and a run with a fresh heartbeat is alive
whatever its pid says. The watchdog resumes a dead entity run from its checkpoint
up to 2 times, else fails it; a dead agent run is resumed from its own checkpoint
the same way.
A run with no heartbeat is probed by pid or container only on the host that
started it. The queue and heartbeat details are on the [workers](workers.md)
page.

## Live updates

Sessions and runs reach the dashboard over one shared SSE connection
(`GET /api/stream`), not one per page. A few things keep a slow or briefly
disconnected browser tab from costing the backend memory or the tab a wrong
view:

- **Queue limit.** Each connected tab has a bounded queue (1000 events by
  default, `AGENTS_HUB_SSE_QUEUE_MAX`). A tab that stops reading, backgrounded
  or stalled, never makes the backend hold events for it forever: once full,
  the oldest queued event is dropped for the newest one, and the tab is told
  with a `lagged` event carrying how many it missed, so it knows to reload
  rather than trust a gap it cannot see. High-volume per-session token events
  are merged into one another once a queue is over half full, so a lagging tab
  gets fewer, bigger frames instead of falling further behind.
- **Replay.** Every event delivered to a tab is numbered, and the last 500 are
  kept for 60 seconds after that tab disconnects. A reconnect (a network blip,
  a laptop waking up) that comes back within that window and names its
  previous connection resumes on the same id and channel set and replays
  exactly what it missed; the stream's `ready` event says so with
  `resumed: true` and how many were replayed.
- **When replay is not enough.** A reconnect outside the window, or one the
  backend no longer recognizes, comes back as `resumed: false`; a page open at
  the time has a gap replay cannot fill, so the frontend refetches its own
  data instead of trusting a stream that skipped a beat. The same happens on a
  `lagged` event.

Related: [instances](instances.md), [service-health](service-health.md), [costs](costs.md), [workers](workers.md).
