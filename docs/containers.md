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
| Mounts | state dir, tasks dir, workspace (all read-write) | same, plus `agents.json` and `custom_providers.json` re-mounted `:ro` on top of the state dir |
| Env | provider-key allowlist (`OPENAI_*`, `ANTHROPIC_*`, …) | full env minus a host-only denylist (`container_env`) — a run needs its session id, workspace name and relay token too |
| Root filesystem | writable | `--read-only`, with `--tmpfs /tmp` for scratch space and `$HOME` |
| Resources | none | `--memory` (`AGENT_DOCKER_MEMORY`, default `2g`), `--cpus` (`AGENT_DOCKER_CPUS`, default `2`), `--pids-limit 512` |
| Capabilities | default | `--cap-drop ALL`, `--security-opt no-new-privileges` |

`managers/container_manager.build_run_command` builds the run profile's
command line as a pure function (no daemon needed), which is what
`tests/test_run_sandbox.py` asserts against.

**Remaining gap:** the shared SQLite database (`agents_hub.db`, holding run
records, tasks, sessions…) lives inside the read-write state-dir mount, so a
run container can reach it directly through `common/db.py`, the same as a
local subprocess does. Only `agents.json` and `custom_providers.json` are
pinned read-only on top. Closing this fully would mean giving runs no direct
filesystem access to the database at all — an HTTP-only run mode that reads
and writes run/task state through the backend's API instead of opening the
SQLite file itself — which is a larger change than wiring the sandbox and is
not done here.

## Logs in Docker mode

A local subprocess run has its stdout/stderr piped straight into the run's log
file by the launcher. A detached container has no such pipe: its own
`agent_run.py` records the log file path on the run (so the dashboard's Logs
tab has something to open) but does not tee its process output into it — that
only happens in the bare-CLI path, where `AGENT_LOG_FILE` is unset. Full run
output for a Docker task run is therefore in `docker logs <container_name>`,
not the log file, until a `--write-stdout-to-log`-style flag is added to
`runtime/agent_run.py` (deliberately not done here — see the note in
`agents/agent_launcher.py`'s `_start_run_in_docker`).

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
