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

## Probes and metrics

`GET /api/health` is for an operator looking at a page. `/livez`, `/readyz`
and `/metrics` are for infrastructure: a load balancer, a container
orchestrator, a Prometheus scraper. All three sit outside `/api` and answer
without the operator's token whatever `AUTH_MODE` is set to, the same way
`/api/ingest` authenticates itself rather than as the operator: a scrape
target or a health check is not the operator either, and closing these three
behind the same token as the dashboard would mean handing that token to every
load balancer that needs to probe the service.

- **`GET /livez`** — 200 `{"status": "alive"}` whenever the process can run
  Python at all. No database access: a liveness probe that can fail because
  the database is slow would restart a process that was never the problem.
- **`GET /readyz`** — whether this process should receive traffic right now.
  200 when the database answers a trivial query and, whichever of the checks
  below apply, none of them says no; 503 otherwise. The body names each
  check, so a 503 does not require guessing:
  - `database` — a trivial query answered
  - `broker` — `null` when `AGENTS_HUB_BROKER_URL` is not set (nothing to
    check); `true`/`false` once it is, from the cross-replica broker
    bridge's own connection state
  - `blob` — `null` when `AGENTS_HUB_BLOB_URL` is not set; `"unknown"` when
    `common.blobs` cannot be read (never fails readiness); `true`/`false`
    from `common.blobs.configured()` otherwise
  - `singletons` (in the `all`/`api` roles only) — whether some process
    currently holds each singleton role's lease (scheduler, watchdog,
    outbox, and telegram when a bot token is configured). Informational,
    not a readiness failure: a deployment that has been up for one second
    has no holder yet, and that is normal, not degraded.
- **`GET /metrics`** — Prometheus text exposition format
  (`text/plain; version=0.0.4`), hand-written in `common/metrics.py` so a
  future in-process reader (the Service Agent, say) can call the same
  `render()` the route does. Metrics: `agents_hub_runs_total{status=}`,
  `agents_hub_runs_running`, `agents_hub_run_queue{status=}`,
  `agents_hub_run_queue_oldest_seconds`, `agents_hub_outbox{state=}`,
  `agents_hub_lease_age_seconds{role=}`, `agents_hub_lease_held{role=,owner=}`,
  `agents_hub_tokens_total{workspace=}`, `agents_hub_cost_usd_total{workspace=}`
  (omitted if the price catalog cannot be read), `agents_hub_database_up`,
  and `agents_hub_info{role=,instance=}`.

## Exporting runs as spans

Set `AGENTS_HUB_OTEL_EXPORT_URL` to an OTLP/HTTP traces endpoint (including
another Agents Hub's own `/api/ingest/v1/traces`) and every run that reaches a
terminal status (`completed`, `stopped`, `failed`, `error`) is posted there as
one span, best-effort, from a background thread (`common/otel_export.py`):
never blocks or fails the run, one retry, and a dropped export on a process
restart is an acceptable loss for telemetry. `AGENTS_HUB_OTEL_EXPORT_HEADERS`
adds extra request headers (a collector token, say) as `"k=v,k=v"`.

The span is shaped to match what this hub's own OTLP receiver
(`connections/otel.py`, `connections/otlp.py`) decodes, carrying `run_id`,
`task_id`, `workspace`, `agent_id`, `status`, provider, model, token counts,
duration and, when the price catalog can compute one, `cost_usd`. That
mirroring is deliberate: pointing one deployment's export at another
deployment's ingest endpoint records the same run there, the same way a real
collector forwarding to a second backend would.

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
