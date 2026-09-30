# Runbook: typical failures

Symptom, how to confirm it, and the fix, for the failures an operator hits
most often. For the broader symptom list see [troubleshooting](troubleshooting.md);
for the moving parts' own liveness see [service-health](service-health.md); for
the SLO numbers these failures usually move see [slo](slo.md).

Every entry below is also worth attaching a [support bundle](#support-bundle)
to when you file it: `ah support-bundle` or the Health page's **Download
support bundle** button collects everything here in one zip, secrets
scrubbed, so nobody has to paste logs by hand.

## Provider key invalid or rate limited

**Symptom.** A run fails immediately with an authentication or rate-limit
error from the provider; `ah doctor`'s `provider` check fails or warns.

**Confirm.** `GET /api/health/doctor` (or `ah doctor`), check **provider**:
it probes the default provider's model listing with the configured key and
reports the HTTP status back. The run's own `error` field usually already
names it (401/403 for a bad key, 429 for a rate limit).

**Fix.** Set the correct key on the Settings page (the model and
default-provider choice is made on the [models](models.md) page instead; the
key and base URL are Settings-only). For a rate limit, wait it out or switch
the workspace's default model to a less loaded one; nothing was half-run,
since the check happens before a run starts spending tokens.

## Model not enabled

**Symptom.** A run refuses to start, or the model picker does not offer the
model you expect.

**Confirm.** The [models](models.md) page's catalog: a model must be marked
enabled to be launchable, whether or not the provider actually serves it.

**Fix.** Run **Discover** for the provider, or add the model id by hand,
then enable it.

## Docker daemon missing

**Symptom.** A container-mode run fails to start; `ah doctor`'s `docker` or
`sandbox` check fails.

**Confirm.** `ah doctor`: **docker** fails when `AGENT_EXECUTION_MODE=docker`
and no daemon answers; **sandbox** fails when the resolved default sandbox
provider cannot run at all.

**Fix.** Start Docker, or check that this process can reach
`/var/run/docker.sock` (see [containers](containers.md)); or set
`AGENT_EXECUTION_MODE=local` / `CODE_RUNNER_FALLBACK=local` to run without a
daemon.

## Worker not running: runs stay pending

**Symptom.** A flow, loop, team or scenario run (or a task assigned to an
agent) sits in `pending` and never moves.

**Confirm.** `ah doctor`'s **run_queue** check: warns when more than 10
launches wait, or one has waited over 5 minutes, with no worker alive. In
the `api`/`worker` role split (docs/workers.md) a launch queues for a worker
to claim; with `AGENTS_HUB_ROLE=all` (the default single-process shape) there
is no queue to get stuck behind, so this usually means the deployment is
split and the worker process died.

**Fix.** Start a worker (`ah worker`), or run the backend in the `all` role.
Also see the [SLO](slo.md) run-start objective: a stuck queue is exactly what
drives `agents_hub_run_start_seconds` over its threshold.

## Database locked or Postgres unreachable

**Symptom.** Requests fail with a database error; `/readyz` returns 503.

**Confirm.** `GET /readyz` names the failing check (`database: false`).
SQLite: another process holding a long write transaction, or the state
directory on a network filesystem that does not support its locking.
Postgres: `AGENTS_HUB_DATABASE_URL` points at a host that refused the
connection, or the connection pool is exhausted.

**Fix.** SQLite: move the state directory to local disk
(docs/scaling.md covers when to move off SQLite entirely); check nothing
else has the file open. Postgres: check the URL, that the database accepts
connections from this host, and its `max_connections` against the number of
backend + worker replicas.

## Migrations failed at start

**Symptom.** The backend does not come up; the log shows a migration error.
`ah doctor`'s **migrations** check fails once it does come up.

**Confirm.** `common/migrations/` applies every pending version inside one
transaction on start (`common.db._ensure_ready`); a failure there means one
statement in a numbered migration could not run against this database's
actual state.

**Fix.** Read the migration's own file for what it assumes; a hand-edited
schema or a very old pre-baseline database is the usual cause. `ah db
status` shows the ledger; `ah db backup` before hand-fixing anything. On
SQLite the database as it was before this start is already archived in
`.agents_hub/backups/pre-migrate_*.tar.gz`; going back to the previous
release restores it ([deployment](deployment.md#rolling-back)).

The same start can also fail with "written by a newer version of the app":
this build is older than the one that last migrated the database, usually
after a rollback of the code without a restore of the data. Either run the
newer release again, or restore the pre-migrate archive as the rollback
procedure describes.

## Disk full from run logs

**Symptom.** Runs fail to write their log; `ah doctor`'s **disk** check
warns (under 2 GB free) or fails (under 500 MB).

**Confirm.** `storage.run_logs_bytes` / `run_logs_files` on `GET /api/health`
or in a support bundle's `health.json`.

**Fix.** Prune old run logs (the daily maintenance sweep does this on its
own past the retention window; force it sooner with the maintenance
function, or delete old files under `run_logs/` directly), prune old
`system/` branches (`POST /api/system/prune`), or give the volume more room.

## Budget refusal

**Symptom.** A run refuses to start, citing the budget.

**Confirm.** The workspace has a hard spend limit for the period
(daily/monthly) and the period's spend already reached it
([costs](costs.md)).

**Fix.** Raise or clear the limit on the Costs page (`0` is unlimited), or
wait for the period to roll over. This is checked at launch, before any
tokens are spent, so nothing was half-run.

## Stuck lease

**Symptom.** A singleton role (scheduler, watchdog, outbox) looks like
nobody is running it, but a process that should hold it is up.

**Confirm.** `GET /readyz`'s `singletons` block, or `agents_hub_lease_held`
on `/metrics`: `false` means the lease has lapsed, not necessarily that no
process wants it. A lease is time-bounded and renewed on a heartbeat, so a
process that stalled (GC pause, a blocking call) for longer than the TTL
drops it and another replica should pick it up on its next tick.

**Fix.** Usually self-heals within one tick interval once the stalled
process recovers or another replica claims the role. If it does not, restart
the stalled process; `common/leases.py` is content to hand the role to
whoever asks next.

## MCP server failing handshake

**Symptom.** An MCP server attached to a workspace shows no tools, or a run
using it fails immediately.

**Confirm.** The MCP page's server list shows the last connection error; a
stdio server that cannot start its command or an http server that refuses
the handshake both surface there.

**Fix.** Check the command/args or URL, and any headers or env values the
server needs (see [mcp](mcp.md)); test it again from the page after fixing.

## SSE buffered by a proxy

**Symptom.** Output for a run appears only once it finishes, never live,
even though the connection indicator shows connected.

**Confirm.** Live updates ride one `EventSource` on `/api/stream`. A proxy
in front of the backend (nginx, a load balancer) that buffers responses will
hold every chunk until the stream closes.

**Fix.** Turn off proxy buffering for that path (`proxy_buffering off;` on
nginx, or the load balancer's equivalent), and confirm streaming is on in
Settings → System.

## Widget CORS refusal

**Symptom.** The embeddable chat widget fails to load or to send a message
from the site it is embedded on; the browser console shows a CORS error.

**Confirm.** A widget only answers requests from the origins listed in its
own `allowed_origins` (docs/widget.md), which is a narrower list than the
dashboard's own `ALLOW_ORIGINS`.

**Fix.** Add the embedding site's exact origin to the widget's allowed
origins on the Widgets page.

## Support bundle

`ah support-bundle [--out PATH] [--since 24h]` (or the Health page's
**Download support bundle** button, admin only) writes one zip:
`version.json`, `doctor.json`, `health.json`, `migrations.json`,
`config.json` (secrets reduced to "is it set", never the value),
`errors.json` (failed runs in the window), `slo.json`, and a tail of each
running process's own log under `logs/`. A single scrubber function
(`common.support_bundle.scrub`) runs over every file before it enters the
zip, catching common key shapes (`sk-...`, `ahk_...`, `Bearer ...`,
`gh?_...`) and any `scheme://user:pass@host` URL, so a database URL or a git
remote with an embedded token comes out the other side with its credentials
masked.

Works without a running backend (direct mode, same state the CLI reads for
everything else) or against one over the network
(`AGENTS_HUB_URL=https://your-hub ah support-bundle`, which downloads `GET
/api/support/bundle`).
