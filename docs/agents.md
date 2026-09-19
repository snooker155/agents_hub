# Agents

An agent is a definition, not a process: a prompt, a set of tools, a model, and
optionally memory. Running it produces a [run](sessions-and-runs.md).

## What an agent is made of

**The prompt** is assembled from three markdown files in the agent's definition
folder, concatenated in order:

- `instructions.md` (required) — the system prompt: who it is and how to work
- `capabilities.md` (optional) — what it can do, in its own words
- `usage.md` (optional) — when to invoke it and when not to

All three go into the system prompt, which is why an agent can describe itself
accurately: it is carrying the description. It is also why a `capabilities.md`
that names a tool the agent does not hold is a defect, not a harmless
inaccuracy, and a test enforces that they match for system agents.

**The tool list** decides what it can actually do. Tools are granted by name,
never by default. See [tools-and-capabilities](tools-and-capabilities.md).

**The model** can be inherited from the workspace or pinned per agent, along
with temperature, max tokens and reasoning settings.

**Memory** is assigned per workspace, not on the agent. See [memory](memory.md).

## Where agents come from

Besides the ones you author here: the [marketplace](marketplace.md) holds agents
published from other workspaces, ready to clone, and an agent that already lives
in its own git repository can be [imported](imported-agents.md) and run behind an
HTTP contract without its code ever entering this process.

## System vs. custom

Every agent is exactly one of the two. System agents ship with the product and
are present in every workspace; custom agents are yours. The Agents page marks
system agents explicitly, and their Tools and Configuration tabs carry a warning
because editing either changes what the product is built on. See
[system-agents](system-agents.md).

## Delegation

`run_agent_tool` hands a self-contained goal to another agent and returns its
output. The child sees **only** the input string: not your conversation, not
your instructions. Anything it needs must be in that string.

An agent may carry a `delegates` allowlist restricting who it can call. An empty
list means no restriction.

## Gotchas

- Editing a system agent marks it as yours and stops it tracking shipped
  updates. That is deliberate, and irreversible without editing the registry.
- An agent with a memory pool bound gets pool-bound memory tools with different
  names, so it never has to guess a pool id.
- `run_agent_tool` is refused inside a tracked task. Use assign and start there.

Related: [chat](chat.md), [tools-and-capabilities](tools-and-capabilities.md), [instances](instances.md), [marketplace](marketplace.md), [imported-agents](imported-agents.md).
