# Troubleshooting

Symptoms, in the order people hit them. For the liveness of the moving parts see
[service-health](service-health.md); for a stack that never came up at all, the
last section of [installation](installation.md).

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
mailbox until the copy goes idle. A copy carried by a node or a container
answers in its own process, so nothing happens if that process is gone; the
watchdog reconciles instances whose carrier died. See [instances](instances.md).

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
See [containers](containers.md).

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
