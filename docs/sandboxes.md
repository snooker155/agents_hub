# Sandboxes

A sandbox runs one snippet of code (from the `run_code` tool, or the Chat
code panel) to completion, somewhere isolated from the hub's own host and
the agent's own working directory. `sandbox/` is the abstraction:
`sandbox/base.py` defines the request/result shape every provider takes and
returns, `sandbox/registry.py` picks a provider by name, and four modules
implement one each: `docker.py`, `local.py`, `e2b.py`, `modal.py`.

## Choosing a provider

`sandbox.registry.resolve(environment, settings)`:

1. The run's [environment](environments.md)'s `sandbox_provider`, when it
   names one explicitly (anything but `inherit`).
2. Else `CODE_RUNNER_PROVIDER` (default `docker`).
3. Else, when that provider cannot run right now and
   `CODE_RUNNER_FALLBACK=local` is set, `local`: the same rule `run_code`
   always applied when docker was unavailable, now expressed as a provider
   choice.

The resolved name is not itself a guarantee of availability: `run_code`'s
output (and `POST /api/environments/sandbox/providers`, the Environments
page's provider picker) says which provider actually ran a given snippet, or
why one could not.

## The providers

### docker (default)

A throwaway container per snippet: `docker run --rm --read-only --cap-drop
ALL --security-opt no-new-privileges --user 65534:65534` with memory/CPU/pid
limits, the snippet mounted read-only, and (with `mount_workspace`) the run's
workspace mounted read-only at `/work`, with `$WORK=/work` so a snippet reads
its files the same way under `local`. Requires a reachable docker daemon
(`docker_available()`, cached 60s).

**Network**: see [Network policy](#network-policy-per-provider) below. This
is the provider with the enforced fence.

### local

A plain subprocess in a temporary directory, with the process's own
environment scrubbed of provider keys (`tools/shell.py`'s `scrubbed_env`) and
the same timeout as docker. No filesystem or network isolation at all: the
snippet runs as this process's own user, and `mount_workspace` mounts nothing
special, it just points `$WORK` at the workspace directory, writable. Used
only when docker is unavailable and `CODE_RUNNER_FALLBACK=local` is set (an
explicit, host-level opt-in), or when an environment names `local` outright.

**Network**: whatever this host's own network allows, always. `local` has
no mechanism to restrict it, so `sandbox.registry.resolve` only ever reaches
it through an explicit choice, never as a silent substitute for a fenced
provider that happens to be unavailable.

### e2b

A remote, short-lived VM through [e2b.dev](https://e2b.dev)'s Python SDK.
Optional dependency (not required to run the hub at all): `pip install
"e2b>=2"`; [Setup](#setup) below says why the version matters.
Not supported: `mount_workspace` (no local filesystem to mount from) and
`stdin` (the SDK's `commands.run` takes no literal input data); both come
back as a clear refusal rather than a silent no-op.

### modal

A remote container through [modal.com](https://modal.com)'s Python SDK.
Optional dependency, not installed by default: `pip install modal`. Same
`mount_workspace`/`stdin` limitations as e2b, for the same reasons.

## Network policy per provider

Every provider takes the same three network types (`none`, `limited` with a
host list, `unrestricted`) and must enforce or refuse them, never silently
narrow or widen what it was asked for.

| Provider | `none` | `limited` | `unrestricted` |
| --- | --- | --- | --- |
| docker | `--network none`: no network at all | fenced onto the internal, no-route-out `agents-hub-egress` network, reachable only through the egress gateway's allowlisted proxy, enforced at the network layer, not just by well-behaved clients. **Refused** (not silently run wide open) when the egress proxy is off | joins the ordinary docker `bridge` network: normal outbound internet |
| local | no mechanism to restrict; only ever reached through an explicit `local` choice | same: no mechanism, so `sandbox.registry.resolve` never chooses it for a `limited` request implicitly | this host's normal network |
| e2b | `allow_internet_access=False`: e2b's own outbound cutoff | e2b's own domain allowlist (`network={"allow_out": hosts, "deny_out": ...}`), enforced by e2b, not by this process. Covers TLS (443) only; plain HTTP is not passed. **Refused** when the request names no hosts | e2b's own default (open outbound) |
| modal | `block_network=True`: modal drops all outbound traffic | `outbound_domain_allowlist` (plus a `*.host` wildcard per host, matching the hub's own "an entry covers its subdomains" rule), enforced by modal. Covers TLS (443) only. **Refused** when the request names no hosts | modal's own default (open outbound) |

### The enforced docker network policy

Before this feature, every docker-mode container (a `run_code` sandbox or
an agent's own run container) stayed on the ordinary `agents-hub` bridge
network regardless of its environment's network type, relying entirely on
the egress proxy (when enabled) and the hub's own tool-level checks
(`tools/web.py`, `tools/browser.py`) to hold a `limited`/`none` policy. A
process that ignored the proxy variables and opened a raw socket was not
stopped by anything (see `environments/egress.py`'s own docstring).

When `AGENTS_HUB_EGRESS_PROXY=1`, `managers/container_manager.py` now fences
such a container instead:

1. `ensure_egress_network()` creates `agents-hub-egress`, a docker network
   with `--internal`: the daemon gives it no default route out, so a
   container on it alone can reach nothing but another container on the same
   network.
2. `ensure_egress_gateway()` starts (idempotently) a small relay container,
   `agents-hub-egress-gateway`, attached to **both** that internal network
   and the ordinary `agents-hub` bridge. It runs `socat` (or whatever image
   `AGENTS_HUB_EGRESS_GATEWAY_IMAGE` names), forwarding raw TCP to the real
   egress proxy on this host (`environments/egress.py`, which does the actual
   token/allowlist check).
3. The run container joins `agents-hub-egress` only, and its
   `HTTP_PROXY`/`HTTPS_PROXY` (already set by `environments/launch.py` to
   point at the proxy, with a token scoped to this run's hosts) are rewritten
   to name the gateway container instead of `host.docker.internal`, since the
   fenced network cannot reach the host directly, only the gateway sitting on
   both networks.

A well-behaved client (`requests`, `pip`, `npm`, `git`, curl) still reaches
its allowed hosts, through the proxy, exactly as before. A process that
ignores the proxy variables now has nowhere to go at all: the internal
network has no route anywhere except the gateway, and the gateway only
speaks the proxy protocol on the one port it relays. `run_code`'s own
`limited` requests refuse outright when the proxy is off, rather than
running the snippet with no fence (a `run_code` snippet needs no network by
default, so there is nothing to lose by refusing); an agent run container
still falls back to the softer, tool-level-only fence when the proxy is off,
since the agent needs *some* network for its own model calls regardless.

This is enforced for both kinds of docker container: `run_code`/code-view
snippets through the `docker` sandbox provider, and an agent's own run
container in docker mode (`managers/container_manager.enforce_network_policy`,
called from `start_container` for both the hardened one-shot run-container
path and the node-container path). See the `sandbox` doctor check
(`common/doctor.py`) for whether it is currently active.

## Setup

**docker**: nothing beyond a reachable daemon. `AGENTS_HUB_EGRESS_PROXY=1`
turns on the enforced network policy above; `AGENTS_HUB_EGRESS_GATEWAY_IMAGE`
overrides the gateway's image (default a tiny `socat`-capable one).

**local**: nothing; it is a plain subprocess. Set `CODE_RUNNER_FALLBACK=local`
to let `run_code` fall back to it when docker is unavailable.

**e2b**: `pip install "e2b>=2"` (or `pip install -r requirements-sandbox.txt`,
which pins the 2.x line: the 1.x API has no network parameters, and this
provider needs them). Set `E2B_API_KEY` (in settings/`.env`, or the hub
secrets store's global scope) and optionally `E2B_TEMPLATE`.

**modal**: `pip install modal`, or `pip install -r
requirements-sandbox.txt`. Set `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`
(settings/`.env`, or the hub secrets store's global scope) and optionally
`MODAL_IMAGE` (a registry image; `modal.Image.debian_slim()` otherwise).

`e2b` and `modal` are optional dependencies: nothing else in the hub imports
them, so their absence never breaks anything besides those two providers
reporting themselves unavailable (`sandbox.registry.available()`, the
`sandbox` doctor check, the Environments page's provider picker).

## The `run_code` tool and the code view

`tools/run_code.py`'s `run_code` tool and `run_snippet` (shared with the code
view's run route, `dashboard/backend/routes/views.py` /
`views/code.py`) both go through `sandbox.registry.resolve` /
`sandbox.registry.get_provider`. The tool's text output names the provider
(`runtime: docker`, `runtime: e2b`, …); `run_snippet`'s structured result
carries it as `sandbox`.

`run_code`'s own network default is unchanged: `none`, full isolation, not
even model access (it is a narrow, explicit sandbox call, not the agent's own
process). A run whose environment sets network `limited` relaxes that to the
environment's own allowed hosts instead of a blanket refusal
(`AGENTS_HUB_NETWORK`/`AGENTS_HUB_ALLOWED_HOSTS`, the same variables
`tools/web.py` reads, set by `environments/launch.py`). Everything else (no
environment, or one that is `unrestricted` or `none`) keeps the historical
no-network sandbox. A `limited` snippet gets no workspace mount, and the
capability guard counts it as taking text in and sending data out
([tools and capabilities](tools-and-capabilities.md)).

## Doctor

The `sandbox` check (`common/doctor.py`) lists every provider's availability
and why not (docker daemon down, SDK missing, key missing), and whether the
enforced docker network policy above is active. `fail` when the resolved
default provider cannot run at all; `warn` when it can but the egress proxy
is off; `ok` otherwise.

Related: [environments](environments.md), [containers](containers.md), [tools-and-capabilities](tools-and-capabilities.md), [service-health](service-health.md).
