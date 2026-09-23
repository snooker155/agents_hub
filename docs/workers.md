# Roles, workers and the launch queue

One process does everything by default. This page is for the deployment
where the backend that answers HTTP is not the machine that runs agents:
several backend replicas, several worker hosts, one Postgres, and no run,
event or file lost when any one machine goes away. Nothing here is needed
on a laptop.

## Roles

`AGENTS_HUB_ROLE` says what a process does:

| Role | What it does | Started by |
|---|---|---|
| `all` (default) | Serves the API and spawns every run itself, on this host, as it always has. | `ah up`, `docker compose up` |
| `api` | Serves the API. Prepares each run (record, session, instance, log path) and puts the launch on the queue instead of spawning it. | the backend with `AGENTS_HUB_ROLE=api` |
| `worker` | Serves nothing. Claims launches from the queue and spawns them on its own host. | `ah worker` |

An `api` backend with no worker running accepts runs that sit in the queue
as `queued` until one appears; the health page shows the queue depth. A
worker needs the same database and the same `.env` as the backend (provider
keys, the API token), because it builds the child's environment itself:
nothing secret travels through the queue.

```bash
# host A: the API
AGENTS_HUB_ROLE=api ah up

# hosts B, C: workers
AGENTS_HUB_ROLE=worker ah worker
# a worker without a Docker socket
AGENTS_HUB_WORKER_MODES=local ah worker --concurrency 8
```

## The queue

`run_queue` is one table. A row is a launch request: the run id, its kind
(`task` or `flow`), the workspace, whether it wants a container, a priority,
and a JSON spec that is everything the launcher needs. A worker claims the
highest-priority oldest row it can run under a lease it renews every couple
of seconds while the child is alive, then closes the row when the child
exits.

The child is detached from the worker. A worker that dies does not take its
runs down: the run keeps writing its own heartbeat (below), the watchdog
keeps treating it as alive, and the queue sweep closes the orphaned row. A
worker that died before it managed to spawn leaves a row whose lease lapses;
the sweep hands it back to the queue for another worker, up to three
attempts, after which the run fails with the last error the workers saw.

On `SIGTERM` a worker stops claiming, keeps the rows of its running children
leased for up to `AGENTS_HUB_WORKER_DRAIN_SECONDS` (default 300) so a
replacement never double-launches them, releases its leases and exits.

## Heartbeats instead of pids

Every agent run stamps `runs.heartbeat_at` every `RUN_HEARTBEAT_SECONDS`
(default 15) through its state transport, so it works from a container with
the HTTP relay too. The watchdog reads that first: a run quiet for
`RUN_HEARTBEAT_STALE_SECONDS` (default 180) is dead whatever its pid says,
and a run with a fresh heartbeat is alive whatever its pid says. The pid and
container probes remain for records without a heartbeat, and only on the
host that started the run (`host` on the record).

A stop requested for a run on another host cannot send it a signal. The stop
marks the record `stop`; the run's next heartbeat reads that back and sends
itself the same signal a local stop would have.

## Checkpoint and resume

A run writes a checkpoint after every tool call: the tool trail so far, the
call in flight, the step and token counters. When the watchdog finds a dead
run that has one, it relaunches the run under the same id with
`--resume-checkpoint` instead of failing it, up to `RUN_MAX_AUTO_RESUMES`
(default 2) times. The resumed process turns the trail back into the
conversation the model saw and asks it to continue from its last
observation.

A tool call that was in flight at the moment of death is the delicate case.
A read, search or listing is simply dropped and the model issues it again.
A call with effects outside the run (`run_shell`, publishing to git, writing
files, starting another agent, sending a message, every MCP tool) stays in
the trail with a result saying it was interrupted and may or may not have
happened, so the model checks before repeating it. The set is
`tools.capabilities.NON_IDEMPOTENT_TOOLS`.

## Singletons under leases

Some background work must run on exactly one replica: the plan scheduler,
the run watchdog, the Telegram poller (two pollers on one bot token fight
over `getUpdates`), the outbox drainer, and, once the event bridge is on,
the external-state publisher. Each is a row in `service_leases`: the replica
holding an unexpired lease on a role runs it, every other replica checks
back each tick and takes over when the lease lapses (default TTL 90 s).
Replicas release their roles on shutdown, so a restart is taken over at
once. The health page lists every role, its holder and the age of the last
renewal.

## Outbox

Webhook and Slack deliveries are rows in `outbox` before they are HTTP
calls. The holder of the `outbox` lease drains the table, retrying a
failed delivery with backoff up to five times. A replica dying with
deliveries pending loses none of them; an event is delivered once.

## Containers across hosts

Whoever starts a container registers it in the `containers` table with its
host. The Containers page shows this host's daemon's view merged with what
other hosts registered, so a run container started by a worker is visible
from any backend replica, marked with its host.

## What still needs one host

Run logs, workspaces, view assets and generated Dockerfiles are files under
`.agents_hub/`. Workers on different hosts need that directory shared (a
bind mount, or the object-store mirror described in [scaling](scaling.md))
or they each see their own copy. The database is not one of those files
once `AGENTS_HUB_DATABASE_URL` points at Postgres.

Related: [scaling](scaling.md), [service-health](service-health.md),
[sessions-and-runs](sessions-and-runs.md), [containers](containers.md),
[notifications](notifications.md), [scheduling](scheduling.md), [cli](cli.md).
