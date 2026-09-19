# Nodes

A node is a long-running agent process. It polls for work and executes it,
which is what lets a task run without a chat window open.

## Two node types

- **worker** (default) — runs a polling loop, consuming tasks from the queue.
- **service** — runs an HTTP server only, no task polling. For agents exposed to
  something outside this product.

## Starting and stopping

Start one per agent from the Agents or Nodes page. A node belongs to a workspace
and counts against that workspace's capacity: outside `default`, a workspace
caps how many nodes and sessions one agent may hold.

Stopping a node **fails every session in progress on it**. That is not a
side effect to discover afterwards; it is the reason to check what is running
first.

## Reading a node's state

The record and the process are two different things. A node whose row says
`running` but whose pid is gone is the single most common cause of "tasks never
start". The status sync notices this, and the Service Agent reports it directly.

Each node writes stdout and stderr to its own log file, which is the first place
to look when it starts and immediately stops.

## Exposure

A node can be exposed over HTTP with a token, so something outside can send it
work. Unexpose removes the binding.

Related: [instances](instances.md), [sessions-and-runs](sessions-and-runs.md), [containers](containers.md), [service-health](service-health.md).
