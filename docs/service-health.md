# Service health and diagnostics

Whether the moving parts are alive, and what to do when they are not.

## The snapshot

`GET /api/health`, and the Service Agent's `service_health` tool, both report the
same thing:

- **database**: reachable, row counts per store, and how many runs are in a
  running state
- **entity_runs**: counts by kind (flow, loop, team) and active runs
  by kind
- **services**: plan scheduler and run watchdog
- **storage**: database size, WAL size, run-log size and file count, total
  state size
- **providers**: which is default and which keys are set (as booleans)
- **agent_cache**: whether the build cache is on, and its stats

## Reading it correctly

Two traps, both common:

- **`null` is not `false`.** A service reported as null means the probe could
  not tell: the module did not import, or it only exists inside the running web
  app. `false` means it is genuinely not running.
- **`running_runs` counts rows, not processes.** A large number with nothing
  actually running means orphaned records left by a process that died, not live work.

## Probes

`GET /api/health` is for you looking at a page. `/livez` and `/readyz` are
for a container health check. Both sit outside `/api`.

- **`GET /livez`**: 200 `{"status": "alive"}` whenever the process can run
  Python at all. No database access.
- **`GET /readyz`**: 200 when the database answers a trivial query, 503
  otherwise. The body names the check.

## The usual diagnoses

| Symptom | Look at |
|---|---|
| Tasks never start | a `running` row with a dead pid |
| A chat hung | the session, then its runs, then the run's log |
| Everything is slow | runs by duration; then the model in use |
| Scheduled jobs stopped | `services.plan_scheduler` |
| Disk filling | `storage.run_logs_bytes` |
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
dev defaults. With `*` the hub allows any origin but without credentials,
which is fine for one person. Fix: set `ALLOW_ORIGINS` to the dashboard's
origins, comma separated.

### Check: security

The settings the hardening list in `SECURITY.md` (repository root) asks for,
read together. A warning for each of: `CAPABILITY_GUARD` set to anything but
`block`; secrets stored while `AGENTS_HUB_SECRET_KEY` is empty (runs then
receive none of them). Fix: the setting the warning names, in `.env` or on
the Settings page.

### Check: stale runs

Agent runs still `running` whose heartbeat is older than the watchdog's
threshold (`RUN_HEARTBEAT_STALE_SECONDS`, 180 s). Warn; fail when there are stale runs and the run watchdog is not
running. Fix: the watchdog fails dead runs on its own; if it is down, restart
the backend.

### Check: disk

Free space under the state directory. Warn under 2 GB, fail under 500 MB. Fix:
prune old run logs (`prune_run_logs`), prune old `system/` branches, or give
the volume more room.

### Check: docker

A docker daemon answers. Fail when agents are set to run in docker; warn when
`run_code` needs it (no `CODE_RUNNER_FALLBACK=local`); skip otherwise. Fix:
start Docker, or check that this process can reach its socket.

### Check: sandbox

Every sandbox provider ([sandboxes](sandboxes.md): docker, local), whether it
can run something right now, and why not (daemon down). Fail when the
resolved default provider cannot run at all. Fix: start Docker, or set
`CODE_RUNNER_FALLBACK=local`.

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

Related: [instances](instances.md), [sessions-and-runs](sessions-and-runs.md), [containers](containers.md), [web-logs](web-logs.md), [system-workspace](system-workspace.md).
