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
 ├── scenario              agents acting in a simulated world, tick by tick
 └── view                  a chart, graph or 3D scene an agent built
```

Running any of these produces the same three records, which is what makes the
system observable at all:

- **run** — one agent invocation. The atom. Everything is made of these.
- **session** — a conversation: the runs that belong together.
- **instance** — a live copy of an agent, with its own state and history.

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
- **Flows, loops, teams and scenarios** are for work that needs more than one
  agent, or more than one pass.

## Two things worth knowing early

**Money is real.** Every run costs tokens. A scenario is every role acting every
tick; a team is members times rounds; a loop is a whole flow repeated. The tools
that start those refuse until you have approved them, and the refusal shows the
estimate. See [costs](costs.md).

**Capabilities are enforced.** An agent that can read your private data, ingest
text from outside, *and* send data out is a data-exfiltration primitive. The
product refuses that combination rather than warning about it. See
[tools-and-capabilities](tools-and-capabilities.md).
