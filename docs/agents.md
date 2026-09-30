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

## The example roster

`examples/agents/` holds prompts that ship with the repository for reference
but are not seeded: a waterfall delivery team, an event-planning crew, two
interactive-fiction helpers, and a three-stage job-search pipeline, grouped by
theme, plus a `misc/` set. None of them sit under `agents/definitions/` — a
folder there is only ever read for an id `bootstrap/agents.json` already
seeds or one an operator has imported, so an unconnected folder was dead
weight rather than a working agent. See `examples/agents/README.md` for what
each set demonstrates and the two API calls that bring one into a running hub.

## System vs. custom

Every agent is exactly one of the two. System agents ship with the product and
are present in every workspace; custom agents are yours. The Agents page marks
system agents explicitly, and their Tools and Configuration tabs carry a warning
because editing either changes what the product is built on. See
[system-agents](system-agents.md).

## Versions

An agent's definition has a fingerprint: a sha256 hash over its tools, model,
provider, reasoning settings, memory binding, capability override, approval
lists, delegates, and the text of `instructions.md`, `capabilities.md` and
`usage.md`. It is stable across processes, so the same definition always hashes
the same way regardless of dict or JSON key order.

Every [run](sessions-and-runs.md) records this hash as `definition_hash`. A run
never fails because the hash could not be computed; it is simply left off the
record.

The registry keeps a history of an agent's past definitions. Right before a
change replaces the stored record, or the dashboard's definition editor
rewrites one of the three markdown files, the state about to be overwritten is
snapshotted, unless it is already identical to the latest snapshot or the
incoming write changes nothing. The Config tab's Version History section lists
each snapshot with a summary of what changed since the one before it: tools
added or removed, a model change, which markdown files were touched.

From there, any version can be diffed against the current state or another
version, shown as a unified diff per part (spec, instructions, capabilities,
usage). Rollback restores a version's record and markdown files, going through
the same `add_agent` path a normal edit takes, so the capability guard still
runs and a blocked tool combination is refused the same way. Rolling back a
system agent marks it as user modified, the same as any other edit, so
bootstrap sync leaves it alone afterward.

## Versions and pinning

A task or a scheduled `agent_task` job can be pinned to a stored agent version via `agent_version`; `null` means the live definition. The launcher passes `--definition-version N` to the runtime, which builds the agent from that stored version. A version whose row has been deleted falls back to the live definition (logged).

Every agent run record carries `agent_version`: the pin if it still existed, the A/B experiment arm's version if an experiment routed the run, or the version matching the live definition's hash. The hash is snapshotted on first use, so it is never null for a registered agent.

The run page panel shows that version against the agent's current one and offers rollback: `POST /api/runs/{run_id}/rollback-agent` (same checks as the agent's own rollback route: refuses a version whose tool set the capability guard now blocks; audited `agent.rollback`).

The task page has a version picker (live or a stored version, via `PATCH /api/tasks/{id}` with `agent_version`, validated against the assigned agent's versions). The Deployments page job editor has the same picker for an `agent_task` job with an agent; `agent_version` requires `agent_id` on the job and is copied onto the task at fire time.

A subtask never inherits its parent's pin, because a subtask usually runs a different agent. Environment and budget are inherited instead.

## Delegation

`run_agent_tool` hands a self-contained goal to another agent and returns its
output. The child sees **only** the input string: not your conversation, not
your instructions. Anything it needs must be in that string.

An agent may carry a `delegates` allowlist restricting who it can call. An empty
list means no restriction.

Inside a task the tool is `delegate_task_tool`. It creates a subtask of the task
the agent is working, launches the chosen agent on it as a real run (its own
process or container, the task's environment and money cap, the live stream),
waits for it by default, and returns the delegate's output together with the
subtask and run ids. The `model` argument picks any enabled catalog model for
that one run, as `provider/model`; `list_models_tool` lists them with the
workspace default and the caller's own model. A delegate keeps its own model
when none is given. Chains stop at `AGENTS_HUB_DELEGATION_MAX_DEPTH` levels (3),
the wait is bounded by `AGENTS_HUB_DELEGATION_WAIT` seconds (900) or the call's
`timeout_seconds`, and stopping the parent task's run stops the delegate. The
same `delegates` allowlist and self-delegation rule apply. With `wait` false the
call returns after the launch; `get_agent_status_tool` and `get_task_result` on
the subtask id read the outcome later, and the subtask shows under its parent
on the task page with the model the run used.

A handoff is the other kind of hand-over: instead of asking another agent for a
result, the agent gives the conversation to it, and that agent answers the user
from then on. The targets (`handoffs`) and the history the receiver sees by
default (`handoff_history`) are set on the agent's Tools tab. See
[handoffs](handoffs.md).

## Gotchas

- Editing a system agent marks it as yours and stops it tracking shipped
  updates. That is deliberate, and irreversible without editing the registry.
- An agent with a memory pool bound gets pool-bound memory tools with different
  names, so it never has to guess a pool id.
- `run_agent_tool` is refused inside a tracked task. Use `delegate_task_tool`
  there (a subtask run, optionally on another model), or assign and start.

Related: [agent-loop](agent-loop.md), [chat](chat.md), [tools-and-capabilities](tools-and-capabilities.md), [instances](instances.md), [marketplace](marketplace.md), [imported-agents](imported-agents.md), [tasks](tasks.md).
