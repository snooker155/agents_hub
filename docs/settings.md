# Settings

Credentials and connection details. What models are offered and which is default
lives on the [models](models.md) page, not here.

## What is here

- **API keys** per provider, and the base URL when it is not the default.
- **Custom backends** — any OpenAI-compatible endpoint.
- **Local models** — Ollama and LM Studio, which need a running local server
  rather than a key.
- **API auth** — a token that protects this service's own API.
- Environment variables, per workspace.

Connectors are **not** here any more. Telegram, GitHub/GitLab and Blender moved
to Connect → Connectors ([connectors](connectors.md)), because a connector is
something you attach rather than a credential you set. Old `/settings/telegram`
links redirect there.

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
