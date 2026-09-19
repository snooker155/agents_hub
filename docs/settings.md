# Settings

Credentials and connection details. What models are offered and which is default
lives on the [models](models.md) page, not here.

## What is here

- **API keys** per provider, and the base URL when it is not the default.
- **Custom backends** — any OpenAI-compatible endpoint.
- **Local models** — Ollama and LM Studio, which need a running local server
  rather than a key.
- **API auth** — a token that protects this service's own API.
- **Connectors** — Telegram, GitHub/GitLab, and **Blender**: the geometry engine
  agents model 3D objects with. Point it at an installed Blender (the usual
  install locations are found automatically), cap how many engines may run at
  once, and see the ones running right now, including those started by agent
  processes. Agents never run Blender Python; they call a fixed set of geometry
  operations. See [views](views.md).
- Environment variables, per workspace.

## Per-workspace overrides

A workspace can override the model default, the execution mode (in-process or
Docker) and environment variables. Anything not overridden falls through to the
global setting.

## Reading the health of this

The health snapshot reports which keys are set (as booleans, never values),
which provider is default, and whether API auth is on. That is the quickest way
to check a configuration problem without opening the page. See
[service-health](service-health.md).

Related: [models](models.md), [workspaces](workspaces.md).
