---
title: "Orchestrated Delivery"
description: "Let the system route a task to specialist agents automatically"
---

# Orchestrated Delivery

The orchestrator evaluates each task and picks the best-fit agent for the job. It can also continue the workflow after each agent completes, handing off to the next stage automatically.

## What you get

A task that moves through multiple agents without manual intervention. Watch the routing log to see why each agent was chosen, then watch the follow-up logic chain agents together into a delivery pipeline.

## Before you start

- `AGENT_EXECUTION_MODE=local`
- A running orchestrator node with these settings:
  - `enabled=true`
  - `assignment_mode=live`
  - `followup_mode=continuous`
  - `wait_for_completion=false`

## Steps

1. Create a workspace named `example-multi-agent`.
2. Go to **Nodes** and start a new `orchestrator` node. Confirm it reaches running state.
3. In the **Orchestrator** page for the workspace, turn on orchestration and set assignment mode to `live` and follow-up mode to `continuous`.
4. Go to **Tasks** and create a realistic task: "Design and implement a simple feedback form workflow with validation, storage, and a review step".
5. Create the task without assigning an agent. The orchestrator will pick one.
6. Go to **Sessions** and watch the routing log. It explains which agent was chosen and why.
7. After the first agent finishes, watch the follow-up: the orchestrator may reassign the task to a reviewer or next-stage specialist.
8. Inspect the **Routing Log** to see the decision tree the orchestrator used.

## Where to read more

Learn how the orchestrator makes routing decisions in [Tasks](/guide/tasks). See agent and task lifecycle in [Sessions and Runs](/guide/sessions-and-runs).

![Orchestrated delivery workflow](/screenshots/recipes/orchestrated-delivery.png)
