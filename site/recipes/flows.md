---
title: "Flows"
description: "Create a graph-based pipeline that coordinates several agents"
---

# Flows

A flow is a directed graph of agents, conditions, and transforms. Nodes run in order with their outputs feeding into the next stage. Parallel nodes run at the same time. Use flows when you want to design the path of work ahead of time.

## What you get

One flow run with several node-level runs executed according to the graph. Watch per-node progress stream in real time. See the final result and review the cost estimate before execution.

## Before you start

- Local execution mode
- A dedicated workspace (optional but recommended)
- Explicit model choices for reproducibility

## Steps

1. Create a workspace named `example-flow`.
2. Go to **Flows** and create a new flow.
3. In the **Flow Editor**, drag nodes onto the canvas:
   - Add an intake or analysis node (an agent).
   - Add an implementation or drafting node.
   - Add a review node.
4. Connect them with edges from output to input.
5. Save the flow with a clear name.
6. Go back to **Flows** and click Run on your flow.
7. When prompted, approve the cost estimate.
8. Watch the run stream: each node reports progress as it finishes.
9. Once the flow is done, inspect:
   - The flow status (success or failed)
   - Each node's logs and output
   - The total cost and token usage
10. Click the flow run in **Sessions** to see the full execution record.

## Where to read more

Learn how flows work, including parallelism, error handling and checkpointing in [Flows](/guide/flows). See loops and teams in [Loops](/guide/loops) and [Teams](/guide/teams).

![Flows workflow](/screenshots/recipes/flows.png)
