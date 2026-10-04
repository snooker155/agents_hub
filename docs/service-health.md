# Service health and diagnostics

Whether the moving parts are alive, and what to do when they are not. For a
symptom-confirm-fix writeup of the failures people actually hit, see the
[runbook](runbook.md); for the two service level objectives judged over a
rolling window, see [slo](slo.md).

## The snapshot

`GET /api/health`, and the Service Agent's `service_health` tool, both report the
same thing:

- **database** — reachable, row counts per store, and how many runs are in a
  running state
- **entity_runs** — counts by kind (flow, loop, team, scenario) and active runs
  by kind
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
  `agents_hub_run_start_seconds` (a summary, the SLO window's p95 quantile),
  `agents_hub_slo_breach{objective=}` (1 in breach, 0 ok, omitted while there
  is not yet enough data; see [slo](slo.md)), and
  `agents_hub_info{role=,instance=}`.

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

## Doctor

The snapshot reports state; the doctor judges it. `GET /api/health/doctor`,
`ah doctor` (add `--json` for the raw result; exits 1 when a check fails) and
the Service Agent's `run_diagnostics` tool all run the same checks
(`common/doctor.py`). Each comes back `ok`, `warn`, `fail` or `skip` with one
sentence, the numbers behind it, and a link to its section below. The overall
status is the worst check; `skip` does not count. A check that errors reports
`fail` with the error, so the doctor itself never crashes.

### Check: migrations

Schema migrations the database has not applied, or ones it has that this build
does not know. Fail either way. Fix: restart the backend (it applies pending
migrations on start); a database ahead of the code means an older build is
running against a newer database, so upgrade the code.

### Check: provider

The default provider answers a model listing within 5 s, the same request the
Settings page's test button makes. Skip when no key is set or the provider is
a custom backend. Fix: check the key and base URL on the Settings page, and
the provider's status page.

### Check: cors

What `ALLOW_ORIGINS` lets browsers do. Ok for a list of origins or the local
dev defaults. With `*` the hub allows any origin but without credentials
(browsers refuse a wildcard with credentials anyway); that is fine in `single`
and `token` mode, and a warning in `multi` mode, where any site can call the
API with a token stolen from a person. Fix: set `ALLOW_ORIGINS` to the
dashboard's origins, comma separated.

### Check: stale runs

Agent runs still `running` whose heartbeat is older than the watchdog's
threshold (`RUN_HEARTBEAT_STALE_SECONDS`, 180 s), and expired singleton
leases. Warn; fail when there are stale runs and the run watchdog is not
running. Fix: the watchdog fails dead runs on its own; if it is down, restart
the backend. An expired lease means the process holding that role died.

### Check: run queue

Launches waiting in the queue. Warn when more than 10 wait, or one has waited
over 5 minutes, and no worker is alive. Fix: start a worker (`ah worker`, see
[workers](workers.md)), or run the backend in the `all` role.

### Check: outbox

Outbound notifications not yet delivered. Warn when any was given up on after
every retry, or more than 100 wait. Fix: check the notification endpoints'
URLs and credentials; the outbox lease holder delivers them.

### Check: disk

Free space under the state directory. Warn under 2 GB, fail under 500 MB. Fix:
prune old run logs (`prune_run_logs`), prune old `system/` branches, or give
the volume more room.

### Check: browser

The browser service's `/healthz` answers. Skip when `AGENTS_HUB_BROWSER_URL`
is not set; warn when it answers but `AGENTS_HUB_BROWSER_TOKEN` is missing.
Fix: start `deploy/browser`, check the URL and the token.

### Check: models runtime

The [model runtime](local-models.md)'s `/healthz` answers. Skip when `AGENTS_HUB_MODELS_URL` is not set; warn when it answers but `AGENTS_HUB_MODELS_TOKEN` is missing. Fix: start `deploy/models` (compose profile `models`, or host mode), check the URL and the token.

### Check: docker

A docker daemon answers. Fail when agents are set to run in docker; warn when
`run_code` needs it (no `CODE_RUNNER_FALLBACK=local`); skip otherwise. Fix:
start Docker, or check that this process can reach its socket.

### Check: sandbox

Every sandbox provider ([docs/sandboxes.md](sandboxes.md): docker, local,
e2b, modal), whether it can run something right now, and why not (daemon
down, SDK missing, key missing). Fail when the resolved default provider
cannot run at all; warn when it can but the egress proxy is off, so a
`limited`/`none` environment network policy is enforced only by the hub's
own tool checks rather than by the container network itself; ok when both
hold. Fix: start Docker (or set `CODE_RUNNER_FALLBACK=local`), install the
`e2b`/`modal` package and set its key, or set `AGENTS_HUB_EGRESS_PROXY=1` for
the enforced network policy.

### Check: frontend build

`dashboard/frontend/dist/index.html` is newer than every file under
`dashboard/frontend/src`. Skip when there is no build (the dev server serves
the frontend). Fix: `npm run build` in `dashboard/frontend`.

### Check: system workspace

The [system workspace](system-workspace.md) exists and its repository copy
has been made. Skip when `SYSTEM_WORKSPACE=false`. Fix: restart to seed it,
then sync the copy (`POST /api/system/sync`).

### Check: skills

No skill attached to an agent carries a high flag from the
[safety review](skills.md#safety-review), no published skill carries a
license that is not open, no high-flagged skill waits in a catalog, and every
repository skill has been reviewed. Skip when there are no skills. The detail
lists the flagged skills and says whether the `skill-scanner` command (Cisco's
open-source scanner) is installed for a deeper, offline second opinion. Fix:
open the skill on the Skills page, read its flags, then detach or delete it,
or keep it knowingly; for an unreviewed repository skill, **Sync from
repositories**.

## Support bundle

`GET /api/support/bundle` (admin only), the Health page's **Download support
bundle** button, or `ah support-bundle [--out PATH] [--since 24h]` from a
terminal (works in direct mode, or against a remote backend when
`AGENTS_HUB_URL` is set): one zip with `version.json`, `doctor.json`,
`health.json`, `migrations.json`, `config.json` (secrets reduced to "is it
set", never the value), `errors.json`, `slo.json` and a tail of each running
process's own log. `common.support_bundle.scrub` runs over every file before
it enters the zip, so nothing that looks like a key, a token or a
credentialed URL survives regardless of which section it turned up in. See
the [runbook](runbook.md#support-bundle) for the full field list.

## The Service Agent

The [system agent](system-agents.md) that owns this. It reads health,
containers, instances, sessions, runs, logs, the routing log, web calls
and spend, and can stop a run, stop or restart a resident instance or a
container, and prune old logs.

Every one of those actions refuses until you approve it, and the refusal names
what would be destroyed. It proposes the smallest thing that would fix the
problem, and it reads before it stops.

It has **no outbound channel at all**: no web, no file writes, no
notifications. That is deliberate. It reads every log in the system, and logs
hold whatever the service handled, so giving it a way out would turn the
service's own diagnostics into an exfiltration path. See
[tools-and-capabilities](tools-and-capabilities.md).

Related: [instances](instances.md), [sessions-and-runs](sessions-and-runs.md), [containers](containers.md), [web-logs](web-logs.md), [system-workspace](system-workspace.md), [runbook](runbook.md), [slo](slo.md).
