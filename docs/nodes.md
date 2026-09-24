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

## Environment

Starting a node can name an [environment](environments.md), an execution
profile shaping its mode, image, packages, network fence, limits and plain
variables. Without one, the node gets its workspace's default environment, if
any. The node record carries `environment_id` and `environment_name` once
resolved, shown in its row. An explicit id that is unknown, archived or
belongs to another workspace refuses the start rather than silently landing
the node somewhere else; a workspace with no default environment starts a
node exactly as it always did.

## Reading a node's state

The record and the process are two different things. A node whose row says
`running` but whose pid is gone is the single most common cause of "tasks never
start". The status sync notices this, and the Service Agent reports it directly.

Each node writes stdout and stderr to its own log file, which is the first place
to look when it starts and immediately stops.

## Exposure

A node can be exposed over HTTP with a token, so something outside can send it
work. Unexpose removes the binding.

`POST /api/external/{token}/run` is throttled per client address:
`AGENTS_HUB_EXTERNAL_RATE_PER_MINUTE` requests in any 60 seconds (default 30,
0 turns it off), counted in memory per API replica. Known and unknown tokens
share the window, so guessing tokens is as slow as flooding a real node. Past
the limit the route answers `429` with a `Retry-After` header (seconds) and
`{"detail": ..., "retry_after": n}`; the refusal lands in the node's connection
log with status 429 when the token belongs to a node, and nowhere when it does
not. Tokens are compared in constant time, here and on the node's own `/run`.

Related: [instances](instances.md), [sessions-and-runs](sessions-and-runs.md), [containers](containers.md), [service-health](service-health.md), [environments](environments.md).
