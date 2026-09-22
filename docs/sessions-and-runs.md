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

## Session

A conversation: the runs that belong together. A chat session groups the turns;
a task session groups the work on one task; a flow session groups its nodes.

Stopping a session stops every run still going inside it.

## Run group

A session says which runs belong together. A **run group** says what set them
going. Four things own a set of runs:

| Kind | What it is | Its children |
| --- | --- | --- |
| `flow` | one execution of a flow | the runs of its nodes |
| `loop` | a flow re-run until an agent judges it good enough | one flow group per iteration |
| `team` | one execution of a team against a goal | the members' turns |
| `container` | a parent task executing its subtasks | the runs on the subtasks |

Whichever kind you ask about, the answers have the same shape: a status, when it
started and finished, an error if there was one, the children, and the total
cost. The cost is always the same sum, the catalog price of the group's own
agent runs, so a flow, a loop and a team are priced by one rule and a loop's
spend is the spend of the flow runs inside it.

Stopping a group stops the work it owns. A flow group signals its orchestrator
and stops the node runs still in flight; a loop or a team asks its run to stop
at the next safe point, between iterations or between rounds; a container pauses
and re-queues the subtask it was on.

From the API: `GET /api/runs/groups?kind=&workspace=` lists them,
`GET /api/runs/groups/{kind}/{id}` reads one, `POST .../stop` stops it. Each
kind also keeps its own richer views under `/api/flows`, `/api/loops` and
`/api/teams`; the group endpoints are for when you want all four at once.

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

Related: [instances](instances.md), [service-health](service-health.md), [costs](costs.md).
