# Example 03: Human-In-The-Loop Approval

## Goal

Use the orchestrator to recommend and assign an agent, but require human approval before execution starts.

## Best For

- risky tasks
- production-sensitive changes
- supervised internal usage

## Main Idea

The orchestrator still performs routing, but it stops after assignment so you can inspect the decision before the agent starts.

## Recommended Settings

- `AGENT_EXECUTION_MODE=local`
- workspace orchestrator `enabled=true`
- `assignment_mode=manual`
- `followup_mode=single`
- `wait_for_completion=false` or `true`

## How To Run

### 1. Start the app

Launch backend and frontend normally.

### 2. Create a workspace

Create a workspace named `example-approval`.

### 3. Start an orchestrator node

Start a running `orchestrator` node from the dashboard.

### 4. Configure workspace orchestration

Set:

- Enabled: on
- Assignment mode: `manual`
- Follow-up mode: `single`

### 5. Create a task

Example:

`Review the current login flow architecture and propose a safe refactor plan`

### 6. Review the assignment

Inspect:

- chosen agent
- routing reason
- assigned parameters

### 7. Approve or reject

Then either:

- approve and start the agent
- reject and reassign

## Expected Outcome

You should be able to control the execution boundary manually while still using orchestrator routing.
