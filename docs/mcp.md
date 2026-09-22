# MCP servers

An MCP server is somebody else's tool collection, speaking the Model Context
Protocol: a filesystem bridge, a ticket system, an internal service that already
talks the protocol. Attach one and its tools become ordinary hub tools an agent
can be granted. **Connect → MCP servers** in the sidebar.

The same direction as [connectors](connectors.md): this hub reaches out to
something you already run. The difference is what comes back. A connector is a
fixed integration this product wrote; an MCP server is a set of tools this
product has never seen, which is why attaching one asks you a security question
that attaching a Telegram bot does not.

Configured per [workspace](workspaces.md). A server attached to one workspace is
invisible to agents built in another, because the configuration lives inside the
workspace record rather than beside it.

## In the dashboard

The **MCP** page lets you attach servers and manage their configuration. Each server form collects the id, transport type (stdio, streamable_http, sse, or websocket), the endpoint details (command and args for stdio, URL and headers for HTTP, URL only for websocket), capability claims, approval settings, and a tool allowlist. The Test button connects to the server and lists its tools, showing which ones the current allowlist keeps. When you grant tools to an agent, use either the group form `mcp:<server_id>` to take the whole server or the tool form `mcp__<server_id>__<tool_name>` to take one.

## Attaching one

Pick a transport and fill in what it needs.

- **stdio** runs the server as a child process: a `command`, its `args`, and
  optional `env` variables. This is how the published servers usually ship.
- **streamable_http** and **sse** call a server over HTTP: a `url` (`http://`
  or `https://`) and optional `headers`.
- **websocket** calls a server over a `ws://` or `wss://` `url`. A websocket
  session carries no headers, so any stored on the server are kept but never
  sent for this transport.

A stdio example, the reference filesystem server:

| Field | Value |
| --- | --- |
| id | `files` |
| transport | `stdio` |
| command | `npx` |
| args | `-y`, `@modelcontextprotocol/server-filesystem`, `/srv/shared` |

An HTTP example:

| Field | Value |
| --- | --- |
| id | `tickets` |
| transport | `streamable_http` |
| url | `https://tickets.internal/mcp` |
| headers | `Authorization: Bearer <token>` |

## The id scheme

The server id is half of every tool name it produces. A server `tickets`
offering `search` becomes the tool `mcp__tickets__search`: the prefix `mcp__`,
the server id, a double underscore, the remote tool's own name. Server ids are
therefore restricted to lowercase letters, digits, underscore and hyphen, with
no double underscore of their own, so the split has exactly one reading.

An agent record can name either form:

- `mcp:tickets` takes the whole server, the way `filesystem` takes the whole
  filesystem group.
- `mcp__tickets__search` takes one tool.

The id cannot be changed after the fact. Agent records reference the tool names
it produces, so renaming it in place would silently detach an agent from its
tools. Delete and re-add instead.

## Capabilities

This is the part that is not optional. The [capability
model](tools-and-capabilities.md) classifies every tool by whether it ingests
untrusted content, reads private data or can send data outside, and refuses the
combinations that compose into an exfiltration primitive. A remote tool cannot
classify itself: nothing about its name or its JSON schema is a security claim,
and a server is free to call its outbound endpoint `get_weather`.

So you declare it, once per server, and every tool from that server inherits the
claim. Tick what is true:

- **ingests untrusted content** if the server returns text somebody outside your
  control can influence: web pages, inbound tickets, customer email.
- **reads private data** if it can read things you would not publish: files,
  records, internal systems.
- **can send data outside** if a call can move bytes out: posting, mailing,
  calling an arbitrary URL.

Per server rather than per tool because the coarse claim you can actually make
("this one reaches the internet") is worth more than a fine-grained one nobody
can verify. When in doubt, tick more: over-claiming costs you an agent that
needs a narrower server, under-claiming costs you the guard.

An agent whose tool set closes the lethal trifecta is refused at save time,
whether the third capability came from `run_shell` or from an MCP server.

## Approval

Each server carries an approval setting, which feeds the same [approval
gate](hooks.md) the built-in tools use:

- `none` gates nothing.
- `all` gates every tool from that server.
- A list of tool names gates those. Either spelling works, `delete_ticket` or
  `mcp__tickets__delete_ticket`.

Like the rest of the gate, it only does anything when the workspace has
`require_tool_approval` turned on. In a task run a gated call parks the task
until somebody answers; in chat it is refused with an explanation.

Hooks apply too. MCP tools are added to an agent's tool list before the hook
wrapper goes on, so a `PreToolUse` hook sees a remote call exactly as it sees a
shell command.

## Security notes

- **A stdio server starts from a scrubbed environment.** It gets the variables
  you gave it plus the ordinary ones a process needs, and not the backend's
  provider keys. It is third-party code this hub spawns, and it is treated like
  it.
- **Use the allowlist.** A server offering forty tools does not have to hand an
  agent forty tools. The Test button lists everything the server offers and
  marks what the current allowlist keeps, which is what it is for.
- **Secrets are masked on the way out.** A header or env value whose name looks
  like a credential (TOKEN, KEY, SECRET, PASSWORD, AUTHORIZATION) is shown as
  its last four characters. Saving the form unchanged keeps the stored value, so
  an edit cannot overwrite a token with dots.
- **A tool description is not trusted input in the way page content is**, but it
  does reach the model. Attach servers you would let someone write prompts for.

## How it behaves at runtime

Tool lists are cached per server for a few minutes, so a burst of agent builds
costs one connect rather than one per build. Editing a server invalidates its
own entry, and Test and Refresh drop it explicitly.

Each tool *call* opens its own session, which for stdio means the server is
spawned for the call and reaped when it finishes. Nothing is left running
between calls, so a server that hangs or crashes cannot outlive the call that
started it. The cost is a process start per call, which is worth paying for a
tool that runs a handful of times in a run.

## Gotchas

- **A server that will not connect is skipped, not fatal.** The agent is built
  with the tools that did resolve, and the error is recorded on the server entry
  for this page to show. An agent that suddenly has fewer tools than its record
  asks for is looked up here, not in its prompt.
- **A count of "not loaded" is not zero.** The list shows a tool count only for
  servers something has already loaded; hit Test or open the tool list to make
  it connect.
- **Deleting a server does not edit the agents that named it.** They simply get
  fewer tools. Their records still list ids that now resolve to nothing.
