# Example 05: Interactive Agent Chat

## Goal

Use direct agent chat for investigation, planning, analysis, or quick help without setting up a long-running task workflow.

## Best For

- repo exploration
- architecture questions
- brainstorming
- code or log explanation

## Main Idea

The chat API creates fresh agent runs on demand and records them in session history. This is lighter than building a full orchestrated task flow.

## Recommended Settings

- `AGENT_EXECUTION_MODE=local`
- workspace override if you want chat to use a different model than the global default

## How To Run

### 1. Start the app

Launch backend and frontend normally.

### 2. Open Chat

In the dashboard:

1. Go to Chat
2. Select an agent
3. Choose a workspace if relevant

### 3. Ask a focused question

Examples:

- `Summarize the current project architecture`
- `Explain how task execution logs are stored`
- `Review this API design and suggest improvements`

### 4. Add attachments if useful

Provide snippets, notes, or file content as context.

### 5. Review the session

Inspect:

- response text
- run logs
- session history

## Expected Outcome

You should get a useful response without configuring nodes or multi-step orchestration.
