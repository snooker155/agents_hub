# Sandboxes

A sandbox runs one snippet of code (from the `run_code` tool, or the Chat
code panel) to completion, somewhere isolated from the hub's own host and
the agent's own working directory. `sandbox/` is the abstraction:
`sandbox/base.py` defines the request/result shape every provider takes and
returns, `sandbox/registry.py` picks a provider by name, and two modules
implement one each: `docker.py`, `local.py`.

## Choosing a provider

`sandbox.registry.resolve(settings)`:

1. `CODE_RUNNER_PROVIDER` (default `docker`).
2. Else, when that provider cannot run right now and
   `CODE_RUNNER_FALLBACK=local` is set, `local`: the same rule `run_code`
   always applied when docker was unavailable, now expressed as a provider
   choice.

The resolved name is not itself a guarantee of availability: `run_code`'s
output says which provider actually ran a given snippet, or why one could
not, and the doctor's `sandbox` check lists what can run right now.

## The providers

### docker (default)

A throwaway container per snippet: `docker run --rm --read-only --cap-drop
ALL --security-opt no-new-privileges --user 65534:65534` with memory/CPU/pid
limits, the snippet mounted read-only, and (with `mount_workspace`) the run's
workspace mounted read-only at `/work`, with `$WORK=/work` so a snippet reads
its files the same way under `local`. Requires a reachable docker daemon
(`docker_available()`, cached 60s).

**Network**: `--network none`, no network at all, not even model access. A
snippet is a narrow, explicit sandbox call, not the agent's own process, so
there is nothing to lose by giving it none.

### local

A plain subprocess in a temporary directory, with the process's own
environment scrubbed of provider keys (`tools/shell.py`'s `scrubbed_env`) and
the same timeout as docker. No filesystem or network isolation at all: the
snippet runs as this process's own user, and `mount_workspace` mounts nothing
special, it just points `$WORK` at the workspace directory, writable. Used
only when docker is unavailable and `CODE_RUNNER_FALLBACK=local` is set (an
explicit, host-level opt-in), or when `CODE_RUNNER_PROVIDER=local` names it
outright.

**Network**: whatever this host's own network allows, always. `local` has
no mechanism to restrict it, so `sandbox.registry.resolve` only ever reaches
it through an explicit choice, never as a silent substitute for the docker
provider when it happens to be unavailable.

## Setup

**docker**: nothing beyond a reachable daemon.

**local**: nothing; it is a plain subprocess. Set `CODE_RUNNER_FALLBACK=local`
to let `run_code` fall back to it when docker is unavailable.

## The `run_code` tool and the code view

`tools/run_code.py`'s `run_code` tool and `run_snippet` (shared with the code
view's run route, `dashboard/backend/routes/views.py` /
`views/code.py`) both go through `sandbox.registry.resolve` /
`sandbox.registry.get_provider`. The tool's text output names the provider
(`runtime: docker`, `runtime: local`); `run_snippet`'s structured result
carries it as `sandbox`.

Under docker the snippet has no network and, unless the agent asks for the
workspace mount, nothing of the hub's to read, which is why the capability
guard counts it as reading private data at most
([tools and capabilities](tools-and-capabilities.md)). Under `local` it is
classified like `run_shell`, since it then has the host's network and
filesystem.

## Doctor

The `sandbox` check (`common/doctor.py`) lists every provider's availability
and why not (docker daemon down). `fail` when the resolved default provider
cannot run at all; `ok` otherwise.

Related: [containers](containers.md), [tools-and-capabilities](tools-and-capabilities.md), [service-health](service-health.md).
