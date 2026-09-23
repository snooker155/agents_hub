# Containers

Agents can run in-process or in Docker. Container mode isolates a run: its own
filesystem view, its own network posture, its own lifetime. Both surfaces that
run agents use it: persistent **nodes** (`managers/node_manager.py`) and
one-shot **task runs** (`agents/agent_launcher.py`).

## Execution modes

Set per workspace, falling back to the global `AGENT_EXECUTION_MODE` setting,
resolved live on every node start and every task run. `local` runs agents as a
subprocess of the backend; `docker` runs each in its own container.

## Two container profiles

Nodes and runs share the same images and the same base mounts, but a run
container is additionally sandboxed — it is unattended and one-shot, where a
node is something an operator is actively watching:

| | Node container | Run container |
|---|---|---|
| Mounts | state dir, tasks dir, workspace (all read-write) | same, plus `agents.json` and `custom_providers.json` re-mounted `:ro` on top of the state dir (and, with `AGENT_RUN_STATE_TRANSPORT=http`, the whole state dir `:ro` with `run_logs/` re-mounted `:rw`, see below) |
| Env | provider-key allowlist (`OPENAI_*`, `ANTHROPIC_*`, …) | full env minus a host-only denylist (`container_env`) — a run needs its session id, workspace name and relay token too |
| Root filesystem | writable | `--read-only`, with `--tmpfs /tmp` for scratch space and `$HOME` |
| Resources | none | `--memory` (`AGENT_DOCKER_MEMORY`, default `2g`), `--cpus` (`AGENT_DOCKER_CPUS`, default `2`), `--pids-limit 512` |
| Capabilities | default | `--cap-drop ALL`, `--security-opt no-new-privileges` |

`managers/container_manager.build_run_command` builds the run profile's
command line as a pure function (no daemon needed), which is what
`tests/test_run_sandbox.py` asserts against.

## HTTP-only state transport

By default a run reaches the shared database (`agents_hub.db`, holding run
records, tasks, sessions…) directly through `common/db.py`, the same as a
local subprocess does: the state dir mount is read-write, and only
`agents.json` / `custom_providers.json` are pinned `:ro` on top.

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

## Logs in Docker mode

A local subprocess run has its stdout/stderr piped straight into the run's log
file by the launcher. A detached container has no such pipe, so its own
`agent_run.py` is passed `--write-stdout-to-log` (alongside `--log-file`'s
container-mounted path, `AGENT_LOG_FILE`) for the inner command
`_start_run_in_docker` builds: it tees stdout/stderr into that file itself,
in append mode so the header the launcher wrote before starting the container
survives. The run's log file therefore carries full output the same way a
local subprocess run's does from its Popen pipe; `docker logs
<container_name>` still works but is no longer the only place to see it.

## Images

- A **shared base image** is built once and holds the runtime and dependencies.
- A **per-agent image** is built on top of it, so rebuilding one agent does not
  rebuild the world.

The generated Dockerfile for any agent can be previewed before building, which
is the fastest way to see what an agent will actually have available.

## The Containers page

Lists running and stopped containers, reads their logs, stops and removes them.

## Why it matters beyond isolation

The capability override for a blocked tool combination can be configured to be
honoured **only** for container-isolated, network-free runs. Containers are
therefore not just tidiness; they are the mechanism that makes an otherwise
refused combination survivable.

## Gotchas

- No Docker installed is not an error. Plenty of installs run everything
  in-process and have no containers at all.
- Stopping a container kills the run inside it. Read its logs first: a container
  looping on a fatal error is still telling you why.
- A run record's `execution_mode` field can read `local` again moments after a
  Docker run starts — the container's own `agent_run.py` writes that field
  from its own environment, which is forced to `local` so an agent never tries
  to nest containers. `container_name`, set once by the launcher and never
  touched again, is the reliable signal for "this run lives in a container",
  not `execution_mode`; `managers/run_manager.py` and `managers/run_watchdog.py`
  key off it for that reason.

Related: [nodes](nodes.md), [tools-and-capabilities](tools-and-capabilities.md), [settings](settings.md).
