# Workspaces

A workspace is the unit of isolation: its own folder on disk, its own roster of
agents, its own settings, its own memory pools and its own projects.

Everything else in the product is scoped to one. Switching workspace in the top
bar changes what you see everywhere.

## What a workspace holds

- **A folder**, under the state root, holding the files agents read and write,
  plus reserved subfolders: `.logs/`, `.plans/`, `.views/`, `knowledge/`, and one
  folder per project. The workspace's own metadata is not in there: it lives in
  a central store keyed by workspace name.
- **`allowed_agents`** — which agents are available here. Every system agent is
  in this list automatically and cannot be removed. Custom agents are added by
  you, from the Agents page.
- **`default_chat_agent`** — which agent the Chat page pre-selects. New
  workspaces get the Main Agent.
- **Settings overrides** — model defaults, environment variables, execution mode
  (in-process or Docker), log level, the shell and web policies, and per-agent
  memory assignments.
- **A budget** — an optional spend cap for this workspace, enforced at run
  launch. See [costs](costs.md).

## Per-workspace memory

An agent's memory pool is resolved per workspace. The same agent can carry one
knowledge pool in one workspace and a different one in another, and the
assignment lives in the workspace's metadata rather than on the agent. That is
what lets a single Main Agent serve several unrelated efforts without bleeding
context between them.

## The Settings tab

The workspace page has a Settings tab drawn by the same sections as the
Settings page, holding what is configured per workspace: agent execution,
the tool policy, **web access** (the workspace's own allow and deny domain
lists, `/api/workspaces/{name}/web-policy`, which replace the global ones
for its runs), **personal memory** (the switch that lets the agents keep a
private pool about the user, [memory](memory.md)), task assignment,
secrets and the palette. Anything not set here falls through to the global
[settings](settings.md).

## One workspace per run

An agent, and every tool it calls, reads and changes only the workspace its run
belongs to (`common/workspace_scope.py`):

- **A `workspace` argument names this workspace or nothing.** `create_task`,
  `update_task`, `run_team_tool`, `create_flow_tool` and every other tool that
  takes one refuse another workspace's name, so an agent cannot put data into a
  neighbouring workspace, or create a new workspace by naming it.
- **A record named by id belongs to this workspace.** A task, a scheduled job,
  a flow, a team, a view, a file, a project of another workspace is "not
  found", and listings show only this workspace's records. An agent can be
  run, assigned or delegated to only when it is available in this workspace,
  and changed or deleted only when this workspace owns it.
- **Tools that see the whole service belong to the service's own agents.**
  Every run, session, container and instance of the hub, its costs and logs,
  and the system workspace's repository are reached by `service_agent` and the
  system workspace's `system_doctor` and `system_engineer` only. Another agent
  cannot be given these tools (400), and loses them when it runs.
- **Workspace management belongs to the main agent in `default`.** The tools
  that create and configure workspaces (`WORKSPACE_ADMIN_TOOLS`, see below)
  work only for `main-agent`, and only in a run of the `default` workspace.

## Managing workspaces from the chat

In the `default` workspace the main agent adds and manages workspaces when the
user asks for it in the chat (`tools/workspace_management.py`):

| Tool | What it does |
|---|---|
| `list_workspaces` | The workspaces the user can see: description, agents and tasks counts, isolated or not. |
| `get_workspace` | One workspace: description, the start of its instructions, default model, agents, isolation and its reading list, attached folder. |
| `create_workspace` | A new workspace with a description, instructions and extra agents. The system agents are in it from the start. |
| `update_workspace` | Its description, instructions or default model (`provider/model`). Nothing else. |
| `add_workspace_agent`, `remove_workspace_agent` | Its agents, under the same rules as the Agents tab. |
| `delete_workspace` | Deletes it after a person's yes on the approval card. |

The rules are the dashboard's. A name is one word without slashes and must be
new. System agents cannot be removed, an agent marked `default_workspace_only`
stays in `default`, another workspace's own agent needs to be shared first,
and an isolated workspace refuses an agent of its own that holds tools
reaching outside. `default` and `system` cannot be deleted, and deleting an
attached workspace only detaches it: the directory stays. Under
`AUTH_MODE=multi` the agent acts for the person who started the chat: creating
needs a signed in account (who becomes the owner), and changing or deleting
needs the workspace's owner or an administrator. A workspace that person
cannot see is "not found".

Isolation, secrets, hooks and the tool policy, members, environment variables,
the web domain policy and connectors are not reachable from these tools. An
agent able to change them could switch off the fence it runs behind or grant
itself access, so a person changes them on the workspace's Settings tab.

`delete_workspace` is on the approval list (`tools/approval.py`), and the
main agent's own tool policy marks it `always_ask`, so the call waits for a
person even where the workspace's approval gate is off.

## The default workspace

`default` always exists and cannot be deleted. Agents marked
`default_workspace_only` are hidden everywhere else.

## The system workspace

`system` is seeded at startup (turn it off with `SYSTEM_WORKSPACE=false`). Its
project is a git copy of this repository under the state directory, and a
scheduled loop proposes fixes to the service there as branches, never pushing.
See [system-workspace](system-workspace.md).

## Gotchas

- Removing a custom agent from a workspace does not delete it; it stays in the
  registry and in any other workspace that has it.
- Agent capacity is per workspace. A workspace that is not `default` caps how
  many nodes and sessions one agent may hold.
- Files live in the workspace folder, not in the project folder, unless the
  agent was told a project. A file "gone missing" is usually one level up.

Related: [projects](projects.md), [agents](agents.md), [memory](memory.md), [costs](costs.md), [system-workspace](system-workspace.md).
