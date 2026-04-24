# Example 08: Local Model Lab

## Goal

Run Agents Hub against a locally served model backend such as Ollama or LM Studio.

## Best For

- local experimentation
- private development workflows
- lower-cost testing

## Main Idea

Instead of routing requests to a hosted provider, you point the system at a local model server and select models globally or per workspace.

## Recommended Settings

For Ollama:

```env
DEFAULT_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gpt-oss:20b
AGENT_EXECUTION_MODE=local
```

For LM Studio:

```env
DEFAULT_PROVIDER=lmstudio
LMSTUDIO_BASE_URL=http://localhost:1234
LMSTUDIO_MODEL=openai/gpt-oss-20b
AGENT_EXECUTION_MODE=local
```

## How To Run

### 1. Start your local model server

Use either:

- Ollama
- LM Studio

### 2. Update `.env`

Configure provider, base URL, and model.

### 3. Start Agents Hub

Launch backend and frontend as usual.

### 4. Validate provider connectivity

From the dashboard settings page, confirm the local provider is reachable.

### 5. Run a small task or chat request

Suggested test:

`Summarize the role of the orchestrator in this project`

### 6. Optionally set a workspace override

Use one workspace to test a local model while keeping the global default unchanged elsewhere.

## Expected Outcome

You should be able to run tasks and chats using the local model backend instead of a hosted provider.
