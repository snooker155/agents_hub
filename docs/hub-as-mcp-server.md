# The hub as an MCP server

The hub speaks the Model Context Protocol at `POST /v1/mcp`, so Claude Code,
Cursor and any other MCP client can ask the hub's agents questions as tools.
The agent runs on the hub with its own tools, memory and knowledge; the
client sees one answer. This is the other direction from
[MCP servers](mcp.md), where the hub is the client and attaches somebody
else's tools to its agents. The code is
`dashboard/backend/routes/mcp_server.py`; the page that shows the address and
the install snippets is Distribution ([distribution](distribution.md)).

## Setup

You need the hub's public address (`https://hub.example.com` below) and, unless
the hub runs in `single` mode, a credential: a personal API key
([api keys](api-keys.md)) or the shared `AGENTS_HUB_API_TOKEN`.

### Claude Code

```
claude mcp add --transport http agents-hub https://hub.example.com/v1/mcp --header "Authorization: Bearer ahk_..."
```

Claude Code's own `--scope` option (`user` or `project`, for example) decides
where Claude Code stores the entry; the hub does not care. Then ask Claude
Code to list the agents on the hub or to ask one of them something.

### Cursor

Put the server in `~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json`
(one project):

```json
{
  "mcpServers": {
    "agents-hub": {
      "url": "https://hub.example.com/v1/mcp",
      "headers": { "Authorization": "Bearer ahk_..." }
    }
  }
}
```

The Distribution page also has an Add to Cursor button that opens Cursor with
the entry filled in.

### From the command line

`ah mcp connect <claude-code|cursor>` prints what to paste, with the hub's
address filled in:

```
ah mcp connect claude-code
ah mcp connect cursor --workspace shop
```

For `claude-code` it prints the `claude mcp add` line, for `cursor` the
`mcpServers` JSON. The address is `AGENTS_HUB_URL` when set, else the hub's
configured public URL, else `http://localhost:8000` (or `DASHBOARD_PORT`). The
key is `--key`, else `AGENTS_HUB_API_KEY`, else `AGENTS_HUB_API_TOKEN`; with
none, no `Authorization` header is written, which is right for a hub in
`single` mode. `-w` or `--workspace` adds the `X-Agents-Hub-Workspace` header.

`--write` does the install: for `claude-code` it runs `claude mcp add` (the
`claude` command must be on `PATH`); for `cursor` it merges the `agents-hub`
entry into `~/.cursor/mcp.json`, or into `.cursor/mcp.json` in the current
folder with `--project`, keeping the other servers in the file. Restart
Cursor or reload its MCP servers afterwards.

### Other MCP clients

Any client that can talk Streamable HTTP and send a custom header works: the
URL is `https://hub.example.com/v1/mcp`, the header is
`Authorization: Bearer <key>`. Add `X-Agents-Hub-Workspace: <name>` to pin the client to one
workspace. In `single` mode no key is needed.

## The tools

| Tool | What it does |
|---|---|
| `list_workspaces` | The workspaces this credential can reach. |
| `list_agents` | The agents you can ask, with a name and description each. Takes an optional `workspace`; without one, every agent runnable in some reachable workspace. |
| `ask_agent` | One turn with an agent. Arguments: `agent_id` and `message` (required), `context`, `workspace`, `conversation`, `wait_seconds`. |
| `get_run` | The status of a run and, once it finished, its answer. Argument: `run_id`. |

`list_agents` applies the chat's own rule for who may run what (system agents
everywhere, an unshared agent only in its owner workspace, a workspace's
`allowed_agents` list). A workspace that is not reachable with the credential
is a tool error.

`ask_agent` sends the message through the web chat's pipeline, so the agent's
tools, memory, guardrails and budget apply exactly as in the chat, and the
turn is a run on the Messages page. Put everything the agent needs in
`message` or `context`: it cannot see the client's files. `context` is
optional material (code, a diff, an error) that is placed in front of the
message under a "Context from the caller" line. The answer is the agent's
text followed by a line with the agent, the run id and the `conversation`
handle; the structured result also carries `status`, `answered_by` (the agent
that wrote the answer, which differs after a handoff), `workspace` and
`usage`.

The workspace is chosen as for `/v1` (see
[agents as models](hub-as-provider.md#agents-as-models)): the `workspace`
argument, else the `X-Agents-Hub-Workspace` header, else the key's only
workspace, else the agent's own. The same refusals apply: a workspace outside
the key's scope or one the caller is not an editor of in `multi` mode, a
workspace that does not exist, an agent not available there. They come back as
a failed tool result with the reason as text.

## Conversations

The first answer carries a `conversation` value. Pass it back to `ask_agent`
to continue: the hub rebuilds the earlier turns from their runs. The handle is
signed, lasts 30 days, and is bound to the caller, the agent and the
workspace. A forged handle, one issued to another account, or one used with
another agent or workspace is refused with a message saying to omit it and
start a new conversation. Without a handle every call starts a new
conversation.

## Long turns

`ask_agent` waits for the answer for `wait_seconds`: 600 by default, never
less than 5, never more than the chat's turn timeout. If the agent is still
working it returns `status: "running"` with a `run_id` and the conversation
handle, and the turn keeps running on the hub. Call `get_run` with that
`run_id` later: it returns `status`, `agent_id`, `workspace`, `started_at`,
`finished_at`, and the `answer` (and `error`, if any) once the run is
completed, failed or stopped. `get_run` answers "no such run" both for an id
that does not exist and for one in a workspace the caller cannot reach, so ids
cannot be probed.

Many MCP clients give a tool call a timeout of their own, shorter than the
agent's work. Ask for a `wait_seconds` below that timeout and poll with
`get_run`.

## Authentication

`/v1/mcp` sits under `/v1`, which is outside `/api` but closed all the same
(`common.auth.CLOSED_OUTSIDE_API_PREFIXES`), because an agent spends the
operator's provider credit for whoever calls it. It is not at `/mcp`: that
path is the dashboard's page for the MCP servers a workspace attaches, and a
browser opening it must get the app. The
middleware resolves the caller by mode, as for [`/v1`](hub-as-provider.md#authentication):

- `single`: nothing is asked; every call is the local operator.
- `token`: the shared `AGENTS_HUB_API_TOKEN` as the bearer token.
- `multi`: a personal API key (`ahk_...`) or a session token. A key acts as its
  owner and honours its workspace scope and expiry.

A missing or wrong credential is a `401` with `{"detail": "Invalid or missing
API token"}`. The per-minute rate limit applies to `/v1/mcp` like any other
request. There is no OAuth discovery: the hub does not advertise an
authorization server, so the client must be given the header up front.

## Untrusted input

A run started over MCP has the origin `mcp` (`message_origin="mcp"`), and
`mcp` is one of the untrusted channels in `tools/capabilities.py`
(`UNTRUSTED_CHANNELS`, with Telegram, Slack, mail and the other chat
channels). The reason is that an IDE agent forwards whatever it read: a
repository file, a web page, a ticket, any of which may carry instructions
meant for the agent behind the tool.

Concretely, the run is evaluated by the
[capability guard](tools-and-capabilities.md) with `ingests_untrusted` added
to whatever the agent's tools grant. An agent that can also read private data
and send data out then forms the lethal trifecta on that channel. For a
channel this check is a warning: it is logged and recorded with the run, it
does not stop the turn. The way to make an agent safe to expose over MCP is to
give it tools that do not close the loop, as for a Telegram or Slack agent.

## Accounting

Before an agent starts, `ask_agent` checks the caller's tokens per day
(`AGENTS_HUB_RATE_LIMIT_TOKENS_PER_DAY` or the key's own value) and a key's
monthly money cap ([api keys](api-keys.md#rate-limits)). Over a limit it is a
failed tool result: "daily token limit reached; it resets at 00:00 UTC" or
"this API key has reached its monthly budget".

Each turn that finishes writes the same two rows a `/v1` agent call writes: a
`serving_usage` row and a `model.serve` audit row ([audit](audit.md)), with
object type `agent`, the workspace, the run id and `via: mcp` in the details,
and the path `/v1/mcp`. A failed turn is recorded with the error. A turn that
outlives `wait_seconds` is not recorded until it is read: its cost is on the
run itself, as for any chat turn.

## Protocol details

- Transport: Streamable HTTP in its stateless form. Every `POST` is answered
  with one `application/json` body. No `Mcp-Session-Id` is issued.
- Protocol versions answered: `2025-06-18`, `2025-03-26` and `2024-11-05`. A
  client asking for one of them gets it; anything else gets `2025-06-18`.
- Methods: `initialize` (with the server name `agents-hub`, the hub's version
  and short instructions for the client's model), `ping`, `tools/list`,
  `tools/call`, and empty answers to `resources/list`,
  `resources/templates/list` and `prompts/list`. Any other method is JSON-RPC
  error `-32601`. Notifications and client responses get `202` with no body.
- A body may be one message or a batch (a JSON array).
- A tool that fails (bad arguments, a refused workspace, a limit) returns a
  normal result with `isError: true` and the reason as text, so the client's
  model can read it. Protocol problems are JSON-RPC errors; an unparseable
  body is `400` with `-32700`.

## Routes

| Route | Answer |
|---|---|
| `POST /v1/mcp` | One JSON-RPC message or a batch; one JSON body back, or `202` for notifications. |
| `GET /v1/mcp` | `405`, `Allow: POST`. The hub has no stream of its own to offer. |
| `DELETE /v1/mcp` | `405`, `Allow: POST`. There is no session to end. |
| `GET /api/distribution` | Among other things, the MCP address (`mcp.url`) the Distribution page shows. |

## Gotchas

- **The address must be the one clients can reach.** The Distribution page
  builds it from `AGENTS_HUB_PUBLIC_URL`, else `AUTH_PUBLIC_URL`, else the
  request (honouring `X-Forwarded-Host` and `X-Forwarded-Proto`). Behind a
  proxy, set the public URL, and use https once the hub leaves your machine:
  the key travels in a header.
- **`GET /v1/mcp` is a 405.** Some clients probe it for a server stream and fall
  back to POST; that is expected. A client that insists on OAuth discovery
  will not connect, because there is none: configure the header by hand.
- **Agents answer, they do not hand tools to the client.** The client never
  sees the agent's tools or files; only the final text comes back.
- **One message per call.** The agent is not told what the client's earlier
  messages were unless they are in `message`, `context` or the continued
  conversation.
- **Keys and workspaces.** A key refused for a workspace outside its scope
  fails the tool call; `list_workspaces` shows what the credential can reach,
  and the `workspace` argument or the header names the one to use.
