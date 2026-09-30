# Troubleshooting

Symptoms, in the order people hit them. For the liveness of the moving parts see
[service-health](service-health.md); for a stack that never came up at all, the
last section of [installation](installation.md); for a symptom-confirm-fix
writeup of the dozen most common failures (plus how to build a support bundle
to attach to a ticket), see the [runbook](runbook.md).

## Nothing runs

**A run fails the moment it starts.** Almost always credentials or the catalog:
a missing provider key, a model that is not enabled on the
[models](models.md) page, or `AGENT_EXECUTION_MODE=docker` with no reachable
Docker daemon. An agent whose folder has an empty `instructions.md` fails the
same way.

**A run refuses to start, citing the budget.** The workspace has a hard limit
and the period's spend reached it. Raise or clear it on the Costs page (`0` is
unlimited) or wait for the daily or monthly period to roll over. Hard limits are
checked at launch, so nothing was half-run. See [costs](costs.md).

**A task was assigned but never started.** The run watchdog auto-starts those on
its next tick. If it does not, `GET /api/health` says whether the watchdog is
running at all.

**A flow, loop, team or scenario run stays in pending status.** In the `api` role, the
launch is queued for a worker. With no worker running (`ah worker`), the launch
waits in `pending` until one appears. Start a worker on the same or another host,
or use the `all` role (default) to launch runs in-process.

## It runs, but nothing appears

**Output appears only when the run finishes.** Live updates ride a single
`EventSource` on `/api/stream`. Check the connection indicator in the top bar,
that live streaming is on in Settings → System, and that no proxy in front of
the backend buffers server-sent events.

**An agent answers in text where a chart was expected.** Views come from the
visualization tools, and an agent only ever has the tools it was granted. Check
that `create_view` and the kind-specific tool it needs are bound to that agent.
The built-in `visualizer` ships with the set already bound. See
[views](views.md) and [tools-and-capabilities](tools-and-capabilities.md).

**A message written to an agent copy is never answered.** Open the copy on the
Instances page and read its state. A message to a **busy** instance waits in its
mailbox until the copy goes idle. A resident copy (started with Run) answers
in its own process: if that process is gone the watchdog marks the instance
stopped, and the next message starts it again. The Process tab of the
instance shows the carrier's status and log. See [instances](instances.md).

**A loop, team or scenario run was stopped and needs to resume.** `POST
/api/loops/runs/{id}/resume`, `POST /api/teams/runs/{id}/resume` or `POST
/api/playground/runs/{id}/resume` relaunches the run from its checkpoint (for
a loop, its position: the iteration after the last one it finished). A team or
scenario run record shows `has_checkpoint: true` when resumption is possible.
The endpoints return 400 when there is nothing to resume.

**A scheduled job never fired.** Jobs fire from a scheduler inside the backend,
so nothing fires while the backend is down. `GET /api/health` reports whether
the scheduler is alive, and the Plan page shows each job's status. See
[scheduling](scheduling.md).

## The numbers look wrong

**Costs show tokens but $0.00.** Spend is computed by joining runs against
catalog pricing, so a model with no price contributes tokens and zero dollars.
Set its prices per 1M tokens on the Models page; the numbers are derived at read
time, so the fix is retroactive.

**The model you want is missing from the picker.** The picker lists only models
marked enabled in the catalog. Run **Discover** for that provider, or add the
model id by hand, then enable it. Keys and base URLs live in Settings; the model
choice is made only on the [models](models.md) page.

## Docker

**Compose starts but agent-container features do not work.** Check, in order:
`AGENT_EXECUTION_MODE=docker` in `.env`; the `agents-hub/base` image built; and
`/var/run/docker.sock` still mounted into the backend service in
`docker-compose.yml`. Without the socket the backend has no daemon to talk to.
See [containers](containers.md). The single-user quickstart
(`deploy/quickstart/`, [installation](installation.md) Path A) mounts no
socket on purpose: agents there run as subprocesses of the backend.

**The quickstart backend restarts with `unable to open database file`.** The
volume was not handed to the backend's user: `docker compose up -d` runs the
`init` step again, and `docker compose logs init` says why it failed.

**The quickstart backend fails reading `.env`.** Docker created a folder of
that name because the file was missing at the first start. `docker compose
down`, `rmdir .env`, save `env.example` as `.env`, start again.

**Nothing embeds, RAG is off.** The image without the `-rag` suffix has no
embedding stack. Pin `AGENTS_HUB_TAG=X.Y.Z-rag` (quickstart) or build with
`WITH_RAG=true` (compose from a checkout), see [deployment](deployment.md),
"Releases".

## The CLI

**`ah` is not found.** The venv is not on PATH and the shell hook is not
installed. Run `ah shell-init --install` from the venv, or call the script by
its absolute path.

**The CLI cannot reach the service.** In direct mode it needs the repository,
its dependencies and the state directory where you run it; with `AGENTS_HUB_URL`
set it needs that backend up. `ah config` prints which mode is in use. See
[cli](cli.md).

**A path the CLI passes is refused.** Against a containerized backend, paths are
resolved by the backend, so a host path means nothing inside the container. Bind
mount it and pass the in-container path.
