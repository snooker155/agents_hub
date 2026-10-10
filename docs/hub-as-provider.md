# The hub as a provider

The hub serves an OpenAI-compatible API under `/v1`, so any client that
speaks the OpenAI Chat Completions API (the OpenAI SDKs, an IDE plugin,
LangChain's `ChatOpenAI`, another agent framework) can call every model the
catalog enables through one address and one credential. The code is
`dashboard/backend/routes/openai_compat.py`; accounting lives in
`common/serving.py`.

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="ahk_...")
reply = client.chat.completions.create(
    model="anthropic/claude-sonnet-4-5",
    messages=[{"role": "user", "content": "Hello"}],
)
```

The hub also speaks MCP at `/v1/mcp`, for coding tools that want to ask an agent
as a tool: see [the hub as an MCP server](hub-as-mcp-server.md).

## What it serves

- `GET /v1/models`: every model the Models page has enabled, per provider,
  plus the global default from `.env` (`DEFAULT_PROVIDER` and that
  provider's `*_MODEL`) even when it is not enabled in the catalog, because
  the runtime falls back to it everywhere else. Each entry is an OpenAI model
  object with two extra keys: `{"id": "openai/gpt-4o", "object": "model",
  "owned_by": "openai", "created": <released_at or 0>, "context_window": n,
  "provider": "openai", "model": "gpt-4o"}`.
- `POST /v1/chat/completions`: `model`, `messages` (roles `system`,
  `developer`, `user`, `assistant`, `tool`; content a string or a list of
  text parts), `temperature`, `max_tokens` or `max_completion_tokens`,
  `stop`, `stream` and `stream_options.include_usage`, `tools` and
  `tool_choice`, `response_format`, `n` (only 1), `user` (accepted and
  ignored).
- Agents, as `agent:<agent_id>` models (see [Agents as models](#agents-as-models)).
- Nothing else: embeddings, images, audio and the Responses API answer 404
  in the OpenAI error shape.

The model is built with the same `build_chat_model` the agents use, so the
provider keys, base URLs and custom backends configured in Settings all
apply, and the request timeout is `LLM_REQUEST_TIMEOUT`.

## Model names

`model` is resolved in this order:

1. `default`: the global default.
2. `<provider>/<model>` exactly as `/v1/models` lists it.
3. A bare model id, looked up among every served model. Served by one
   provider, it resolves to that one. Served by two (the same id enabled for
   `openai` and for a custom backend, say), it is a 400 naming both
   candidates; send the full id instead.

A model id that contains a slash itself (`meta-llama/llama-3` on an
OpenRouter style backend) still works as a bare id, because the part before
the first slash is only taken as a provider when a provider of that name is
served. A model that is in the catalog but disabled is treated as unknown:
404 with `{"error": {"message", "type": "invalid_request_error", "param":
"model", "code": "model_not_found"}}`.

## Authentication

`/v1` sits outside `/api`, where OpenAI clients expect it, but it is closed
all the same (`common.auth.CLOSED_OUTSIDE_API_PREFIXES`): it spends the
operator's provider credit for whoever calls it. The middleware resolves the
principal exactly as for `/api`, by mode:

- `single`: nothing is asked; every call is the local operator. Do not
  expose a single-mode hub's port to a network you do not trust.
- `token`: the shared `AGENTS_HUB_API_TOKEN` as the bearer token, which is
  what the SDKs send as `api_key`.
- `multi`: a personal API key (`ahk_...`, see [api keys](api-keys.md)) or a
  session token. A key acts as its owner, and a key scoped to some
  workspaces can still call `/v1`, which names no workspace.

A missing or wrong credential is the middleware's 401 with `{"detail":
"Invalid or missing API token"}`, not the OpenAI error shape. The SDKs raise
their authentication error on the status code alone, so this reads the same
to a client; only the message text differs.

## Streaming and tools

With `stream: true` the answer is `text/event-stream`: `data:` lines of
`chat.completion.chunk` objects. The first carries `{"role": "assistant"}`,
then one chunk per content delta, `tool_calls` deltas (with `index`, and `id`
and `function.name` on the first delta of each call) when the model calls a
tool, a final chunk with `finish_reason`, and `data: [DONE]`. The final
chunk carries `usage` when the request set `stream_options.include_usage`.
A provider failure after the headers were sent arrives as one chunk with
`finish_reason: "error"` and an `error` object, then `[DONE]`.

`tools` in the OpenAI function format are bound to the model with the driver's
`bind_tools`, so they work with every provider that supports tool calling.
`tool_choice` `required` becomes the driver's `any`, a named function becomes
that name, `none` skips binding. The model's tool calls come back in the
OpenAI shape with `finish_reason: "tool_calls"`; the client runs the tool and
sends an `assistant` message with the `tool_calls` and a `tool` message with
the `tool_call_id`, exactly as with OpenAI. The hub never runs the tools.

`response_format` of type `json_object` or `json_schema` is best effort: it
becomes a system instruction asking for one JSON object (with the schema),
because not every provider has a JSON mode but every one reads a system
message. Validate the answer on the client.

## Agents as models

Every agent the caller may run is a model too: `agent:<agent_id>`.

```python
reply = client.chat.completions.create(
    model="agent:support-bot",
    messages=[{"role": "user", "content": "Where is order 42?"}],
    extra_headers={"X-Agents-Hub-Workspace": "shop"},
)
```

`GET /v1/models` lists them after the catalog's models, as `{"id":
"agent:<id>", "object": "model", "owned_by": "agents-hub", "created": 0,
"agent_id", "name", "description"}`. Which ones: the agents usable (by the
chat's own rule: system agents everywhere, an unshared agent only in its owner
workspace, a workspace's `allowed_agents` list) in the workspace the
`X-Agents-Hub-Workspace` header names, or, without the header, in any
workspace the caller can see. A personal key scoped to some workspaces sees
only theirs; a header naming a workspace outside the scope is a `403` with
`type: "permission_error"`.

A completion with an agent model runs the agent through the web chat's own
pipeline (`chat.pipelines.run_chat_pipeline`), so its tools, memory,
guardrails, budget and model settings apply exactly as in the chat, and the
turn is a run on the Messages page with `message_origin="api"`. Like a chat
turn it runs on a replica of a [service](services.md), the agent's own or
the workspace's runner, never in the backend process; the key's owner and
the key itself travel with the turn, so the run is stamped and charged as
before. The
workspace is, in order: the `X-Agents-Hub-Workspace` header, the key's one
workspace when it is scoped to exactly one, the agent's owner workspace,
`default`. Refused before anything runs: a workspace outside the key's scope
or one the caller is not an editor of in `multi` mode (`403`,
`workspace_not_allowed`), a workspace that does not exist (`404`,
`workspace_not_found`), an agent not available there (`404`,
`model_not_found`).

Messages map onto the chat turn: `system` and `developer` messages are the
caller's instructions and are put in front of the last user message (the
agent keeps its own system prompt); earlier `user` and `assistant` messages
are the conversation history; the last message must be the user's.
`response_format` becomes an instruction as for models. An agent runs its
own tools on the hub, so client `tools` (or `functions`), a `tool` message
and an assistant message with `tool_calls` are a `400`. `temperature`,
`max_tokens` and `stop` are ignored: the agent's own settings apply.

Two hub fields in the body shape that one run (an OpenAI SDK sends them
through `extra_body`): `agent_version` builds it from a stored version of the
agent instead of the live definition (404 `agent_version_not_found` for one
it does not have), and `overrides` is the per-run overrides object of
docs/agents.md "Per-run overrides" (400 `invalid_overrides`, 409
`capability_violation` for a tool set the guard refuses). Both are echoed in
`agents_hub`.

The answer carries an extra `agents_hub` object, `{"agent_id", "workspace",
"run_id"}`, so a client can link to the run. Streaming sends the agent's
tokens as content deltas, which include what it says between tool calls; a
handoff to another agent puts a blank line between the two replies; an
agent that streamed nothing sends its final answer as one delta. The final
chunk has `finish_reason: "stop"`, the `agents_hub` object and, with
`stream_options.include_usage`, the usage. A failed turn is a `502` (a
chunk with `finish_reason: "error"` when streaming). A client that
disconnects mid-stream stops the run, as the chat's stop button does. The
timeout is the chat's: the larger of `CHAT_REQUEST_TIMEOUT` and
`LLM_REQUEST_TIMEOUT` plus a minute.

Accounting is the same as for a model call: one `serving_usage` row, filed
under the provider and model the run actually used (so the per-model totals
and the tokens-per-day cap include agent calls), and one `model.serve` audit
row with `object_type: "agent"`, `object_id: "agent:<id>"`, the workspace,
and the run id in its details. The run's own cost is on the run, as for any
chat turn.

## Token accounting

Every completion writes one row to `serving_usage` (migration 0016): when,
who (user id, credential kind, name, and the key id when an API key was
used), provider and model, prompt, completion and total tokens, duration,
whether it streamed, `ok` or `error` with the error text. Counts come from
the provider's `usage_metadata` or `token_usage`. When a provider reports
none, they are estimated at four characters per token, and both the row and
the response's `usage` carry `"estimated": true`.

Tokens only, no cost: the catalog's prices drive the cost pages for runs,
and the served calls are counted separately so they never mix with them.
`GET /api/models/serving/usage` aggregates the rows per model for the Models
page. In `multi` mode an administrator sees everyone's calls and anyone else
only their own.

## Tokens per day

`AGENTS_HUB_RATE_LIMIT_TOKENS_PER_DAY` (default 0, off) caps the tokens one
caller may spend through `/v1/chat/completions` per UTC day: per personal key
when one is presented, per person otherwise. A key can carry its own cap
(see [api-keys](api-keys.md#rate-limits)). The day's total is read from
`serving_usage` (successful calls only) and cached for 30 seconds. A caller at
or over the cap gets, before any model is called:

```
HTTP/1.1 429 Too Many Requests
Retry-After: 3600

{"error": {"message": "Daily token limit reached; it resets at 00:00 UTC",
           "type": "rate_limit_error", "param": null,
           "code": "tokens_per_day_exceeded"}}
```

`Retry-After` is the number of seconds until the next 00:00 UTC. The
requests-per-minute limit (`AGENTS_HUB_RATE_LIMIT_PER_MINUTE`) applies to
`/v1` as well, answered by the guard with `{"detail": "Rate limit exceeded",
"retry_after": n}` rather than the OpenAI shape; OpenAI SDKs retry on the
`429` and `Retry-After` either way.

## Audit

Every completion, successful or not, also writes an audit row (see
[audit](audit.md)): action `model.serve`, object type `model`, object id
`<provider>/<model>`, result `ok` or `error`, and details `prompt_tokens`,
`completion_tokens`, `stream`, `duration_ms`, `tools` (how many were
offered) and the error text on a failure. It is written in every mode,
including `single`. Neither the usage row nor the audit row can fail a call:
both swallow their own errors.

## Routes

| Route | Answer |
|---|---|
| `GET /v1` | `{"object": "api", "endpoints": [...]}` |
| `GET /v1/models` | `{"object": "list", "data": [model, ..., agent, ...]}` |
| `GET /v1/models/{id}` | one model; the id may be full, bare, `default` or `agent:<id>` |
| `POST /v1/chat/completions` | a `chat.completion`, or a stream of chunks |
| any other `/v1/...` | 404, code `unknown_url` |
| `GET /api/models/serving/info` | `{"base_url": "<origin>/v1", "models": n, "agents": n, "auth": "api_key" \| "token" \| "none"}` |
| `GET /api/models/serving/usage?since=&until=&limit=` | `{"rows", "totals", "recent"}` |

`base_url` honours `X-Forwarded-Host` and `X-Forwarded-Proto`, so behind a
proxy it shows the address clients should use.

## Gotchas

- Only text goes through. An image, audio or file part is a 400, not
  silently dropped, because dropping it would answer a different question.
- `n` above 1, `logprobs` and `seed` are not supported; `n` is refused, the
  others are ignored.
- The response's `model` echoes what the client sent, not the resolved
  `<provider>/<model>`.
- A provider failure before any output is a 502 (504 on the timeout), a
  missing provider key or an unbuildable model is a 503; all three in the
  OpenAI error shape.
- The timeout covers the whole stream, including time the client takes to
  read it.
- Nothing is cached and nothing is retried: a client that retries pays
  twice, and both calls are counted.
