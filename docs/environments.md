# Environments

An environment is a named execution profile: where a run happens (in-process
or in Docker), what image and packages it gets, what network it can reach,
what resource limits apply, and a few plain environment variables. Set one up
once, then point a task, a node or a scheduled job at it instead of repeating
the same settings everywhere.

## Scope and defaults

An environment belongs to one workspace, or to none, in which case every
workspace may use it. Names are unique within their scope: a workspace and the
global scope may both have a "Sandbox", but a workspace cannot have two.

At most one environment per scope is the default. A run that names no
environment gets its workspace's own default, else the global default, else
none at all, which means no fence beyond the workspace's own execution mode.

## Mode, image and packages

`mode` is `inherit` (the workspace's execution mode, `local` or `docker`),
`local`, or `docker`. Setting `docker` or `local` on an environment overrides
the workspace and the global setting for any run that uses it.

`image` is the base Docker image for a docker-mode environment; leaving it
unset uses the agent's own image. `packages` is a list of pip requirements
installed on top of that base image the first time the environment is used:
the hub builds a derived image, tagged `agents-hub-env:<hash>` where the hash
covers the base image and the sorted package list, so the same combination is
only ever built once and a later run reuses the cached tag. If the build
fails, the run falls back to the base image without the packages rather than
refusing to start; the failure is logged. The **Build image** action on the
Environments page (or `POST /api/environments/{id}/build`) builds it ahead of
time instead of on a run's first use, and answers `{ok, image, error}`. This
has nothing to do with a `local`-mode environment, which runs no container at
all.

## Sandbox provider

`sandbox_provider` picks which [sandbox](sandboxes.md) `run_code` and the
Chat code panel use for a run in this environment: `inherit` (the default:
`CODE_RUNNER_PROVIDER`, `docker` unless set otherwise, falling back to
`local` when docker is unavailable and `CODE_RUNNER_FALLBACK=local`),
`docker`, `local`, `e2b` or `modal`. This is separate from `mode` above,
which is where the *agent itself* runs; `sandbox_provider` is only where a
snippet the agent hands to `run_code` runs.

## Limits

`memory`, `cpus` and `pids_limit` on a docker-mode environment become the
container's `--memory`, `--cpus` and `--pids-limit`. Left unset, a run keeps
the run profile's own defaults.

## Network

Three types:

- **unrestricted**: nothing added; a run reaches whatever the host network
  allows.
- **limited**: the run is fenced to `allowed_hosts` (an exact host or a
  subdomain of one), plus the package registries (PyPI, npm) when
  `allow_package_managers` is on.
- **none**: the same fence with an empty host list, so nothing but the
  infrastructure hosts below stay reachable.

This is not Docker's `--network none`: cutting a docker-mode run off from the
network entirely would also cut it off from its own model provider, and an
LLM agent with no model to call cannot do anything. Three things enforce the
fence, from softest to hardest:

- The hub's own `tools/web.py` (fetch and search results) and `tools/browser.py`
  (navigation) read `AGENTS_HUB_NETWORK` and `AGENTS_HUB_ALLOWED_HOSTS` from
  the run's environment and refuse a host outside the list, with a clear
  message, every time regardless of whether the proxy below is on.
- The egress proxy, when enabled, holds every client that honours the
  `HTTP_PROXY`/`HTTPS_PROXY` variables (`requests`, `httpx`, `curl`, `pip`,
  `npm`, `git`) to the same allowlist.
- **When the egress proxy is enabled and the run is a docker-mode container**
  (a task run, or a `run_code`/code-view snippet through the `docker` sandbox
  provider), `managers/container_manager.py` fences the container itself: it
  joins an internal, no-route-out network (`agents-hub-egress`) instead of
  the ordinary `agents-hub` bridge, and reaches the egress proxy only through
  a small gateway container attached to both networks. A process that opens
  its own socket and ignores the proxy variables entirely now has nowhere to
  go: the internal network has no default route out, unlike the ordinary
  bridge. See [containers](containers.md) and [sandboxes](sandboxes.md) for
  how the gateway is set up, and the doctor's `sandbox` check for whether it
  is active.

**What this does not do without the egress proxy.** With the proxy off, a
docker-mode container stays on the ordinary `agents-hub` bridge (today's
behaviour, unchanged) and a process that opens sockets directly and ignores
the proxy variables is not stopped by anything at the network level; only the
hub's own web and browser tools hold regardless of the proxy.

### The egress proxy

Off by default. Enable it with `AGENTS_HUB_EGRESS_PROXY=1`; it listens on
`AGENTS_HUB_EGRESS_PROXY_PORT` (default `8099`), bound to
`AGENTS_HUB_EGRESS_PROXY_HOST` (127.0.0.1 in local mode, 0.0.0.0 automatically
when docker mode is in use so a container can reach it), and reported to a run
under `AGENTS_HUB_EGRESS_PROXY_PUBLIC_HOST` when neither default host fits. It
runs in the dashboard backend's `api` role and in every `worker` process, since
the proxy has to be reachable from wherever a run's own process actually
launches.

Each launch that needs the fence gets its own token
(`AGENTS_HUB_EGRESS_TOKEN_TTL` seconds, default 86400), carried as the
userinfo in the proxy URL the run is handed
(`http://<token>@host:port`). The token maps to that run's allowed hosts plus
the hosts of the configured model providers and the hub's own services (so a
`network: none` run can still reach its model). An unknown or expired token
gets `407`; a host outside the list gets `403`.

## Plain variables

`env` is a flat map of `NAME: value` added to the run's process environment.
Names the launcher, the run budget or the environment fence itself use
(`AGENTS_HUB_*`, `AGENT_*`, `DOCKER_*`, and a short list of exact names such
as `PATH` and `HTTP_PROXY`) are refused at save time, so an environment can
never override what carries the fence.

## Archiving and deletion

Archiving freezes a profile: it can no longer be edited, made the default, or
picked for a new task, node or job, but whatever is already running in it
keeps running, and a task that already names it still launches with its
fence. Dropping the fence of a profile someone archived would be the wrong
way to fail.

Deleting is refused (409) while any active node or pending scheduled job
still references the environment; archive it, or point those at something
else first, then delete.

## Where it applies

- **A task**: `task.environment_id`, resolved at launch time by
  `environments/launch.py` into environment variables for the child process
  and, in docker mode, container options (`runtime/docker_runner.py`,
  `managers/container_manager.py`). An id that no longer exists falls back to
  the workspace's default with a warning, rather than refusing the run.
- **A node**: `POST /api/nodes` accepts `environment_id`; node records carry
  it and its name (`managers/node_manager.start_node`).
- **A scheduled job**: an `environment_id` on the job is copied onto every
  task the job creates, whatever kind of job it is (see
  [deployments](deployments.md) and [scheduling](scheduling.md)).

## The Environments page

`/environments`, under Infrastructure. A table of environments for the
current workspace (name, scope, mode, network type, limits, default and
archived badges, usage counts), a create/edit form, and row actions: make
default, archive, delete (disabled with a tooltip while something uses it),
build the derived image, and a usage drawer listing the nodes, scheduled jobs
and recent runs tied to it, each linking back to its own page.

## Gotchas

- `network: none` is not offline. It means the hub's own tools and, when the
  proxy is on, everything that honours proxy variables are held to the
  infrastructure hosts only. A tool or a subprocess that opens its own socket
  is not affected by either.
- A `docker`-mode environment with packages set builds an image on first use;
  that first run is slower than the rest. Use **Build image** ahead of time
  if the delay would land on someone waiting.
- Archiving is not deleting. An archived environment keeps applying to the
  tasks that already name it; it just cannot be picked again.
- The egress proxy must run in the same process (or on the same host, for
  worker mode) that launched the run, since the URL a run is handed names
  `127.0.0.1` or `host.docker.internal`, not a shared address.

Related: [sandboxes](sandboxes.md), [containers](containers.md), [nodes](nodes.md), [deployments](deployments.md), [scheduling](scheduling.md), [service-health](service-health.md).
