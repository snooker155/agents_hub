# Example 06: Flow-Based Automation

## Goal

Create and run a graph-based flow that coordinates several agents or processing steps.

## Best For

- experimentation
- repeatable multi-step pipelines
- graph-oriented orchestration design

## Main Idea

Flows provide a more explicit visual execution model than plain task assignment. They are useful when you want to shape the path of work ahead of time.

## Recommended Settings

- local execution
- dedicated workspace for the flow
- explicit model choices for reproducibility

## How To Run

### 1. Create a workspace

Use a workspace such as `example-flow`.

### 2. Open the Flow Editor

Create or load a flow with several logical stages.

Simple example:

1. intake or analysis node
2. implementation or drafting node
3. review node

### 3. Save the flow

Give it a clear name.

### 4. Trigger the flow

Run it against a task or description in the selected workspace.

### 5. Inspect flow runs

Review:

- flow status
- node-level runs
- linked session data
- resulting outputs

## Expected Outcome

You should see one flow-level run and several node-level runs tied together through a shared session.
