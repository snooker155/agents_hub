---
title: "Approval"
description: "Route with the orchestrator but require human approval before execution"
---

# Approval

Use the orchestrator to recommend an agent and assign the task, but stop and wait for your approval before the agent starts. This gives you a chance to inspect the routing decision and either approve or reassign.

## What you get

A task assigned by the orchestrator that waits in `pending` status until you say go. You can review the chosen agent, its reasoning, and any parameters before execution. This combines automated routing with human oversight.

## Before you start

- `AGENT_EXECUTION_MODE=local`
- A running orchestrator node with these settings:
  - `enabled=true`
  - `assignment_mode=manual`
  - `followup_mode=single`

## Steps

1. Create a workspace named `example-approval`.
2. Go to **Nodes** and start an `orchestrator` node.
3. In the **Orchestrator** page, turn on orchestration and set assignment mode to `manual`.
4. Go to **Tasks** and create a task: "Review the current login flow architecture and propose a safe refactor plan".
5. Create the task without assigning an agent. The orchestrator assigns it but does not start it.
6. Go to **Sessions** and check the routing log. It shows which agent was chosen and the reasoning.
7. Open the task details. The agent is assigned but the task is in `pending` status.
8. Inspect the assigned agent and parameters.
9. Approve by moving the task to `ready` or `in_progress`, or reject by reassigning to a different agent.

## Where to read more

Learn about task status transitions and approval flows in [Tasks](/guide/tasks). See the full status model in the guide.

![Approval workflow](/screenshots/recipes/approval.png)
