# Example 01: Basic Task Assistant

## Goal

Use Agents Hub as a simple local task assistant: create a workspace, create one task, assign one agent, and inspect the result.

## Best For

- first-time setup validation
- simple implementation or research tasks
- quick manual agent runs

## Main Idea

This is the smallest useful workflow in Agents Hub. You create the task yourself, choose the agent yourself, and review the output yourself.

## Recommended Settings

- `AGENT_EXECUTION_MODE=local`
- `DEFAULT_PROVIDER=openai` or another configured provider
- orchestrator disabled or left unused

## How To Run

### 1. Start the app

Backend:

```bash
python -m uvicorn dashboard.backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Frontend:

```bash
cd dashboard/frontend
npm run dev -- --host 0.0.0.0 --port 5173
```

### 2. Create a workspace

In the dashboard:

1. Open Workspace Manager
2. Create a workspace named `example-basic`

### 3. Create a task

Example task:

`Write a short README section describing this project`

### 4. Assign an agent manually

Good starter agents:

- `researcher_agent`
- `swe_agent`
- `decomposer`

### 5. Start the run

Run the agent from the task details page and wait for completion.

### 6. Inspect output

Review:

- task status
- run logs
- result output
- any created or changed files

## Expected Outcome

You should see one completed or resolved task with one agent run and one result.

## CLI Variant

```bash
python cli.py workspace list
python cli.py task list
python cli.py agent list
```
