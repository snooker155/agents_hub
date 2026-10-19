# What this service is

An agent hub: a place to define AI agents, give them tools and memory, and run
them, alone or in groups, against real work in a workspace.

## How the objects nest

Almost the whole learning curve is knowing what contains what.

```
workspace                  an isolated folder + its own agents, settings and memory
 ├── project               one codebase or effort inside the workspace
 │    └── task             a unit of work, with subtasks and dependencies
 ├── agent                 a definition: prompt + tools + model + memory
 ├── flow                  a graph of agents, run as one pipeline
 │    └── loop             a flow re-run until a judge says it is good enough
 ├── team                  a roster of agents working over a shared board
 └── view                  a chart, graph or 3D scene an agent built
```

Running any of these produces the same three records, which is what makes the
system observable at all:

- **run**: one agent invocation. The atom. Everything is made of these.
- **session**: a conversation, the runs that belong together.
- **instance**: a live copy of an agent, with its own state and history.

## Two kinds of agent

**System agents** ship with the product. They are present in every workspace,
cannot be removed from one, and their tools and descriptions are kept in sync
with what the product ships. They are what makes the service work out of the
box. See [system-agents](system-agents.md).

**Custom agents** are yours. You create them, add them to the workspaces you
want, and nothing the product ships will change them.

## Where work actually happens

- **Chat** is the front door: talk to any agent directly, no setup.
- **Tasks** are for tracked work: assigned, staged, resumable.
- **Flows, loops and teams** are for work that needs more than one
  agent, or more than one pass.

## The menu

The sidebar follows the same tree in five groups: **Conversation** (Chat),
**Work** (projects, tasks, the plan, artifacts, flows, loops, teams, the
orchestrator, deployments, workspaces), **Agents and library** (agents, models,
skills, memory, the marketplace, the toolbox, instances), **Integrations**
(connectors) and **Records** (sessions, messages, run groups, web logs,
guardrails, containers, health, settings, docs).

It has two modes. The **simple menu** keeps fourteen pages, the ones a
newcomer needs, workspaces among them, plus Settings and Docs; it is the
default. The **full menu** shows every page. The toggle at the foot of the menu
switches between them, and the browser remembers the choice. No page is
removed in either mode, and the simple menu never changes shape: a page outside
it lights up the row it belongs to (Deployments opened from the Plan page
lights up Plan, Loops and Teams light up Flows, Connectors, Sessions and the
other records light up Settings).

## Two things worth knowing early

**Money is real.** Every run costs tokens. A team is members times rounds; a loop is a whole flow repeated. The tools
that start those refuse until you have approved them, and the refusal shows the
estimate. Each run page shows what that run cost.

**Capabilities are enforced.** An agent that can read your private data, ingest
text from outside, *and* send data out is a data-exfiltration primitive. The
product refuses that combination rather than warning about it. See
[tools-and-capabilities](tools-and-capabilities.md).
