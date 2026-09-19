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

## Gotchas

- A flow can only reference agents available in its workspace. One built
  elsewhere may not be runnable here.
- The terminal node's agent is the default judge when the flow is wrapped in a
  [loop](loops.md), so the node order is also a decision about who evaluates.
- A flow that fails mid-graph leaves the completed nodes' output behind. Read it
  before re-running the whole thing.

Related: [loops](loops.md), [teams](teams.md), [agents](agents.md).
