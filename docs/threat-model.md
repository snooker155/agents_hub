# Threat model

What the hub protects, from whom, where the boundaries are, and what it leaves
to the operator. The short version: the hub assumes the model can be turned
against you by what it reads, and limits what a turned agent can reach; it
does not try to make the model itself trustworthy. How to report a flaw is in
`SECURITY.md` at the repository root.

## What is worth protecting

| Asset | Where it lives | Why it matters |
| --- | --- | --- |
| Model provider keys | `.env`, Settings, the model catalog | spend on your account, access to your provider data |
| Secrets for tools and connectors | `secrets` table, encrypted ([secrets](secrets.md)) | mail, GitHub, Google, databases in the agent's name |
| The host | the machine the backend runs on | a run in `local` mode is a process with your permissions |
| Workspace files, chats, memory | `.agents_hub/`, the database, blobs | private work, personal memory about a person |
| Connected accounts | channels, watchers, connectors | an agent can send mail or post as you |
| Money | provider bills, sandbox hours | a runaway loop or a flood of calls costs real money |

## Who might attack

- **Text the agent reads.** A web page, a mail, a GitHub issue, a file, a
  message in a channel or a tool result can carry instructions. This is the
  most likely attacker and the one most of the design is about.
- **A member of a shared hub** (`multi` mode) reaching another workspace's
  records or another person's runs.
- **A widget visitor** on a site that embeds a [chat widget](widget.md).
- **Someone on the network** who can reach the hub's port.
- **A compromised or careless dependency**: an MCP server, a skill from a
  public repository, an imported agent.

The operator and the administrators of a `multi` hub are trusted: they hold
the keys and can change every setting.

## Boundaries and what holds each

**Browser or client to the API.** `single` mode asks nobody for a credential
and is for one person on a machine only they can reach. `token` mode needs the
shared token. `multi` mode needs a session, a personal API key or single sign
on, and checks the caller's role on the workspace named in the path, the query
or `X-Workspace`; routes whose path names no workspace check the role on the
record they load ([identity](identity.md)). Sessions and keys are stored as
hashes, passwords as PBKDF2 digests. Requests per minute are limited per
principal, streams use short tickets instead of tokens in URLs, and CORS
drops credentials for a wildcard origin.

**The hub to its runs.** A run is a subprocess or a container that reports
back over `/api`. In `token` and `multi` mode it gets a run token minted for
that launch, which reaches only the relay routes, and people cannot reach
those routes at all. The hub's own credentials are removed from every run's
environment ([identity](identity.md#run-tokens-and-the-service-credential)).

**A run to the host.** In `local` execution mode a run is your own process:
it can read what you can read. `docker` mode gives each run its own container
with limits from its [environment](environments.md). An
[isolated workspace](isolation.md) runs shell and code in a container with no
network, a read only root and only the workspace folder mounted, and keeps the
agent loop on the hub so no key enters the sandbox.

**A run to the internet.** Fetches and the browser go through
`common/ssrf.py`, which refuses loopback and private addresses after
resolving the name. An environment's network can be `limited` to a host list
or `none`. The hub's own web and browser tools hold that list always; with the
egress proxy on, clients that honour proxy variables are held too, and a
`docker` mode container joins a network with no route out except the proxy. Secrets can be bound to hosts, so a token is only sent
where it belongs ([secrets](secrets.md#secrets-bound-to-hosts)).

**An agent's own reach.** Every tool is classified by what it grants
(ingests untrusted, reads private, can exfiltrate), and an agent that would
hold all three is refused when it is saved or built, not warned about
([tools and capabilities](tools-and-capabilities.md)). On top of that, per
workspace: a [tool policy](tool-policy.md), the approval gate and
[hooks](hooks.md) around each call, [guardrails](guardrails.md) on inputs and
outputs including sequences of calls, and a budget per run that stops it.

**A visitor to the hub.** A widget answers only allowed origins, with a
visitor token bound to the widget, and shows the visitor their own thread and
nothing of the run behind it ([widget](widget.md#security-model)).

## Threats and answers

| Threat | Answer in the hub | What is left |
| --- | --- | --- |
| Injected text makes an agent send private data out | capability guard blocks the three way combination; host bound secrets; network `limited` or `none` | an agent with two capabilities can still be misled within them |
| Injected text makes an agent act (send mail, push code, delete) | approval gate, hooks, sequence guardrails, tool policy | the approval gate is off by default; turn it on where agents act outside |
| A run reads or changes the host | `docker` mode, environments, isolated workspaces | `local` mode, the default, has no such boundary |
| A run reaches internal services (SSRF) | `common/ssrf.py` on fetch and browser, egress proxy | without the egress proxy, a process that opens its own sockets is not filtered |
| A member reads another workspace | role checks by workspace in middleware and routes, record level checks, scoped API keys | records written before workspaces existed read as visible to every account |
| A stolen session or key | hashed storage, revocation, expiry, per key scope and rate limits, audit log | a bearer token works until revoked |
| Secrets leak from a backup | Fernet encryption under `AGENTS_HUB_SECRET_KEY`, kept apart from backups | the key in `.env` on the same host as the database |
| A malicious skill, MCP server or imported agent | skill safety review with flags and license check, MCP tools classified like any tool | a reviewed skill is only as safe as the review |
| Runaway spend | budget per run with auto pause, daily token caps on `/v1`, rate limits | a provider's own limits are the last stop |
| Personal memory poisoned by a conversation | one private pool per person and workspace, switched on or off per agent | what an agent remembers is still model output |

## What the hub does not defend against

- **An exposed `single` mode hub.** `ah serve` binds `0.0.0.0` so a phone on
  the same network can open the dashboard; with no login, anyone who reaches
  the port is the operator. Use `token` or `multi` mode, or a firewall, as soon
  as the network is not only yours.
- **The model provider.** Prompts, files the agent reads and outputs go to the
  provider an agent uses. Use a [local model](local-models.md) for data that
  must not leave the machine.
- **Prompt injection as such.** No filter reliably tells instructions from
  data. The hub limits consequences instead; the injection suite in
  `evals/injection.py` measures how often agents comply.
- **The host it runs on.** Root on the host, or read access to `.env` and the
  database, is everything.

## Checking a deployment

`ah doctor` includes a `security` check: the capability guard, the secret key
against the secrets stored, and people sharing a hub whose runs have the
host's permissions ([service health](service-health.md#check-security)). The
hardening list in `SECURITY.md` is the rest.
