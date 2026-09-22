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

## API auth

The service's own API can be closed to callers that do not present a token.
This is off by default, unlike a provider key: nothing here checks it until the
operator turns it on.

- **Server side.** Set `AGENTS_HUB_API_TOKEN` in `.env` and restart the
  backend. Once set, every `/api` request must present it, as an
  `Authorization: Bearer` header, an `X-Api-Token` header, or a `?token=` query
  parameter (used by the browser's EventSource, which cannot set headers).
  `/api/ingest` is exempt: an external run reports in with its own connection
  credential instead.
- **Browser side.** System → API access holds this browser's token, kept in
  its `localStorage` rather than in a workspace or the `.env`: it belongs to
  the browser, not to a workspace. Saving it attaches it to every request this
  browser makes; clearing the field and saving removes it. The Test button
  calls `GET /api/health` with the current token and reports whether the
  backend accepted it.

## Reading the health of this

The health snapshot reports which keys are set (as booleans, never values),
which provider is default, and whether API auth is on. That is the quickest way
to check a configuration problem without opening the page. See
[service-health](service-health.md).

Related: [models](models.md), [workspaces](workspaces.md).
