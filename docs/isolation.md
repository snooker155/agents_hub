# Isolated workspaces

An isolated workspace is a perimeter: inside it an agent may do anything, and
nothing it does reaches outside, except reading the sites the workspace lists.
Shell commands and code run in a sandbox container with no network at all, so
`curl` has nowhere to go. Every tool that would work around the container is
off and cannot be added. The code is `common/isolation.py`.

## What changes inside

- **Shell and code run in a sandbox.** `run_shell` runs each command in a
  throwaway container: `--network none`, a read only root filesystem, no
  Linux capabilities, nothing in its environment but `HOME`, and only the
  workspace folder mounted, at `/work`, read and write. `run_code` always uses
  the docker sandbox with no network, whatever the environment or
  `CODE_RUNNER_PROVIDER` name: a cloud provider or the local fallback would run
  the snippet outside the perimeter. The image is
  `AGENTS_HUB_ISOLATED_SHELL_IMAGE`, else run_code's Python image (it has bash
  and Python both).
- **No combination is refused.** The capability guard does not judge an agent
  of an isolated workspace: with no network in the sandbox and only the
  allowlist below, nothing it combines can send data out. The perimeter is the
  guard. The approval gate and the hooks still apply when the owner sets them.
- **The agent loop stays on the hub.** It is the hub's own code and needs the
  hub's database; a container that ran it would need write access to that
  database, which would be a way out by itself. The model is called by the hub,
  as in every workspace, and no key ever enters the sandbox. Loops, teams,
  flows and resident instances of an isolated workspace run as local processes
  for that reason, even when the workspace or an environment names Docker.

## The tools an agent keeps

An allowlist (`isolation.ALLOWED_TOOLS`), so a tool added to the hub later is
out until somebody classifies it:

- files in the workspace, the workspace's memory, tasks and their schedule,
  views (without `view_serve`), skills, the hub's own documentation;
- `run_shell` and `run_code`, in the sandbox;
- other agents of the same workspace, and its flows, teams and loops. As in
  every workspace, a tool that takes a `workspace` argument may name only this
  one ([workspaces](workspaces.md#one-workspace-per-run)): a task created in a
  neighbouring workspace would carry data to agents that can send it out;
- reading the internet: `fetch_url`, `web_search`, `browser_open`,
  `browser_read`, `browser_screenshot`, `browser_close`.

Out: connectors, chat channels, `notify_user`, MCP servers, deploys,
`git_publish`, the hub's own operations, agent management, Blender (which runs
on the host) and `browser_act`. An imported agent (Claude Code, Codex, any
remote service) does not run in an isolated workspace at all.

An agent the workspace owns cannot be saved with a tool from outside the
list (400). A shared agent, such as the main agent, keeps working here with its
outside tools taken off, and its prompt says so.

## Reading the internet

Only the hosts on the workspace's list (`isolation_allow_domains`), each one
itself or any subdomain of it. The list replaces every other domain list (the
hub's, the workspace's web policy, the agent's own), and an empty list reads
nothing.

- `fetch_url` sends GET only, with no body, a fixed User-Agent, no cookies (they
  are dropped before every hop) and nothing from the agent but the URL. A URL
  with a user name or password is refused. Every redirect hop is re-checked
  against the list and the private network block.
- `web_search` sends the query to the hub's search provider, narrowed to the
  list, and withholds results off it. With an empty list the provider is not
  called.
- The browser opens read only sessions: every request a page makes must be a
  GET or a HEAD with no body, WebSockets are refused, the list always applies,
  and clicking and typing are refused, the agent's and a person's on the live
  view alike. The page's address is re-checked after every navigation and read.
- A budget per run: `AGENTS_HUB_GATEWAY_MAX_REQUESTS` reading calls (default
  300). Every call, allowed or refused, is in the web call log.

A GET can still carry data in its address. That is why the list is the
workspace owner's, and why only listed sites are readable.

## The hub's own ways out

Closed for an isolated workspace:

- MCP servers cannot be attached, through the MCP page or a connection
  proposal;
- chat channels (Slack, Discord, Teams, mail) and Telegram chats cannot be
  bound to it, and widgets cannot be created for it;
- the personal memory pool, which is shared with the person's other
  workspaces, is switched off and cannot be switched back on;
- outbound webhooks and Slack endpoints receive none of its notifications or
  audit rows; they stay in the hub's inbox and audit log;
- a `.hooks.json` in the workspace folder is ignored, since the sandbox writes
  there, and an `http` hook never runs. Hooks the owner stored through the API
  still run, `command` ones only.

## Turning it on

On the workspace's settings, Isolation section, or
`PUT /api/workspaces/{name}/isolation` with `{"isolated": true,
"allow_domains": ["docs.python.org"]}`. Owner or administrator only; every
change is in the audit log as `workspace.isolation`. Turning it on is refused
(409) until:

- Docker answers on the hub's host, and the sandbox image is pulled (the
  sandbox has no network to pull it itself);
- no agent the workspace owns holds a tool from outside the list;
- nothing leads around the perimeter: no MCP server attached, no chat bound,
  no widget.

`GET /api/workspaces/{name}/isolation` answers the switch, the list, the
readiness checks, the offending agents and the allowlist itself. Turning it off
needs only the role, and reopens every way out.

## Gotchas

- **Files written in the sandbox belong to the hub's user.** The container runs
  with the hub process's uid and gid so the agent can edit the workspace's own
  files.
- **Nothing installs from the network.** `pip install` and `npm install` fail
  in the sandbox; bake what the workspace needs into
  `AGENTS_HUB_ISOLATED_SHELL_IMAGE`.
- **Every shell command is a fresh container.** Nothing survives between two
  `run_shell` calls but the workspace folder, the same as before, when each
  call was its own process.
- **The model provider still sees the conversation.** Isolation keeps data
  from leaving through the agent's tools; what the model reads goes to the
  provider configured on the Models page, as in every workspace.

Related: [workspaces](workspaces.md), [sandboxes](sandboxes.md), [tools-and-capabilities](tools-and-capabilities.md), [hooks](hooks.md), [browser](browser.md), [mcp](mcp.md), [secrets](secrets.md).
