---
title: "Basic Task"
description: "Create one task, assign one agent, and review the result"
---

# Basic Task

This is the smallest complete workflow: create a task, choose an agent to work on it, and review the output. No orchestrator, no flows, no long-running infrastructure.

## What you get

A single completed task with one agent run and one result. You see how to move a task through the Kanban board, how agents pick up work, and what the run logs and result output look like.

## Before you start

- `AGENT_EXECUTION_MODE=local`
- `DEFAULT_PROVIDER=openai` or another configured provider
- The orchestrator disabled or left unused

## Steps

1. Open the **Workspace Manager** and create a workspace named `example-basic`.
2. Go to **Tasks** and create a new task with a description: "Write a short README section describing this project".
3. Assign the task to an agent. Start with `researcher`, `swe_agent`, or `decomposer`.
4. Move the task to `ready` on the Kanban board (or start it directly from the task details).
5. Wait for the run to finish. Watch the progress in **Sessions**.
6. Open the completed task and inspect: the task status, the run log, the result output.
7. Optionally, use the CLI to list tasks and filter by status:

```bash
ah task list --status todo
ah task list --workspace example-basic
```

## Where to read more

Learn more about task lifecycle and status transitions in the [Tasks guide](/guide/tasks). See agent selection and capabilities in [Agents](/guide/agents).

![Basic task workflow](/screenshots/recipes/basic-task.png)
