# TypeScript SDK

A typed client for talking to a hub from outside it: Node or a browser
calling the dashboard's own `/api` routes (agents, tasks, chat) and the
OpenAI-compatible `/v1` surface, in TypeScript or plain JavaScript. The
package is `clients/agents-hub-ts/` (`@agents-hub/sdk`), not on npm yet, so
it installs the same way the LangGraph.js reporter does: straight from this
repository.

```bash
npm install ./clients/agents-hub-ts   # from this hub's repository
```

```ts
import { AgentsHub } from "@agents-hub/sdk";

const hub = new AgentsHub({ baseUrl: "http://localhost:8000", apiKey: "ahk_..." });

const agents = await hub.agents.list({ workspace: "shop" });
const { response } = await hub.chat.send({ agent_id: "support-bot", message: "Where is order 42?" });

for await (const event of hub.chat.send({ agent_id: "support-bot", message: "..." }, { stream: true })) {
  if (event.type === "token") process.stdout.write(String(event.token));
}
```

`apiKey` is whatever [a personal API key](api-keys.md) would be: `ahk_...`
in `AUTH_MODE=multi`, the shared `AGENTS_HUB_API_TOKEN` in `token` mode, or
left out entirely in `single` mode. It is sent as
`Authorization: Bearer <key>`, the header `common.auth.extract_bearer` reads.

## What is generated, what is hand-written

Two files are generated straight from the hub's own OpenAPI schema
(`dashboard.backend.main.app.openapi()`) by `scripts/gen_ts_sdk.py`, a plain
JSON Schema to TypeScript converter with no npm dependency of its own, so it
runs with nothing but Python:

- `clients/agents-hub-ts/openapi.schema.json`: the schema itself, committed
  so the package builds without a hub running.
- `clients/agents-hub-ts/src/generated/types.ts`: a TypeScript type for every
  schema the hub defines, plus `ApiPaths`, a map from an operation's path and
  method to its request body and success response types.

```bash
python scripts/gen_ts_sdk.py          # refresh both files after a route changes
python scripts/gen_ts_sdk.py --check  # fail if they are stale (CI)
```

Everything else is hand-written on top: `AgentsHub`, the `agents`, `tasks`,
`chat` and `v1` resources, the SSE readers, and the error mapping. `request()`
on `AgentsHub` reaches any route in `ApiPaths` even without a dedicated
method, typed the same way:

```ts
const options = await hub.request("get", "/api/widgets/options");
const agent = await hub.request("get", "/api/agents/{agent_id}", { params: { agent_id: "support-bot" } });
```

## `agents` and `tasks`

Thin wrappers over `dashboard/backend/routes/agents.py` and `tasks.py`:
`agents.list({ workspace, limit, offset })`, `agents.get(id)`,
`tasks.list(...)`, `tasks.create(body)`, `tasks.get(id)`,
`tasks.update(id, patch)`, `tasks.delete(id)`. `list()` without `limit`/`offset` is the full
list; with either, a page: `{items, total, limit, offset}`.

## `chat`

`chat.send(request, { stream })` is `POST /api/chat/message` (one awaited
answer) or, with `stream: true`, `POST /api/chat/stream` ([chat](chat.md)):
an async generator of the pipeline's own events (`meta`, `token`,
`tool_start`/`tool_end`, `handoff`, `done` for one agent; `flow_meta`,
`node_start`, `node_done`, `flow_finish` for a flow; `team_meta`,
`team_message` for a team). `request` needs exactly one of `agent_id`,
`flow_id` or `team_id`.

```ts
for await (const event of hub.chat.send({ agent_id: "support-bot", message: "Hi" }, { stream: true })) {
  if (event.type === "done") console.log(event.response);
}
```

## `v1`

[The hub as a provider](hub-as-provider.md)'s own surface, for a caller that
would rather not pull in an OpenAI SDK just to talk to this one hub:

```ts
const completion = await hub.v1.chat.completions.create({
  model: "anthropic/claude-sonnet-4-5",
  messages: [{ role: "user", content: "Hello" }],
});

// An agent, as a model: model: "agent:<id>", the turn runs through the same
// chat pipeline, and the answer carries agents_hub: {agent_id, workspace, run_id}.
for await (const chunk of hub.v1.chat.completions.create(
  { model: "agent:support-bot", messages: [{ role: "user", content: "Where is order 42?" }], stream: true },
  { workspace: "shop" },
)) {
  process.stdout.write(chunk.choices[0]?.delta.content ?? "");
}

const { data } = await hub.v1.models.list();
```

`{ workspace }` on the call's options sends `X-Agents-Hub-Workspace`, which
is how the hub picks which workspace's agents answer for an `agent:<id>`
model. The request body has no named schema in the OpenAPI document (the
route reads raw JSON, not a Pydantic model), so `ChatCompletionRequest` and
its response types are hand-written from the route and from
[the doc](hub-as-provider.md) rather than generated.

A real OpenAI SDK also works against the same `/v1`, unmodified (see the
doc); this package exists for the caller that does not want that
dependency.

## Errors

Every non-ok response becomes one `AgentsHubError`, whichever shape the hub
answered in: `/api`'s `{"detail": "...", "code"?}` or `/v1`'s OpenAI shape
`{"error": {"message", "type", "code"}}`. `error.status`, `error.code` and
`error.type` (the `/v1` one only) are pulled out; `error.body` keeps the
parsed JSON for anything else.

## Streaming, underneath

Both streaming calls answer `text/event-stream`: `data: <json>\n\n` lines,
read by `src/sse.ts`'s `readSSELines`/`readSSEJson`, which reassemble an
event split across network chunks and stop cleanly at `/v1`'s `data: [DONE]`
sentinel. `/api/chat/stream` has none: the stream just ends when the run
does. Neither streaming path sends `id:` lines; that is `/api/stream`'s own
job (the dashboard's multiplexed browser connection), which this SDK has no
reason to use.

## The widget

`examples/widget/` has a plain HTML page embedding [the chat
widget](widget.md) (the `<script data-widget data-key>` snippet, no SDK
involved: the widget talks to its own public API directly) and a small Node
script that calls an agent through this SDK instead, for the two different
shapes an integration takes.

## Testing

`node --test` against the TypeScript sources directly (Node 22.18+ strips
types natively, no build step), with a hand-rolled `fetch` double
(`test/testing.ts`) recording what was sent and answering from a queue of
canned responses: no network, no running hub. `npm run typecheck` runs
`tsc --noEmit` over the package in strict mode.

## Gotchas

- `src/generated/types.ts` is large (one entry per route the hub serves,
  over 700 of them) because it is a straight mechanical translation, not
  hand-curated; most callers only ever touch the handful of named types the
  hand-written resources already use.
- Running the package's own `.ts` files directly (no build step) needs
  Node 22.18 or newer, or a bundler; this is a dev convenience, not a promise
  about what a consumer's runtime must be once the package starts shipping
  compiled output.
- `ApiPaths` has no query parameter types yet, only path, body and response;
  pass query params as a plain object to `request()`'s `query` option.
- Regenerating after a route changes is mechanical but not automatic: nothing
  runs `scripts/gen_ts_sdk.py` for you, other than `--check` catching a stale
  commit.

## Related

- [api-keys](api-keys.md): what `apiKey` is and how it is scoped
- [hub-as-provider](hub-as-provider.md): the `/v1` surface this wraps
- [widget](widget.md): the chat widget the example page embeds
- [chat](chat.md): the pipeline behind `chat.send`
- [cli](cli.md): the other door into the same hub, from a terminal
