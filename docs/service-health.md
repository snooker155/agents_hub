# Service health and diagnostics

Whether the moving parts are alive, and what to do when they are not.

## The snapshot

`GET /api/health`, and the Service Agent's `service_health` tool, both report the
same thing:

- **database** — reachable, row counts per store, and how many runs are in a
  running state
- **services** — plan scheduler, run watchdog, Telegram poller, external-state
  publisher
- **storage** — database size, WAL size, run-log size and file count, total
  state size
- **providers** — which is default, which keys are set (as booleans), whether
  API auth is on
- **blender** — the geometry engine: whether the connector is on, whether a
  binary was found, and how many engines are running right now, counted across
  processes rather than only this one
- **agent_cache** — whether the build cache is on, and its stats

## Reading it correctly

Two traps, both common:

- **`null` is not `false`.** A service reported as null means the probe could
  not tell: the module did not import, or it only exists inside the running web
  app. `false` means it is genuinely not running.
- **`running_runs` counts rows, not processes.** A large number with no live
  nodes means orphaned records left by a process that died, not live work.

## The usual diagnoses

| Symptom | Look at |
|---|---|
| Tasks never start | nodes: a `running` row with a dead pid |
| A chat hung | the session, then its runs, then the run's log |
| Everything is slow | runs by duration; then the model in use |
| Scheduled jobs stopped | `services.plan_scheduler` |
| Disk filling | `storage.run_logs_bytes` |
| Costs spiked | costs by agent and model over the window |
| A 3D view will not build | `blender.available` and `blender.engines_running` |
| An agent fetched something odd | the [web access log](web-logs.md) for that run |

## The Service Agent

The [system agent](system-agents.md) that owns this. It reads health,
containers, nodes, instances, sessions, runs, logs, the routing log, web calls
and spend, and can stop a run, a node or a container, and prune old logs.

Every one of those actions refuses until you approve it, and the refusal names
what would be destroyed. It proposes the smallest thing that would fix the
problem, and it reads before it stops.

It has **no outbound channel at all**: no web, no file writes, no
notifications. That is deliberate. It reads every log in the system, and logs
hold whatever the service handled, so giving it a way out would turn the
service's own diagnostics into an exfiltration path. See
[tools-and-capabilities](tools-and-capabilities.md).

Related: [nodes](nodes.md), [sessions-and-runs](sessions-and-runs.md), [containers](containers.md), [web-logs](web-logs.md).
