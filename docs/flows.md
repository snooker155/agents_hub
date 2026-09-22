# Flows

A flow is a graph of agents run as one pipeline: nodes do work, edges define
order, and the whole thing streams as it goes.

## Building one

Drag from the palette onto the canvas: agents, processors, conditions,
transforms. Connect them to define the order of work. The **Flow Creator** has
its own chat on the page and can design, validate and create a flow from a
description of what you want.

Validation is separate from creation on purpose: a design can be checked before
it costs anything to run.

## Node kinds

- **Agent** — runs one agent with the input it receives.
- **Condition** — routes down one branch or another.
- **Transform** — reshapes data between nodes without a model call.
- **Processor** — non-agent work in the middle of a pipeline.

## Running

`run_flow_tool` starts it, and refuses until you have approved: a flow is as
many model calls as it has agent nodes. It returns a run id immediately and
streams per-node progress; it does not block the conversation.

Nodes that do not depend on each other run at the same time. A node starts as
soon as every node feeding it has finished, up to `max_parallel` (4 unless the
flow says otherwise), so two branches of a fan-out are two branches in fact and
not only on the canvas.

## What a run costs before it runs

`GET /api/flows/{id}/estimate` prices one pass: one call per agent node, at the
model each agent resolves to, from the prices on the Models page. The same
figure comes back as `estimated_cost` when you start a run. It is an estimate,
not a quote: a node that uses tools takes several steps and costs more, and a
branch that is not taken costs nothing.

## When a node fails

`on_error` on the flow decides what a failure does to everything else:

- **fail_fast** (default) — nodes already running finish, nothing new starts.
- **continue** — a node still runs when at least one of its inputs succeeded, so
  a join tolerates one failed branch. The run is still reported as failed.
- **isolate_branch** — everything downstream of the failure is skipped, and the
  branches beside it run to the end.

Per node, `retry: {max, backoff_seconds}` repeats a failed attempt and
`timeout_seconds` bounds each attempt. A timed-out agent node counts as failed:
the model call itself cannot be interrupted, so what stops is the record of it,
and the flow moves on according to `on_error`.

## Asking a person mid-flow

A **human interrupt** node stops the flow and asks. The task goes to
`awaiting_input` with the question on it, exactly as an agent's `ask_user` does,
and the run writes a checkpoint and ends. Answering resumes the flow from that
checkpoint with the answer in flow state under the node's output key, so the
node after it reads what the person actually said. In flow chat, where there is
no task to park, the node simply answers with its question.

## Checkpoint and resume

After every node, the run records what is done, what was skipped and the whole
flow state. If the process dies, the run is not lost: `POST
/api/flows/runs/{run_id}/resume` replays the finished nodes and runs only what
is left. The watchdog does this by itself, twice at most, for a run whose
heartbeat has gone quiet; a run with no checkpoint, or one that has used up its
attempts, is failed as before.

### Why not LangGraph for flows

Checkpoint and resume here are built on the hub's own `FlowState` and DAG rather
than on LangGraph's checkpointer. A flow is the hub's own graph: its nodes are
agents and entities from this registry, its state is `FlowState` with the
mutability rule, and its runs are flow-run records the dashboard already reads.
A LangGraph checkpointer saves a LangGraph graph, which is not what a flow is,
so adopting it would mean translating the DAG both ways and keeping two
sources of truth about where a run had got to. Moving the agent loop itself off
AgentExecutor is a separate question, and this is not it.

## Gotchas

- A flow can only reference agents available in its workspace. One built
  elsewhere may not be runnable here.
- The terminal node's agent is the default judge when the flow is wrapped in a
  [loop](loops.md), so the node order is also a decision about who evaluates.
- A flow that fails mid-graph leaves the completed nodes' output behind. Read it
  before re-running the whole thing, or resume the run instead of starting one.
- Parallel nodes write to one shared state. With `mutability: false`, two
  branches writing the same key is a failure in whichever branch arrives second,
  which is the point of the setting.

Related: [loops](loops.md), [teams](teams.md), [agents](agents.md).
