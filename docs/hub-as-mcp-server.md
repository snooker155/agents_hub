# The hub as an MCP server

The hub speaks the Model Context Protocol at `POST /v1/mcp`, so any MCP
client (Claude Code, Cursor, VS Code, Windsurf, Claude Desktop, Codex, Gemini
CLI and the rest) can use the hub as a set of tools: ask its agents, read and
upload workspace files, search the workspace's knowledge, start teams, flows
and loops, and follow or stop runs. The server and its tools are the same for
every client; only where each client keeps its settings differs. This is the
other direction from [MCP servers](mcp.md), where the hub is the client and
attaches somebody else's tools to its agents. The code is
`dashboard/backend/routes/mcp_server.py` (protocol and agent tools) and
`dashboard/backend/routes/mcp_server_tools.py` (files, knowledge, workflows,
runs); the page that shows the address and a snippet for each client is
Distribution ([distribution](distribution.md)).

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

### VS Code

Put the server in `.vscode/mcp.json` in a project (note `servers` and
`type`, not `mcpServers`):

```json
{
  "servers": {
    "agents-hub": {
      "type": "http",
      "url": "https://hub.example.com/v1/mcp",
      "headers": { "Authorization": "Bearer ahk_..." }
    }
  }
}
```

For every project, add it to your profile with
`code --add-mcp '{"name":"agents-hub","type":"http","url":"https://hub.example.com/v1/mcp"}'`
(headers as above), or with the Add to VS Code button on the Distribution
page.

### Windsurf

`~/.codeium/windsurf/mcp_config.json`, with `serverUrl`:

```json
{
  "mcpServers": {
    "agents-hub": {
      "serverUrl": "https://hub.example.com/v1/mcp",
      "headers": { "Authorization": "Bearer ahk_..." }
    }
  }
}
```

### Claude Desktop

Claude Desktop starts local servers from `claude_desktop_config.json`
(Settings, Developer, Edit config), so a remote server with a header goes
through the `mcp-remote` bridge, which needs Node.js:

```json
{
  "mcpServers": {
    "agents-hub": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://hub.example.com/v1/mcp",
               "--header", "Authorization:${AUTH_HEADER}"],
      "env": { "AUTH_HEADER": "Bearer ahk_..." }
    }
  }
}
```

The value travels through `env` so the space in `Bearer ...` survives
argument quoting on Windows.

### Codex CLI

`~/.codex/config.toml`:

```toml
[mcp_servers.agents-hub]
url = "https://hub.example.com/v1/mcp"
http_headers = { "Authorization" = "Bearer ahk_..." }
```

### Gemini CLI

`~/.gemini/settings.json` (or `.gemini/settings.json` in a project), with
`httpUrl`:

```json
{
  "mcpServers": {
    "agents-hub": {
      "httpUrl": "https://hub.example.com/v1/mcp",
      "headers": { "Authorization": "Bearer ahk_..." }
    }
  }
}
```

### From the command line

`ah mcp connect <client>` prints what to paste for one client, with the hub's
address filled in. The clients are `claude-code`, `cursor`, `vscode`,
`windsurf`, `claude-desktop`, `codex`, `gemini` and `other` (the plain URL
and headers):

```
ah mcp connect claude-code
ah mcp connect vscode --workspace shop
```

The address is `AGENTS_HUB_URL` when set, else the hub's configured public
URL, else `http://localhost:8000` (or `DASHBOARD_PORT`). The key is `--key`,
else `AGENTS_HUB_API_KEY`, else `AGENTS_HUB_API_TOKEN`; with none, no
`Authorization` header is written, which is right for a hub in `single` mode.
`-w` or `--workspace` adds the `X-Agents-Hub-Workspace` header.

`--write` does the install. For `claude-code` it runs `claude mcp add`, for
`vscode` it runs `code --add-mcp` (the command must be on `PATH`). For the
others it merges the `agents-hub` entry into the client's own file and keeps
the other servers in it: `~/.cursor/mcp.json`, `~/.codeium/windsurf/mcp_config.json`,
Claude Desktop's `claude_desktop_config.json` (in `~/Library/Application Support/Claude/`
on macOS, `%APPDATA%\Claude\` on Windows, `~/.config/Claude/` on Linux),
`~/.gemini/settings.json`, and `~/.codex/config.toml` (appended; a file that
already has `[mcp_servers.agents-hub]` is left alone with an error). With
`--project` the file is the one in the current folder: `.cursor/mcp.json`,
`.vscode/mcp.json` or `.gemini/settings.json`. Restart the client or reload
its MCP servers afterwards.

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
| `ask_agent` | One turn with an agent. Arguments: `agent_id` and `message` (required), `context`, `file_ids`, `workspace`, `conversation`, `wait_seconds`. |
| `list_files` | Files of a workspace, newest first: uploads and what agents wrote. Optional `query` (part of a name, or an id), `source`, `limit`. |
| `read_file` | The text of a file (text, code, JSON, CSV, PDF) by `file_id` or by `path` in the workspace folder. A long file comes in pages: `offset`, `max_chars` (20 000 by default, 200 000 at most), and `next_offset` in the answer. |
| `upload_file` | Put a file into a workspace: `name` and either `content` (text) or `content_base64`. The same bytes return the existing file (`deduplicated: true`). With `path` the file is written into the workspace folder and replaces one already there. |
| `list_knowledge` | The memory pools of a workspace, with how many documents and notes each holds. |
| `search_knowledge` | Ranked passages from the pools for a `query`: indexed documents, notes, facts and blocks, the way an agent's `search_memory` ranks them. Optional `pool` (name or id) and `top_k`. No model call. |
| `list_workflows` | The teams, flows and loops of a workspace. Optional `kind`. |
| `run_workflow` | Start a team, flow or loop: `kind`, `id`, `input` (a team's or loop's goal, a flow's task description), `wait_seconds` (120 by default, 0 to return at once, 1800 at most). |
| `list_runs` | Recent runs of a workspace, agent turns and team, flow, loop and scenario runs together. Your own unless `mine` is false; optional `kind`, `status`, `limit`. |
| `get_run` | The status of any run (agent, team, flow, loop) and its answer once it finished. Argument: `run_id`. |
| `stop_run` | Stop a running agent turn, team, flow or loop and everything it started. Argument: `run_id`. |

Reads need the workspace to be reachable with the credential. `upload_file`,
`run_workflow` and `stop_run`, like `ask_agent`, also need the `editor` role
there in `multi` mode. The workspace is chosen the same way everywhere: the
`workspace` argument, else the `X-Agents-Hub-Workspace` header, else the
key's only workspace, else `default` (for `ask_agent`, the agent's own
workspace before `default`).

Nothing over MCP creates, changes or deletes agents, connectors, memory pools
or settings. What an IDE agent forwards may come from any file it read, so the
hub's configuration stays behind the dashboard. A personal memory pool is
listed and searched only for its owner, never for an admin.

`file_ids` on `ask_agent` attaches workspace files to the turn the way the
chat composer does: upload a local file with `upload_file`, then pass its id.
A file of another workspace, or one that does not exist, is refused with its
id.

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

`run_workflow` works the same way: it starts the run through the kind's own
launcher (the run is the same one the Teams, Flows or Loops page would start,
and it shows there), waits up to `wait_seconds`, and returns either the
finished run with its `answer` (a team's or loop's result, a flow's task
result) or the `run_id` with a note to call `get_run`. `stop_run` stops a run
of any kind with everything nested under it.

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

`run_workflow` checks the same two limits before it starts anything. The run
it starts is charged to the caller and the key like any run started from the
dashboard ([costs](costs.md)), so it counts against the key's monthly cap.
`upload_file`, `run_workflow` and `stop_run` each leave an audit row
(`file.upload`, `workflow.run`, `run.stop`) with `via: mcp` and the path
`/v1/mcp`.

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
