# Workspace roles

A role is a kind of work, and each workspace says which of its agents does it.
The system agents do not hand code to `swe_agent`; they hand it to `@coder`,
and the workspace decides who that is. Until it decides, the product's own
agent holds the role. Swapping the built-in coder for Claude Code, Codex or
Aider is one setting on the workspace, not an edit of every agent that
delegates code. The code is `agents/roles.py`.

## The roles

| Role | Reference | Default agent | Called by (shipped) |
|------|-----------|---------------|---------------------|
| Coder | `@coder` | `swe_agent` | main-agent, orchestrator, universal_agent, the creators |
| Code reviewer | `@reviewer` | `code_reviewer` | the same |
| Planner | `@planner` | `planner` | the same |
| Visualizer | `@visualizer` | `visualizer` | the same, analyst |
| Web search | `@web_search` | `web_searcher` | researcher, verifier, sourcer |
| Verifier | `@verifier` | `verifier` | writer (handoff) |
| Researcher | `@researcher` | `researcher` | your own agents |
| Analyst | `@analyst` | `analyst` | your own agents |
| Writer | `@writer` | `writer` | your own agents |

A role nobody calls yet is still worth binding: your own agents can name it in
their delegates, and the settings page shows who calls each role.

## Choosing who holds a role

Workspace settings, **Agent roles**. Each role lists the agents of this
workspace; the default is marked. The change saves at once
(`PUT /api/workspaces/{name}/roles/{role}` with `{"agent_id": ...}`, an empty
id returns the role to its default). Another workspace keeps its own choice.

To make an outside coding agent the coder:

1. On the Agents page, **Add Claude Code**, **Add Codex** or **Import from
   repo** for Aider ([imported agents](imported-agents.md)).
2. Add it to the workspace (workspace page, Agents tab).
3. In the workspace settings, Agent roles, pick it for Coder.

A binding is refused when the agent is not in the workspace, when the target
is another role, and when an agent that calls the role would then reach a
capability combination the guard blocks (below). If the bound agent later
leaves the workspace or is deleted, the default holds the role again and the
settings page says so.

## Where a reference works

`@role` is accepted wherever an agent is named for work:

- an agent's `delegates` and `handoffs` lists (the agent editor shows the
  roles above the agents in the delegation card);
- `run_agent_tool`, `delegate_task_tool`, `assign_agent_tool` and
  `handoff_to_agent`, as the target id.

A task assigned to `@coder` records the agent that held the role, so its
history names a real agent. `list_agents_tool` marks each holder with its
roles, and an agent whose delegates or handoffs name a role gets a short
"Roles in this workspace" section in its prompt listing who holds each one.

Agent ids cannot start with `@`.

## The capability guard

The guard judges an agent when it is saved, without a workspace, so a
reference reaches every agent that may hold the role anywhere: the default and
every workspace's binding (`agents.roles.expand_all`). Binding a role re-checks
every agent that calls it with the new holder in place, and refuses the
binding when one of them would form a blocked combination. Example: a coder
holding `run_shell` cannot become `@coder` while main-agent, which reads
private files, calls `@coder`.

An imported agent declares no tools, so the guard sees nothing in it. What it
can do lives in its own container and its own keys.

## Upgrading

The shipped system agents name roles from this release on. A system agent you
edited by hand stopped following the seed, so on the first start of this
release its `delegates` and `handoffs` are rewritten once: where the seed now
names `@coder` and the record names `swe_agent`, the id becomes the reference.
Nothing else you chose changes, and until a workspace binds the role the
reference reaches the same agent. Pinning the id again afterwards is kept.

## Gotchas

- An instruction that names an agent by id (`call web_searcher`) still
  reaches that agent only while it holds the role. The shipped instructions
  name the role.
- The workspace's main chat agent is not a role: it is the workspace's
  default chat agent, set on the workspace page.
- Flow nodes, team members and scenario roles name agents, not roles: their
  editors check each id against the registry when they are saved.

Related: [agents](agents.md), [imported-agents](imported-agents.md), [system-agents](system-agents.md), [handoffs](handoffs.md), [tools-and-capabilities](tools-and-capabilities.md), [workspaces](workspaces.md), [special-models](special-models.md).
