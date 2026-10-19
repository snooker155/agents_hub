# Containers

Agents can run in-process or in Docker. Container mode isolates a run: its own
filesystem view, its own network posture, its own lifetime. Both surfaces that
run agents use it: **resident instances** (`instances/carrier.py`, the agent page's Run) and
one-shot **task runs** (`agents/agent_launcher.py`).

## Execution modes

Set per workspace, falling back to the global `AGENT_EXECUTION_MODE` setting,
resolved live on every instance start and every task run. `local` runs agents as a
subprocess of the backend; `docker` runs each in its own container.

## Two container profiles

Resident instances and runs share the same images and the same base mounts,
but a run container is additionally sandboxed: it is unattended and one-shot,
where a resident instance is something an operator is actively watching:

| | Instance container | Run container |
|---|---|---|
| Mounts | state dir, tasks dir, workspace (all read-write) | same, plus the registry snapshot (`run_snapshots/<run_id>/`: `agents.json`, `custom_providers.json`, `models.json`) mounted `:ro` inside the state dir and named in `AGENTS_HUB_SNAPSHOT_DIR` (and, with `AGENT_RUN_STATE_TRANSPORT=http`, the whole state dir `:ro` with `run_logs/` re-mounted `:rw`, see below) |
| Env | provider-key allowlist (`OPENAI_*`, `ANTHROPIC_*`, …) | full env minus a host-only denylist (`container_env`); a run needs its session id and workspace name too |
| Root filesystem | writable | `--read-only`, with `--tmpfs /tmp` for scratch space and `$HOME` |
| Resources | none | `--memory` (`AGENT_DOCKER_MEMORY`, default `2g`), `--cpus` (`AGENT_DOCKER_CPUS`, default `2`), `--pids-limit 512` |
| Capabilities | default | `--cap-drop ALL`, `--security-opt no-new-privileges` |

`managers/container_manager.build_run_command` builds the run profile's
command line as a pure function (no daemon needed), which is what
`tests/test_run_sandbox.py` asserts against.

When the hub itself runs in Docker Compose, the daemon resolves bind mounts on
the host, so the compose file sets `HOST_PROJECT_ROOT` to the checkout's host
path and `managers/container_manager.py` rebases the in-container state paths
onto it. Keep the state directory inside the checkout (or follow the comment in
`docker-compose.yml`), or the agent containers mount an empty directory.

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

Setting `AGENT_RUN_STATE_TRANSPORT=http` (env var, or the `run_state_transport`
setting; default `db`) closes that: `build_run_command` mounts the whole
state dir `:ro` instead, with `run_logs/` re-mounted read-write on top so the
run can still write its own log file directly. Everything else the run's own
entrypoint (`runtime/agent_run.py`) needs to write, opening and closing its
run record, the heavy process payload, persisting the task result, parking on
`ask_user`/tool approval, finalizing the task, goes instead through
`common/state_transport.py`'s `HttpStateTransport`, which posts to the
backend's `dashboard/backend/routes/run_state.py` (`/api/run-state/...`). Each route handler
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
same route family as above, whatever `AGENT_RUN_STATE_TRANSPORT` says), and
the backend creates the subtask and launches the delegate exactly as it
launches a run from the dashboard: in its own container with its own limits
and environment. Nothing is spawned inside the delegating container, whose
environment is forced to `AGENT_EXECUTION_MODE=local` and has neither the
Docker CLI nor the socket; before this the delegate ran there as a bare
subprocess sharing the parent's cgroup, network and read-only root, and
under the `http` transport could not be launched at all, since the subtask
row could not be written. While it waits, the tool polls
`GET /api/run-state/tasks/<id>/delegation` and reads its own parent's status
from `GET /api/run-state/runs/<id>`; a parent stopped meanwhile stops the
child through `POST /api/run-state/runs/<id>/stop`.

A container that cannot reach the backend cannot delegate: the tool answers
with `code: unreachable` and tells the agent to do the work itself, rather
than falling back to a nested subprocess.

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

## The Containers page

Lists running and stopped containers, reads their logs, stops and removes them.
A running container with an exposed `http_url` shows it as a link.

## Why it matters beyond isolation

The capability override for a blocked tool combination can be configured to be
honoured **only** for runs inside a container
(`CAPABILITY_OVERRIDE_REQUIRES_CONTAINER=true`). Containers are therefore not
just tidiness; they are the mechanism that makes an otherwise refused
combination survivable.

## Gotchas

- No Docker installed is not an error. Plenty of installs run everything
  in-process and have no containers at all.
- Stopping a container kills the run inside it. Read its logs first: a container
  looping on a fatal error is still telling you why.
- A delegate launched from a container is a sibling container, not a child
  process: look for it on the Containers page under its own run, and stop the
  parent's run (not its container) to stop the whole tree.
- A run record's `execution_mode` field can read `local` again moments after a
  Docker run starts: the container's own `agent_run.py` writes that field
  from its own environment, which is forced to `local` so an agent never tries
  to nest containers. `container_name`, set once by the launcher and never
  touched again, is the reliable signal for "this run lives in a container",
  not `execution_mode`; `managers/run_manager.py` and `managers/run_watchdog.py`
  key off it for that reason.

A shell inside a running run's container opens from the dashboard: see [terminal](terminal.md).

Related: [instances](instances.md), [tools-and-capabilities](tools-and-capabilities.md), [settings](settings.md), [terminal](terminal.md).
