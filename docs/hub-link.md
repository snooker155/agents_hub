# Hub link: one hub attached to another

A hub on a laptop and a shared hub in the cloud are the same software. This
page is about attaching the first to the second: the laptop keeps running
everything itself, uses the shared hub's models and agents, sends copies of
what it did there, and can lend the shared hub its own machine as a *node*.
Nothing is ever read back from the shared hub: it is a supplier and a
mirror, never a source of truth for the laptop. Two hubs linked to each
other do not echo.

Settings → System → Remote hub is the one form for all of it; `ah hub` is
the same from the terminal. The code is `hublink/`, the routes are
`dashboard/backend/routes/hublink.py`.

## The link

```bash
# on the laptop
ah hub link https://hub.example.com --key ahk_...      # a personal key cut on the shared hub
ah hub status
ah hub sync                                            # send the outbox now
ah hub unlink
```

The key is a personal API key of the shared hub (Settings → API access →
Personal keys there), so everything the laptop does there is done as that
person: mirrored records belong to them and land in their personal
workspace unless another one is named. The link is checked against
`/api/auth/me` before anything is registered. The key and the mirror
connection's token are stored in the laptop's database, at the same trust
level as a custom backend's key.

Linking does three reversible things on the laptop; `unlink` undoes them,
and deletes nothing on the shared hub.

**Its models.** The shared hub's `/v1` becomes the custom backend
`hub-remote` (Settings → Models → Custom backends), seeded with every model
the shared hub serves, agents included (`/v1/models` lists them). Pick one
on the Models page like any other; the shared hub does the accounting for
the key. Switch it off with `--no-models` or the toggle.

**Its agents as tools.** The shared hub's MCP server (`/v1/mcp`,
[hub-as-mcp-server](hub-as-mcp-server.md)) is attached to the `default`
workspace as the server `hub-remote`, so `ask_agent`, `list_files`,
`search_knowledge` and the rest of its tools are available to the laptop's
agents. The server is claimed `reads_private` and `can_exfiltrate` (it
reaches another hub), which the capability guard weighs like any other MCP
server. `--no-agents` leaves it out.

**The mirror connection.** When runs are mirrored, a connection named
`node-<name>` is created on the shared hub ([connections](connections.md),
kind `hub`) and its token kept; the token can only report runs. A second
link with the same name rotates the token rather than failing.

## The mirror

Three kinds, each a switch (`--mirror runs,chats,memory`; `--mirror none`):

| Kind | What is sent | Where it lands on the shared hub |
|---|---|---|
| `runs` | every run that reaches a terminal status, as the OTLP span `common/otel_export.py` builds, with its model and tool calls and its cost | a run of the connection `node-<name>`, back-dated to when it ran, in the target workspace |
| `chats` | a conversation, whole, each time it is saved | `mirror:<name>:<id>` in the chat list, read-only, badged with the node's name |
| `memory` | the notes of a personal pool each time one is written | merged by note id into the person's personal pool in the target workspace |

Everything goes through an outbox (the `hub_link_outbox` DocStore). A store
write only marks the record as due, keyed by the record, so a chat saved
ten times is sent once with what it holds when the drainer gets to it. The
drainer runs every five seconds on one replica (lease `hublink-mirror`),
sends in batches of fifty and retries what failed up to fifty times; the
outbox is capped at five thousand markers, oldest dropped. A shared hub
that is down costs nothing but a longer outbox. Settings shows the pending
count and the last error; `ah hub sync` drains now.

What is never mirrored: a run the laptop only reported in (`origin`
`ingest`), a chat that is itself a mirror, a note that arrived through a
mirror. Secrets, connectors, `.env` and agent definitions never travel this
way either; definitions move with [`ah apply`](apply.md).

On the shared hub a mirrored chat opens like any other but cannot be
continued: the composer is off and a banner names the node. Its project,
flow and team references are dropped on arrival, since they would point at
the laptop's records.

## Nodes

The other direction: the laptop serves the shared hub. On the shared hub,
Nodes (under Integrations; `ah hub nodes add laptop`) creates a node and
shows its token once. On the laptop:

```bash
ah hub link https://hub.example.com --key ahk_... --node-token ahnode_...
```

or the "Act as a node" part of the Remote hub form. From then on the
laptop's backend runs a loop that says hello with what it offers and
long-polls `/api/node-link/poll` for work. The laptop accepts no
connection; it dials out, so a machine behind NAT qualifies, and a hub with
several API replicas is fine since the mailbox is the database.

A node offers two things:

**Its local models.** The models of the laptop's local providers (the hub
runtime, Ollama, LM Studio; never a cloud provider, so no key of the
laptop's is spent by anyone else) appear on the shared hub as the custom
backend `node-<slug>`, with their context windows and a price of zero. A
model driver on the shared hub calls `/api/node-link/<id>/v1/...` with a
per-node proxy key, the request is written to the mailbox, the laptop
takes it on its next poll, runs it against its own `/v1` and streams the
answer back in pieces. Latency is the poll's (up to a quarter of a second
when the node is waiting) plus the model's. The catalog is hub-wide, so
every workspace on the shared hub may pick a node's model; an administrator
disables one there like any other.

**Its sandbox.** The laptop's own `run_code` provider (whatever
`CODE_RUNNER_PROVIDER` names there, docker when it has one) is the provider
`node:<id>` on the shared hub ([sandboxes](sandboxes.md)). An environment
names it and its snippets run on the laptop. The node's owner may limit
this to some workspaces; a run from any other is refused with 403, since a
snippet on someone's laptop should never be another team's. The network
policy travels with the request: a laptop whose provider is `local` refuses
a fenced request rather than running it open, and `mount_workspace` is
refused outright (the files are on the shared hub).

What a node never does: run the shared hub's agent loop (that needs the
shared hub's database, see [isolation](isolation.md) for why that design
was dropped), reach any route but `/v1/models`, the completion endpoints,
`/v1/embeddings` and its own exec route, or answer for a model outside its
shared providers. Switch either half off with `--no-share-models` or
`--no-sandbox`.

A node is online while its last poll is younger than ninety seconds. The
Nodes page shows each node's models, sandbox, host and version; `ah hub
nodes list` the same; a token is rotated with `ah hub nodes rotate <id>`
and the old one stops at once.

## The pool

Every node with models also feeds one shared backend, `nodes` (Settings →
Models), whose models are every node's together. A model picked there is
answered by whichever node shares it, may see the work, and is least busy
at that moment; the node is named in the answer's `X-Hub-Node` header. The
sandbox provider `nodes` does the same for snippets: an environment that
names it runs each snippet on the freest node with a sandbox.

So independent work spreads over the nodes without anything else changing:
a flow's parallel branches, an agent's delegated subtasks, the cases of an
eval, each on its own node. For explicit batch work, the tool
`map_on_nodes` sends one instruction over up to 200 items, all at once, and
returns each item's output with the node that made it.

How a node is chosen (`hublink/scheduler.py`):

- **Load** is a node's open jobs (waiting, taken, streaming) over its
  **capacity**, read from the mailbox, so every API replica sees the same.
  The node announces its capacity (Settings → System → Remote hub, "Requests
  at once", `--capacity`, default 4); the hub may cap it lower on the Nodes
  page. A node is handed no more jobs than it runs at once.
- When every eligible node is full, the request waits up to two minutes for
  a slot, then fails with 503.
- A node that does not take a job within eight seconds (asleep, offline,
  its loop stuck) loses it to the next node. Only a job nobody took moves,
  so a request never runs twice.

`GET /api/nodes/pool?workspace=<name>` lists the pool's models, capacity
and load, and for that workspace which nodes may take its work and why the
others may not. The Nodes page shows the same.

```bash
ah hub nodes pool --workspace research           # models, capacity, load; who may take research's work
ah hub nodes set <id> --trust untrusted          # administrators only
ah hub nodes set <id> --max-parallel 2           # cap what the node runs at once; 0 removes the cap
ah hub nodes set <id> -w team-a -w team-b        # limit the node to workspaces; --any-workspace lifts it
ah hub nodes set <id> --disable                  # out of service; --enable brings it back
ah hub link https://hub.example.com --key ahk_... --node-token ahnode_... --capacity 2   # on the node
```

## Trust and data classes

Whoever owns a node can read what it computes: the prompts sent to its
model, the snippets run in its sandbox, the answers. Software cannot change
that; only hardware that keeps memory encrypted from its own owner can
(confidential computing, proven by remote attestation). So the hub does not
try to hide work from a node. It decides which work a node may see at all.

Every node has a **trust level**, which only the hub's administrator sets,
never the node's owner:

| Trust | Meaning |
|---|---|
| `untrusted` | A volunteer's or a partner's machine. Public work only. The default for a node anyone but an administrator creates. |
| `trusted` | A machine the administrator answers for. Internal work too. The default for a node an administrator creates. |
| `attested` | Reserved for a node whose hardware proves, by remote attestation, that it runs the hub's image in an enclave its owner cannot read. Not settable by hand; the hub does not run attestation yet. |

Every workspace has a **data class** (workspace → Isolation tab, `PUT
/api/workspaces/<name>/data-class`), and an agent may set a stricter one
for itself (Model tab → loop settings, `loop.data_class` in `ah apply`):

| Class | Who may see it |
|---|---|
| `public` | any node |
| `internal` (default) | trusted or attested nodes |
| `confidential` | attested nodes only, so today no node: the work stays on this hub |

An agent's class only ever makes the workspace's stricter. The check runs
wherever work is handed to a node: a node's own backend (`node-<slug>`),
the pool, and both exec routes. A model call carries its workspace and
agent as headers (`X-Hub-Workspace`, `X-Hub-Agent`), set where the model is
built, so the class in force now applies, not the one at build time; a call
that names none is the default workspace's. A refusal names the node, its
trust and the class.

A node record from before trust levels counts as `trusted` in single mode
and `untrusted` in multi mode. `map_on_nodes` counts as reading untrusted
text for the capability guard: an untrusted node, given public work only,
could still answer anything.

## Routes

| Route | Credential | Purpose |
|---|---|---|
| `GET/PUT/DELETE /api/hublink`, `POST /api/hublink/sync` | administrator | the link |
| `PUT /api/hublink/mirror/chats`, `PUT /api/hublink/mirror/memory` | personal key | the mirror's receiving end |
| `GET/POST /api/nodes`, `GET/DELETE /api/nodes/{id}`, `POST /api/nodes/{id}/rotate` | any account (own nodes; administrators see all) | the nodes |
| `PATCH /api/nodes/{id}` | owner or administrator; `trust` administrators only | trust, capacity cap, workspaces, disabled |
| `GET /api/nodes/pool` | any account | the pool, and who may take a workspace's work |
| `GET /api/node-link/pool/v1/models`, `POST /api/node-link/pool/v1/{path}` | the pool's key | the backend `nodes` |
| `POST /api/node-link/pool/exec` | a hub credential | the sandbox provider `nodes` |
| `POST /api/node-link/hello`, `GET /api/node-link/poll`, `POST /api/node-link/jobs/{job}/head\|chunk\|end` | node token | the node's loop |
| `GET/POST /api/node-link/{id}/v1/{path}` | the node's proxy key | the shared hub's model drivers |
| `POST /api/node-link/{id}/exec` | a hub credential (a run token, the service token, a key) | `sandbox/node.py` |
| `POST /api/node-link/self/exec` | the loop's per-process secret | the node running a snippet on itself |

`/api/node-link` is self-authenticating (`common.auth
.SELF_AUTHENTICATING_PREFIXES`): each handler checks its own credential. The
proxy key lives only in the custom backend record it authenticates and as a
hash on the node; the node token is stored as a hash and shown once.

## Gotchas

- The shared hub's `/v1/models` is seeded into `hub-remote` at link time.
  Relink (save the form again) after the shared hub adds models, or run a
  Discover on the backend.
- A node's models are offered under `node-<slug>` with the laptop's own
  provider in the id (`hub-local/qwen...`). Two nodes with the same name get
  `-2`, `-3` suffixes.
- The node's base URL on the shared hub is its public URL
  (`AGENTS_HUB_PUBLIC_URL`) when set, else `localhost` on the API port.
  Workers on other hosts need the public one.
- A mirrored run's agent is the connection (`node-<name>`), not the
  laptop's agent id; the agent's name is in the span's attributes.
- Personal memory must be on in the target workspace of the shared hub for
  notes to be merged; otherwise they are skipped, not retried.
