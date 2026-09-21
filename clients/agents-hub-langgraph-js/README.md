# agents-hub-langgraph (JavaScript)

Your LangGraph.js flow runs where it already runs, on your own triggers. This
reports what it does to an Agents Hub, so there is a UI and a run history.

```bash
npm install ./clients/agents-hub-langgraph-js   # from this hub's repository
```

Not on npm yet, so it installs from the repository your hub came from, which is
also how you get the version that matches it.

```js
import { HubTracer } from "agents-hub-langgraph";

const tracer = new HubTracer({ url: "https://hub.internal", token: "ahc_…", graph });
await tracer.reportGraph();                    // once, so the hub can draw it

await graph.invoke(state, {
  callbacks: [tracer],
  configurable: { thread_id: threadId },
});
```

That is the integration. No adapter process, no endpoint to expose, no diff in
the graph. `AGENTS_HUB_URL` and `AGENTS_HUB_TOKEN` are read from the environment
when the options are left out.

Get the token by creating a connection in the hub:

```bash
curl -X POST $AGENTS_HUB_URL/api/connections \
     -H 'content-type: application/json' \
     -d '{"id": "billing-graph", "name": "Billing graph", "kind": "langgraph"}'
```

It is shown once. It tells the hub which process is reporting; the ingest
endpoints are all it reaches.

## What the hub gets

| From | Shows up as |
| --- | --- |
| the root chain starting and finishing | a run, with its input, output and duration |
| each graph node | the path the run took, live on the graph picture |
| `handleLLMNewToken` | the answer appearing as it is written |
| tool calls | the tool trail on the run |
| `usage_metadata` | tokens and cost |
| `reportGraph()` | the graph drawn, branches and all |

`__start__` and `__end__` are skipped: they arrive as nodes here but are not
work, and leaving them out keeps the path identical to the Python client's for
the same graph.

## Graphs that stop to ask

`interrupt()` suspends your graph until someone answers. The hub cannot push
that answer to you: in this direction nothing there ever calls your process. So
your run is parked with the question on it, a person answers on the connection's
page, and you collect it:

```js
import { Command } from "@langchain/langgraph";

let result = await graph.invoke(state, cfg);
while (result.__interrupt__) {
  const answer = await tracer.waitForAnswer();   // resolves when somebody answers
  result = await graph.invoke(new Command({ resume: answer }), cfg);
}
```

Only your code can resume your graph, so that loop stays yours. Passing `graph`
to the tracer is what lets it read *what* the graph is waiting for: nothing in
any callback carries the question, so the graph itself is asked. Without it a
pause is still reported, as "waiting in `<node>`".

`waitForAnswer()` resolves to `null` if nobody answers within its timeout (six
hours by default). The resumed run is reported as its own run, linked to the one
that waited.

## What it will not do to you

This runs inside your process, so it is built to be boring:

- **It never throws into your graph.** Every callback swallows its own errors.
  Pass `onError` to see them, read `tracer.errors` for how many there were, or
  rely on the first one reaching `console.warn` once per process. After that it
  is silent, because a library inside somebody's production process does not get
  to narrate.
- **It never makes your graph wait.** Reporting is queued and flushed on a
  timer, and nothing on your path awaits it.
- **It never grows without bound.** The buffer is capped and drops the oldest
  events when the hub is unreachable.
- **It adds no dependencies.** Not even LangChain: a callback handler may be a
  plain object, and `fetch` is in the runtime (Node 18+).

A hub that is down costs you telemetry, not a run.

## Options

| Option | Default | What it is for |
| --- | --- | --- |
| `url`, `token` | `AGENTS_HUB_URL`, `AGENTS_HUB_TOKEN` | where to report, and as what |
| `graph` | none | lets a pause carry its question, and `reportGraph()` take no argument |
| `thread` | none | your own grouping id; runs sharing one land in a single session |
| `title`, `model`, `provider` | none | what the run is called and what it ran on |
| `metadata` | `{}` | anything else you want stored with the run |
| `onError` | none | called with any error this package swallows; replaces the one-off warning |
| `flushMs` | `500` | milliseconds between batches |

`await tracer.flush()` waits until everything queued has reached the hub. Worth
calling before a short-lived script exits.

## Testing your own integration

`src/testing.js` ships two doubles so you do not have to write them:
`RecordingTransport` (records what would have been sent) and `BrokenTransport`
(fails the way a hub that is down fails).

```js
import { RecordingTransport } from "agents-hub-langgraph/src/testing.js";
```

## Not using LangGraph?

The endpoints underneath are plain HTTP and language-neutral: open a run, post
frames, close it. See `docs/connections.md` in the hub.
