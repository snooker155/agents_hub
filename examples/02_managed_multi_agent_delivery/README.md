# Example 02: Managed Multi-Agent Delivery

## Goal

Run a task through orchestrator-led multi-agent execution, where the system chooses specialist agents and continues the workflow automatically.

## Best For

- feature delivery
- staged implementation work
- demonstrating orchestration behavior

## Main Idea

You start an orchestrator node and let it route a task to the most appropriate specialist agents. In continuous follow-up mode, the orchestrator can re-enter after worker completion and continue the workflow.

## Recommended Settings

- `AGENT_EXECUTION_MODE=local`
- workspace orchestrator `enabled=true`
- `assignment_mode=live`
- `followup_mode=continuous`
- `wait_for_completion=false`

## How To Run

### 1. Start backend and frontend

Use the standard startup commands from the main README.

### 2. Create a workspace

Create a workspace named `example-multi-agent`.

### 3. Start an orchestrator node

In the dashboard:

1. Open Nodes
2. Start a node for `orchestrator`
3. Confirm that it reaches running state

### 4. Configure orchestrator settings

In the Orchestrator page for the workspace:

- Enabled: on
- Assignment mode: `live`
- Follow-up mode: `continuous`
- Wait for completion: off

### 5. Create a realistic task

Example:

`Design and implement a simple feedback form workflow with validation, storage, and a review step`

### 6. Observe orchestration

Watch:

- routing log
- assigned agents
- follow-up sessions
- final task status

## Expected Outcome

You should see multiple linked runs and at least one orchestrator handoff to a specialist agent.
