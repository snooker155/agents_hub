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

A chat turn runs on an instance of the workspace's runner service
([services](services.md)), so in `docker` mode it runs in that instance's
container. One instance is one container.

A team, flow or loop run is one container for the whole run: its agent nodes
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

## No database in a container

An agent container never opens the hub's database, on SQLite or Postgres, and
the state directory is not mounted into it. Its process (a task run, an
instance's carrier, a team, flow, loop or scenario run) reaches its state
through the hub instead:

- Every store function such a process calls is marked with
  `common.remote_state.hub_call`. In the container (`AGENTS_HUB_REMOTE_STATE=1`)
  a marked function does not run there: the call goes to the hub as one
  `POST /api/run-state/call` with the container's run token, and the hub runs
  it and sends the result back.
- Each marked function names its scope: the workspace argument, or the record
  an id names (a task, run, instance, flow, chat, session, loop, team...),
  whose workspace the hub looks up. The hub compares that with the workspace
  the token was minted for and refuses anything else. A carrier changes only
  its own instance row and its own mailbox. Lists without a workspace argument
  come back filtered to the token's workspace.
- `common.db` refuses to open a database in such a process, so a store call
  nobody marked fails loudly (and is logged) instead of opening a file.
- The token is minted for every container, in `single` mode too: it is how the
  process reaches its state at all.

What the container gets (`managers/container_manager.remote_container_env`):
the run's own environment minus the database and broker URLs, the database
password, the key that decrypts the secrets table, the file store's
credentials and the hub-wide tokens; plus `AGENTS_HUB_REMOTE_STATE=1`, the run
token and `AGENTS_HUB_CALLBACK_URL`, the hub's address as the container sees it
(`container_hub_url`):

- `AGENTS_HUB_CONTAINER_HUB_URL` when set, for workers on another machine
  whose containers reach the API elsewhere;
- a hub in Docker (the compose stack): `http://agents-hub-api:8000`. Every
  backend replica joins the `agents-hub` network under that alias at start
  (`join_agent_network_as_hub`), so containers a worker starts reach the API
  too;
- a hub on the host: `http://host.docker.internal:8000`.

Mounted: a tmpfs as the state root, the log folders `run_logs/` and
`instance_logs/` read-write on top (the process writes its own log file), the
workspace folder at `/workspace`, and the registry snapshot read-only for a
task run.

No provider key reaches the container either (`common/llm_gateway.py`). Its
`OPENAI_API_KEY` and the other provider keys read `hub-gateway`, an agent's own
key reads `hub-agent-key:<agent>`, a custom provider's `hub-backend-key:<id>`,
and a workspace's keys come back from the hub as `hub-gateway` too. The model
drivers run unchanged and still call `api.openai.com`: in the container their
HTTP requests (httpx and requests) to the hosts in `AGENTS_HUB_LLM_HOSTS` go to
the hub instead, `/api/run-state/llm/<scheme>/<host>/<path>` with the run
token. The hub proxies only hosts it knows (the providers at their configured
base URLs, custom providers with a key, not the local network), puts in the
real key (the agent's, the custom provider's, the workspace's own, else the
hub's) and streams the answer back. Google models use the REST transport in a
container so the gateway carries them. Web search goes the same way: through
the model provider it searches on, or with `WEB_SEARCH_API_KEY` to Brave
(`X-Subscription-Token`), Exa (`x-api-key`) or Tavily (`api_key` in the JSON
body, put in by the hub). Answers reach the container decoded.

The browser service too: its token opens every workspace's sessions, so a
container holds the reference and the hub keeps it to its own workspace. A new
session is the run's workspace's, with a policy no looser than the
workspace's (an isolated workspace's own, its denied hosts added, its allow
list intersected); a listing names the run's workspace; a session named by id
must be one of it.

Tracing keys (Langfuse) and the OTLP export settings never reach a container:
a run's spans are exported on the hub, where the run is closed. Neither does
the host's deployment (`AGENTS_HUB_ROLE`, compose and setup variables). Left in
the container: its own workspace's connector secrets.

This is why docker mode works on Postgres and on Docker Desktop alike: SQLite's
locks never have to cross the Docker Desktop VM, and the database password
never reaches a container. `local` mode is the opposite: the process runs next
to the hub or on a worker and opens the database itself.

`tests/remote_harness.py` runs every entry point that way, as a real
subprocess without a database against a test hub, and
`AGENTS_HUB_REMOTE_STATE=audit` lists every direct access a process still
makes in one run (`REMOTE_AUDIT=<file>` in `tests/test_remote_*.py`).

### State on a Docker volume

A hub in Docker on a Mac can keep its own state on a Docker volume, which
lives on the VM's own disk.

    docker compose -f docker-compose.yml -f docker-compose.volume.yml up --build

`docker-compose.volume.yml` mounts the volume `agents_hub_state` at
`/data/agents_hub`, points `AGENTS_HUB_ROOT` there and names the volume in
`AGENTS_HUB_STATE_VOLUME`. Every path under the state dir then reaches an agent
container as `--mount type=volume,src=agents_hub_state,volume-subpath=...`
(`managers/container_manager.mount_flags`): the log folders of run and
instance containers, shell command containers, `run_code` snippets and
imported agents alike. A volume keeps real ownership, so those containers run
as the hub's user (`user_flags`).

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

## The container profile

Instances and runs get the same profile (`build_run_command`), unattended
either way:

| | Instance and run containers |
|---|---|
| Mounts | a tmpfs as the state root, `run_logs/` and `instance_logs/` read-write, the workspace at `/workspace`; a task run also its registry snapshot (`run_snapshots/<run_id>/`) read-only, named in `AGENTS_HUB_SNAPSHOT_DIR` |
| Env | the run's environment minus host-only variables (`container_env`) and everything that reaches the hub's state directly (`remote_container_env`) |
| Root filesystem | `--read-only`, with `--tmpfs /tmp` for scratch space and `$HOME` |
| Resources | `--memory` (`AGENT_DOCKER_MEMORY`, default `2g`), `--cpus` (`AGENT_DOCKER_CPUS`, default `2`), `--pids-limit 512`; an environment's limits replace them |
| Capabilities | `--cap-drop ALL`, `--security-opt no-new-privileges` |

`managers/container_manager.build_run_command` builds the command line as a
pure function (no daemon needed), which is what `tests/test_run_sandbox.py`
asserts against. A restart of an instance stops its container and starts a new
one, never `docker restart`: the old container would keep the profile,
environment and token it was started with.

## Run records over HTTP

A task run's own records (its run record, the heavy process payload, the task
result, parking on `ask_user` or a tool approval, finalizing the task) go
through `common/state_transport.py`'s `HttpStateTransport` to
`dashboard/backend/routes/run_state.py` (`/api/run-state/...`); in a container
`AGENT_RUN_STATE_TRANSPORT` is always `http`. Each route handler calls the
same `managers.run_manager` / `tasks.*` function a local run calls, including
`finalize_task_from_run`, so an auto-retry, an auto-started review or a
session continuation it triggers spawns from the hub, never from inside the
container. Everything else the process reads or writes goes through the
remote store calls above.

The agent registry, the custom provider list and the model catalog are also
written as JSON into `<state>/run_snapshots/<run_id>/` for a task run
(`common/snapshot.py`), mounted read-only, so the run reads a frozen copy of
its own definition. Snapshots of finished runs are removed by the maintenance
sweep.

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
Docker CLI nor the socket. While it waits, the tool polls
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
started with no network. A chat turn is built in an instance of the
workspace's runner, whose container needs the network to reach the hub, so
the same agent is refused there; the agent editor's hint answers for
background runs.

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
