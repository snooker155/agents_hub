# Containers

Agents can run in-process or in Docker. Container mode isolates a run: its own
filesystem view, its own network posture, its own lifetime. Every background
surface uses it: **resident instances** (`instances/carrier.py`, the agent page's Run),
one-shot **task runs** (`agents/agent_launcher.py`), and **team, flow and loop
runs** (`runtime/entity_launch.py`).

## Execution modes

Set per workspace, falling back to the global `AGENT_EXECUTION_MODE` setting,
resolved live on every instance start and every task run. `local` runs agents as a
subprocess of the backend; `docker` runs each in its own container.

Set per workspace means `settings.agent_mode` on the workspace, read by
`common.config.agent_execution_mode(workspace)` for task runs, instances,
teams and `run_shell` alike.

A chat turn is not one of them: it always runs in the hub process (flows and
teams started from chat too), whatever the mode says.

A team, flow or loop run (where a container may share the database, see
below) is one container for the whole run: its agent nodes
run inside the run's process, so they share that container and the base image
(a per-agent image applies to task runs and instances only). The workspace
folder is mounted at `/workspace`; a run with no workspace gets no mount,
never the project root. A nested team, loop or flow the run starts is a
subprocess in the same container: inside an agent container the mode always
reads `local`, so nothing tries to start docker from there. Stopping a flow
stops its container. In `docker` mode its
`run_shell` commands still go into a container: a throwaway one per command
(`managers/container_manager.build_shell_command`), on the agent's image and
network, with the run profile's limits below and only the command's working
directory mounted read-write at `/workspace`. No hub environment and no state
directory reach it. Docker down means the command is refused, never run on the
host. The rest of the turn (file tools, web, MCP) runs with the hub's rights.

## SQLite and Docker Desktop

A container may open the hub's SQLite database through the state mount only on
a Linux host whose state directory sits on a local filesystem
(`managers/container_manager.db_shared_with_containers`, which reads the
filesystem type from `/proc/mounts`). Docker Desktop on macOS or Windows
reaches a host folder through a shared-folder filesystem (virtiofs, seen as
`fakeowner` inside a container) that carries neither SQLite's locks nor its
shared memory. A container that closes the database there believes it was the
last user, folds the WAL into the file and deletes it under every other user;
they keep writing to the deleted file, and everything they wrote is lost.

Measured on Docker Desktop for Mac: a host process and a container lose the
WAL, and so do two containers on the same macOS folder; two containers on a
Docker volume do not. A hub in Docker on a Mac keeps its state in such a
folder by default (`docker-compose.yml` bind mounts the checkout), so running
the hub in Docker helps only with the state on a volume, below.

### State on a Docker volume

The way around it on Docker Desktop: run the hub in Docker with its state on a
Docker volume, which lives on the VM's own disk.

    docker compose -f docker-compose.yml -f docker-compose.volume.yml up --build

`docker-compose.volume.yml` mounts the volume `agents_hub_state` at
`/data/agents_hub`, points `AGENTS_HUB_ROOT` there and names the volume in
`AGENTS_HUB_STATE_VOLUME`. Every path under the state dir then reaches an agent
container as `--mount type=volume,src=agents_hub_state,volume-subpath=...`
(`managers/container_manager.mount_flags`): run and instance containers, shell
command containers, `run_code` snippets and imported agents alike. A volume
keeps real ownership, so those containers run as the hub's user
(`user_flags`). Inside the hub container the state reads as `ext4`, so
`db_shared_with_containers` allows containers.

The volume and the checkout's `.agents_hub` follow each other
(`common/state_sync.py`, on when `AGENTS_HUB_STATE_SYNC_DIR` names the folder,
as `docker-compose.volume.yml` does):

- at start, before the backend opens the database, the folder is copied into
  the volume when a hub on the host changed it since the last sync and this
  hub stopped cleanly last time (so the volume holds nothing unexported);
- at stop, the backend stops its agent containers and copies the volume to the
  folder, keeping the previous folder as `.agents_hub.prev`. The compose file
  gives it `stop_grace_period: 180s`.

When both sides changed since the last sync (a hub on the host ran at the same
time), nothing is copied either way and the backend log says so. The very
first sync with a database on both sides needs a choice too. Settle either
inside the hub's container: `docker compose exec backend ah state export
--force` (the volume wins), or with the hub stopped `docker compose run --rm
backend ah state import --force` (the folder wins); `ah state status` there
shows the last sync. The backend prints all of this at every start, and the
setup wizard says it where it asks where agents run.

The folder's database is never opened while a `-wal` or `-shm` file sits next
to it: a hub on the host has it open, or stopped without closing it (a
read-only `sqlite3` session leaves them too). The sync then skips and says
why. The database is copied with SQLite's backup API, never as a file.

Without the volume, so on Docker Desktop, every hub process that opens
the database (task runs, resident instances, teams, flows, loops) runs locally
whatever the mode says (`common.config.run_execution_mode`). Only shell
commands (`run_shell`, and `run_code` as always) go into a container: theirs
never sees the database. The Settings page says so under the docker mode
switch.

The `http` state transport does not lift this: it relays a run's own records,
but building an agent reads the registry and the task store from the database
everywhere, so a run with the state dir mounted read-only fails at start. A
container started anyway (a spec from elsewhere) gets `http` forced
(`container_state_transport`), so the worst it does is fail, never write.

A run in a container records its log under `/app/.agents_hub/...`; run records
are read back with that path rebased onto the hub's state dir
(`common.paths.host_state_path`), so the dashboard opens it.

## Network

Agent containers join `AGENT_DOCKER_NETWORK` when it is set (`none` for no
network at all, `host`, or a network of your own), else the `agents-hub`
bridge. Every agent container is stamped with the network it got in
`AGENTS_HUB_RUN_NETWORK`: the process inside reads that, not the setting, to
know how it is fenced. A run with network `none` cannot reach a model provider
either, so it suits only models it can reach without one.

## Two container profiles

Resident instances and runs share the same images and the same base mounts,
but a run container is additionally sandboxed — it is unattended and one-shot,
where a resident instance is something an operator is actively watching:

| | Instance container | Run container |
|---|---|---|
| Mounts | state dir, tasks dir, workspace (all read-write) | same, plus the registry snapshot (`run_snapshots/<run_id>/`: `agents.json`, `custom_providers.json`, `models.json`) mounted `:ro` inside the state dir and named in `AGENTS_HUB_SNAPSHOT_DIR` (and, with `AGENT_RUN_STATE_TRANSPORT=http`, the whole state dir `:ro` with `run_logs/` re-mounted `:rw`, see below) |
| Env | provider-key allowlist (`OPENAI_*`, `ANTHROPIC_*`, …) | full env minus a host-only denylist (`container_env`) — a run needs its session id, workspace name and run token too (the hub-wide tokens are never in it) |
| Root filesystem | writable | `--read-only`, with `--tmpfs /tmp` for scratch space and `$HOME` |
| Resources | none | `--memory` (`AGENT_DOCKER_MEMORY`, default `2g`), `--cpus` (`AGENT_DOCKER_CPUS`, default `2`), `--pids-limit 512` |
| Capabilities | default | `--cap-drop ALL`, `--security-opt no-new-privileges` |

`managers/container_manager.build_run_command` builds the run profile's
command line as a pure function (no daemon needed), which is what
`tests/test_run_sandbox.py` asserts against.

## HTTP-only state transport

By default a run reaches the shared database (`agents_hub.db`, holding run
records, tasks, sessions…) directly through `common/db.py`, the same as a
local subprocess does: the state dir mount is read-write, and only the
registry snapshot is pinned `:ro` on top.

The agent registry, the custom provider list and the model catalog live in
the database, and a container is not handed the database just to read
them: before the container starts, the launcher writes the three as JSON
into `<state>/run_snapshots/<run_id>/` (`common/snapshot.py`,
`write_snapshots`), mounts that directory read-only and points
`AGENTS_HUB_SNAPSHOT_DIR` at it. Inside the container every registry
serves the snapshot and refuses writes, so a run reads a frozen copy of its
own definition and cannot change what the capability guard will allow it.
A resident instance's container gets the same under its own snapshot
directory; a registry edit reaches a running instance on its next restart. Snapshots of finished runs
are removed by the maintenance sweep.

Setting `AGENT_RUN_STATE_TRANSPORT=http` (env var, or `run_state_transport` in
Settings; default `db` on SQLite, `http` when `AGENTS_HUB_DATABASE_URL` names
a Postgres database, so a container is never handed the database password
just to update its own record; see docs/scaling.md) closes that: `build_run_command` mounts the whole
state dir `:ro` instead, with `run_logs/` re-mounted read-write on top so the
run can still write its own log file directly. Everything else the run's own
entrypoint (`runtime/agent_run.py`) needs to write, opening and closing its
run record, the heavy process payload, persisting the task result, parking on
`ask_user`/tool approval, finalizing the task, goes instead through
`common/state_transport.py`'s `HttpStateTransport`, which posts to the
backend's `dashboard/backend/routes/run_state.py` (`/api/run-state/...`,
authenticated the same way `agents/callbacks/streaming.py`'s relay is:
`AGENTS_HUB_API_TOKEN` via `common.auth.auth_headers()`). Each route handler
calls the exact same `managers.run_manager` / `tasks.*` function the direct
path calls, including `finalize_task_from_run`, so an auto-retry, an
auto-started review or a session continuation it triggers spawns from the
backend process, never from inside the run container. Host/port resolution
mirrors that same relay: `DASHBOARD_PORT` (default `8000`) on `localhost`,
rewritten to `host.docker.internal` by `common.hostnet.host_service_url` when
the caller is itself containerized.

Left outside this transport, so a run still needs *some* writable path to the
state dir for them: the log tee below (writes the file directly, no HTTP
equivalent), and anything a tool does through memory pools or session storage.
Those are unaffected by this setting and keep reading/writing `.agents_hub`
directly, mounted read-only or not; a tool that touches them inside an
`http`-transport run still needs the state dir writable for that path, which
this mode does not provide. `db` (the default) remains the fully-capable mode.

## Delegation from a container

`delegate_task_tool` (docs/tasks.md) decides *what* to hand over inside the
run that delegates; *where* the subtask is created and the delegate launched
is the state transport's business (`tasks/delegate.py`, reached through
`common/state_transport.py`). A run that is a host subprocess does both in
its own process, as it always did. A run in a container instead posts the
request to the backend (`POST /api/run-state/tasks/<id>/delegate`, the same
relay and token as above, whatever `AGENT_RUN_STATE_TRANSPORT` says), and
the backend creates the subtask and launches the delegate exactly as it
launches a run from the dashboard: in its own container with its own limits
and environment, or onto the run queue for a worker in the `api` role
(docs/workers.md). Nothing is spawned inside the delegating container, whose
environment is forced to `AGENT_EXECUTION_MODE=local` and has neither the
Docker CLI nor the socket; before this the delegate ran there as a bare
subprocess sharing the parent's cgroup, network and read-only root, and
under the `http` transport could not be launched at all, since the subtask
row could not be written. While it waits, the tool polls
`GET /api/run-state/tasks/<id>/delegation` and reads its own parent's status
from `GET /api/run-state/runs/<id>`; a parent stopped meanwhile stops the
child through `POST /api/run-state/runs/<id>/stop`. The child is attributed
to the user the parent run was launched by, as an in-process delegation
attributes it.

A container whose environment allows no network (`network: none`,
docs/environments.md) cannot reach the backend and therefore cannot
delegate: the tool answers with `code: unreachable` and tells the agent to do
the work itself, rather than falling back to a nested subprocess.

## Logs in Docker mode

A local subprocess run has its stdout/stderr piped straight into the run's log
file by the launcher. A detached container has no such pipe, so its own
`agent_run.py` is passed `--write-stdout-to-log` (alongside `--log-file`'s
container-mounted path, `AGENT_LOG_FILE`) for the inner command
`_start_run_in_docker` builds: it tees stdout/stderr into that file itself,
in append mode so the header the launcher wrote before starting the container
survives. The run's log file therefore carries full output the same way a
local subprocess run's does from its Popen pipe;
`docker logs <container_name>` still works but is no longer the only place
to see it.

## Images

- A **shared base image** is built once and holds the runtime and dependencies.
- A **per-agent image** is built on top of it, so rebuilding one agent does not
  rebuild the world.

The generated Dockerfile for any agent can be previewed before building, which
is the fastest way to see what an agent will actually have available.

## Environments: image, limits, network

A docker-mode [environment](environments.md) shapes a run container further,
on top of the two profiles above. `managers/container_manager.options_to_kwargs`
turns its docker options into `start_container`/`build_run_command`
arguments:

- **image**: the environment's own base image, or the agent's own when unset.
- **packages**: pip requirements installed on top of that base image the
  first time the combination is used, into a derived image tagged
  `agents-hub-env:<hash>` (`ensure_environment_image`, cached by a hash of the
  base image and the sorted package list) so the same environment only ever
  builds once. A failed build falls back to running on the base image without
  the packages, logged, rather than refusing the run.
- **limits**: `memory`, `cpus` and `pids_limit` override the run profile's
  own defaults (`AGENT_DOCKER_MEMORY`, `AGENT_DOCKER_CPUS`, the fixed
  pids-limit) when the environment sets them.

**Network** is not one of these container arguments. Every environment keeps
the container on the normal agents-hub bridge, whatever its `network` type,
because Docker's `--network none` would also cut the agent off from its own
model provider. A `limited` or `none` environment is enforced instead by the
hub's own web and browser tools reading `AGENTS_HUB_NETWORK` /
`AGENTS_HUB_ALLOWED_HOSTS` from the container's environment, and, when the
egress proxy is enabled (`AGENTS_HUB_EGRESS_PROXY=1`), by every client inside
the container that honours the `HTTP_PROXY`/`HTTPS_PROXY` variables. See
[environments](environments.md) for the full network model.

## The Containers page

Lists running and stopped containers, reads their logs, stops and removes them.
A running container with an exposed `http_url` gets a **Preview** button; see
below.

## Preview through the hub

A project's frontend and a [project deployment](project-deployments.md)'s
service open inside the hub, through an authenticated proxy:
`POST /api/preview/tickets` mints a short-lived, signed ticket
(`common/preview_tickets.py`) for `{kind: "project", project_id}` or
`{kind: "deployment", deployment_id, service?}`, and the page loads in an
iframe pointed at `/preview/<ticket>/`, a path outside `/api` where the
ticket itself is the credential (`dashboard/backend/routes/preview.py`).
An agent container has no page of its own to preview: agents open no port
(see [instances](instances.md), "Publishing"), so there is no container
kind.

Why a ticket rather than a direct iframe: an iframe navigation cannot carry
the `Authorization: Bearer` header the rest of the API uses, and the iframe
runs with `sandbox="allow-scripts allow-forms allow-popups allow-modals"`,
deliberately without `allow-same-origin`, so the previewed page executes in
an opaque origin and cannot read the dashboard's own tokens out of
`localStorage`. The ticket is re-resolved to the target's live address on
every proxied request, not baked in at mint time, so it survives the target
being restarted (a new port) for as long as the ticket itself has not
expired; it binds the user who minted it, so in `AUTH_MODE=multi` a
revoked account's outstanding tickets stop working with it.

A ticket lives ten minutes. While the preview is open and the tab visible, the
dashboard checks every four minutes whether the current ticket would run out
before the next check, and only then calls `POST /api/preview/tickets/renew`
(body `{ticket}`, or the mint body once the old one has lapsed) and points the
iframe at the new ticket. The ticket is only checked when the frame makes a
request, so this reloads the page about once per ticket lifetime rather than
on every renewal. The renewal re-checks the target and the caller's access and
renews only the caller's own ticket. A client that can read response headers
gets the same from the proxy: a request served on a ticket past half its life
carries a successor in `X-Preview-Ticket`. A preview left in a hidden tab past
its ten minutes shows the expired page, which asks the dashboard for a fresh
ticket.

The proxy rewrites an HTML response's absolute-path URLs (`href="/`, `src="/`,
`action="/`) to stay under the ticket prefix and injects a `<base>` tag when
the page has none, by a plain regex over the markup, not a parser: an
absolute-path URL built at runtime by JavaScript is the known limitation, and
is never rewritten inside a `<script>` block on purpose. A same-origin
redirect is rewritten to stay under the ticket too; a redirect elsewhere is
left alone and simply leaves the frame. See docs/projects.md for the same
mechanism applied to a project's frontend.

## Why it matters beyond isolation

The capability override for a blocked tool combination can be configured to be
honoured **only** for container-isolated, network-free runs. Containers are
therefore not just tidiness; they are the mechanism that makes an otherwise
refused combination survivable.

The build check honours it only in a process stamped
`AGENTS_HUB_RUN_NETWORK=none`, that is a background run whose container was
started with no network. A chat turn is built on the hub, so the same agent is
refused there even with `docker` mode and `AGENT_DOCKER_NETWORK=none`; the
agent editor's hint answers for background runs.

## Gotchas

- No Docker installed is not an error. Plenty of installs run everything
  in-process and have no containers at all.
- Stopping a container kills the run inside it. Read its logs first: a container
  looping on a fatal error is still telling you why.
- A delegate launched from a container is a sibling container, not a child
  process: look for it on the Containers page under its own run, and stop the
  parent's run (not its container) to stop the whole tree.
- A run record's `execution_mode` field can read `local` again moments after a
  Docker run starts — the container's own `agent_run.py` writes that field
  from its own environment, which is forced to `local` so an agent never tries
  to nest containers. `container_name`, set once by the launcher and never
  touched again, is the reliable signal for "this run lives in a container",
  not `execution_mode`; `managers/run_manager.py` and `managers/run_watchdog.py`
  key off it for that reason.

A shell inside a running run's container, or a service instance's, opens from the dashboard: see [terminal](terminal.md).

Related: [instances](instances.md), [tools-and-capabilities](tools-and-capabilities.md), [settings](settings.md), [environments](environments.md), [terminal](terminal.md).
