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

## The default workspace

`default` always exists and cannot be deleted. Agents marked
`default_workspace_only` are hidden everywhere else.

## Gotchas

- Removing a custom agent from a workspace does not delete it; it stays in the
  registry and in any other workspace that has it.
- Agent capacity is per workspace. A workspace that is not `default` caps how
  many nodes and sessions one agent may hold.
- Files live in the workspace folder, not in the project folder, unless the
  agent was told a project. A file "gone missing" is usually one level up.

Related: [projects](projects.md), [agents](agents.md), [memory](memory.md), [costs](costs.md).
