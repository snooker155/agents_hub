---
title: "Local Models"
description: "Point Agents Hub at a locally served model backend"
---

# Local Models

Instead of sending requests to a hosted provider, you point Agents Hub at a local model server such as Ollama or LM Studio. Useful for private development, lower costs and experimentation with local models.

## What you get

Agents Hub running against a local model. Chat, tasks and flows all use the local backend. Switch between models without changing code.

## Before you start

Have a local model server running. Choose one:

- **Ollama:** Download from ollama.ai, run `ollama serve`, then `ollama pull` a model.
- **LM Studio:** Download from lmstudio.ai, select a model, and click Start Server.

## Steps

1. Start your local model server and confirm it is serving on localhost.

2. For Ollama, set in `.env`:
```env
DEFAULT_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama2-7b
AGENT_EXECUTION_MODE=local
```

3. For LM Studio, set in `.env`:
```env
DEFAULT_PROVIDER=lmstudio
LMSTUDIO_BASE_URL=http://localhost:1234
LMSTUDIO_MODEL=openai/llama2-7b
AGENT_EXECUTION_MODE=local
```

4. Start the backend and frontend.

5. Go to **Settings** and confirm the local provider is reachable.

6. Open **Chat** or create a **Task**.

7. Run a small test: "Summarize the role of the orchestrator in this project".

8. Optionally, go to **Models** and set a workspace override to test local while keeping other workspaces on the global default.

## Where to read more

Learn about providers and model configuration in [Models](/guide/models) and [Settings](/guide/settings).

![Local models workflow](/screenshots/recipes/local-models.png)
