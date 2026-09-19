# Containers

Agents can run in-process or in Docker. Container mode isolates a run: its own
filesystem view, its own network posture, its own lifetime.

## Execution modes

Set per workspace. `local` runs agents in the server process; `docker` runs each
in a container.

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

Related: [nodes](nodes.md), [tools-and-capabilities](tools-and-capabilities.md), [settings](settings.md).
