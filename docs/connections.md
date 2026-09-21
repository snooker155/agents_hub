# Connections

An agent that already runs in production, on its own triggers, reporting what it
does into this hub. The hub starts nothing and only watches.

This is the opposite direction from an [imported agent](imported-agents.md),
where the hub holds the endpoint and calls it. Both leave the external code
where it is; they differ in who decides when a run happens.

| | imported agent | connection |
| --- | --- | --- |
| Who starts a run | the hub | their own trigger: API, queue, cron, CI |
| Transport | the hub calls out | the agent posts in |
| Chat, tasks, flows | yes | no, it is a mirror |
| Runs, costs, logs, live view | yes | yes |

## Where it lives

**Connect → Connections** in the sidebar. The page lists what is attached, with
each connection's kind, when it last reported, and how many runs it has sent.
Opening one shows three tabs: the graph it reported, the runs it produced with
the path each took, and its setup, where the token is rotated and the snippet to
paste is generated.

The same page is reachable from where the need is felt: **Connect external** on
the Agents page, and **Mirror an external graph** on the Flows page. The latter
leads out of flows on purpose, because a graph that runs elsewhere is mirrored
rather than executed here.

## Creating one

Create the connection, keep the token it answers with. It is shown once and
stored only as a hash, so a lost token is rotated, never recovered.

Use **Connect** on the Connections page, or the API:

```bash
curl -X POST localhost:8000/api/connections \
     -H 'content-type: application/json' \
     -d '{"id": "billing-graph", "name": "Billing graph", "kind": "langgraph"}'
```

The token identifies that one connection to the hub, and the ingest endpoints
are all it reaches: it cannot read the dashboard, create tasks, run flows or
touch memory. Rotating it stops the previous one immediately; disabling the
connection stops it working while keeping the record and the history.

## Where a connection lives

In a workspace, like everything else here. A connection created without one
lives in `default`; it does not follow you into other workspaces. Its runs are
filed in the same workspace, and the client does not get a say: a report asking
for another workspace is still recorded in the connection's own.

Once a connection has reported runs its workspace is fixed. Moving it would move
that history with it, so make a new connection instead.

This is about where things are kept. The hub is a single-tenant install: there
are no user accounts and no authorisation model, and a workspace is a place, not
a permission.

The connection token fits the same picture. It tells the hub which process is
reporting, so runs are filed under the right connection and one reporter can be
stopped — rotated, disabled, deleted — without touching the others. It is stored
only as a hash and never shown again, because a value that can speak for a
connection should not be lying around; but it is an identity for a machine, not
a key issued to a person. The `answered_by` recorded with an answer is a label
in the same sense.

## Reporting a run

Three calls, with the same frames an imported agent streams:

```
POST /api/ingest/runs                  {"input": "...", "thread": "ticket-42"}
  -> {"run_id", "session_id"}
POST /api/ingest/runs/{run_id}/events  {"events": [{"type": "token", "token": "…"}, …]}
POST /api/ingest/runs/{run_id}/close   {"ok": true, "output": "…", "usage": {…}}
```

`GET /api/ingest/self` verifies the token and returns the limits, which is the
first call a client should make. `POST /api/ingest/topology` reports the shape of
the graph: nothing here can go and fetch it, because in this direction the hub
never calls out.

Events are batched deliberately. A graph that sent one request per token would
spend more time on this API than on its own work.

`thread` is the client's own grouping id. Runs that share one land in a single
session, the way a chat's turns do; runs without one are each their own session,
which is right for a scheduled job with no conversation.

## From LangGraph, in one line

A graph does not have to implement the calls above. The bundled clients do it
from inside the process, as a callback — Python first, JavaScript below:

```python
from agents_hub_langgraph import HubTracer          # see Installing, below

tracer = HubTracer(url="https://hub.internal", token="ahc_…")
tracer.report_graph(graph)                          # once, so the hub can draw it
graph.invoke(state, config={"callbacks": [tracer]})
```

```js
import { HubTracer } from "agents-hub-langgraph";   // see Installing, below

const tracer = new HubTracer({ url, token, graph });
await tracer.reportGraph();
await graph.invoke(state, { callbacks: [tracer], configurable: { thread_id } });
```

LangGraph passes callbacks into every node, so that sees model tokens, tool
calls, node boundaries and token usage without the graph knowing. They ship from
`clients/agents-hub-langgraph/` and `clients/agents-hub-langgraph-js/` in this
repository, and report the same frames, so the same graph looks the same
whichever language it is written in.

Both are built not to matter to the process they run in: they never raise into
the graph, reporting is off the graph's path, the queue is bounded and drops the
oldest events when the hub is unreachable. The Python one depends on
`langchain-core`, which a LangGraph user already has; the JavaScript one depends
on nothing at all. A hub that is down costs telemetry, not a run.

### Installing them

Neither client is published to PyPI or npm yet. Both install from this
repository, which is also how you get the version that matches your hub:

```bash
pip install -e clients/agents-hub-langgraph
npm install ./clients/agents-hub-langgraph-js
```

### When nothing arrives

Everything a client cannot deliver is swallowed, because a monitoring library
that throws takes down the run it was watching. That is right in production and
unhelpful while wiring it up, where a mistyped token looks exactly like success,
so there are three ways to find out:

- `GET /api/ingest/self` with the token, which is the first call a client should
  make and the fastest way to tell a wrong token from a wrong URL;
- the first failure is logged once per process, on the `agents_hub_langgraph`
  logger in Python and through `console.warn` in JavaScript, and says how to see
  the rest. Passing `on_error` / `onError` replaces it, on the grounds that you
  have then chosen the channel;
- `tracer.errors` counts every failure that was swallowed, which is the number
  to assert on in a test.

## From an exporter you already run

A team already sending traces to LangSmith, Langfuse, Phoenix or a collector has
the cheapest integration of all: no library, no code, a second destination on
the process they already run.

```bash
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://hub.internal/api/ingest/v1/traces
OTEL_EXPORTER_OTLP_TRACES_HEADERS=x-connection-token=ahc_…
```

The endpoint speaks OTLP over HTTP in either encoding, because the SDKs differ
and neither is negotiable from here: the Python exporter sends protobuf, the
JavaScript one sends JSON, and gzip is understood. Nothing is installed on this
side either — the payload is decoded directly, so a hub that never receives a
span carries no OpenTelemetry dependency for it.

Keep the exporter you have. This is meant to be the *second* one, and the point
of trying it that way is that nothing is at risk if the answer is no.

**What arrives is an import, not a live view.** Spans are exported when they
end, so the span that knows the input, the answer and the outcome — the root —
is the last to arrive. A trace is therefore held until its root lands and then
written in one go, back-dated to when the work actually ran. A run appears
seconds after it finishes rather than while it happens, and it cannot be watched
or interrupted. A trace whose root never arrives (the process died, a batch was
dropped) is recorded anyway after five minutes, from whatever did arrive, and
says so on the run.

**What is read from a span.** Nothing in OTLP knows what an agent is, so every
emitter invented its own attributes. The hub reads OpenInference
(`openinference.span.kind`, `input.value`, `llm.token_count.*`), OpenLLMetry's
`traceloop.*`, Langfuse's and LangSmith's, and the OpenTelemetry GenAI
conventions (`gen_ai.*`) that are meant to replace them. Tokens are credited
from whichever spelling is present, so cost works the same as for a reporting
client.

Which spans become steps of the graph is the one judgement call. A span is a
step when it names itself a node — a LangGraph span whose name matches the
`langgraph_node` in its metadata — and otherwise when the root called it
directly, which is the shape of nearly every agent trace. Spans deeper than that
still contribute their tools and their tokens; they do not each become a line in
the path. An emitter that names its nodes gets an exact picture; one that does
not gets a readable approximation.

**What is dropped.** A stacktrace: a run record keeps the exception's type and
message, on the grounds that whatever the team already sends its traces to is a
better place to read a traceback. Span links, span events other than exceptions,
and metrics are ignored. Re-exporting a trace the hub has already recorded is
ignored too — the reply says how many spans were refused — so a retry or a
collector fanning out twice cannot double a run or its cost.

## When a graph stops to ask

A graph that pauses for a human (LangGraph's `interrupt()`) parks its run here,
with the question, the choices and the node it stopped in. The connection's page
shows it above everything else and takes the answer.

What happens then is worth being precise about: the answer is **stored**, not
delivered. The graph is suspended in its own process, on its own checkpointer,
and nothing here can reach into it. It collects the answer the next time it asks
(`GET /api/ingest/runs/{run_id}/answer`), and continues on its own. With the
bundled client that is `tracer.wait_for_answer()`, which blocks until somebody
answers.

Collecting the answer finishes the parked run: asked, answered, delivered. The
work that follows is reported as a new run carrying `resumed_from`, so the two
read as one piece of work without pretending a run stayed open across a person's
lunch break. That is also how this hub's own agents behave when they call
`ask_user`.

A parked run does not count against the connection's open-run budget, and
retention never deletes it: a person may take days.

## What you get

The same records a local run leaves: a run row with its status, duration, input
and output, a session grouping, live events on the dashboard's stream, and cost
accounting from the `usage` frames. The reporting agent is the only party that
can know its token counts, since the model call happens in its process, so a
client that reports no usage leaves the cost columns at zero.

## Limits

Per connection, enforced per API worker:

- 500 events per request, refused rather than truncated: a silently dropped tail
  would make a run's trail quietly wrong.
- 500 open runs at once.
- 1200 requests a minute.
- For OTLP: 4 MB per request, 2000 spans per request, 1000 spans per trace, 200
  traces buffered at once. A trace over the span cap keeps its root regardless,
  because that is what says how the run ended; the count of what was dropped
  comes back in the export's own `partialSuccess` reply and is stored on the run.

**Retention** is a count, not an age. Each connection keeps its newest 2000 runs
by default and the rest are deleted, with their payloads and the sessions they
left behind. Set a different number per connection on its Setup tab, or 0 to
keep everything, which is fine for a quiet connection and not for a busy one.
The hub-wide default is `CONNECTION_RETENTION_RUNS`.

An age limit is the wrong instrument here: a graph reporting a few hundred runs
an hour writes a quarter of a million rows before a thirty-day limit notices.

Trimming happens in the daily maintenance pass, and on demand from the
connection's page. It is never done inside an ingest request: pruning is a burst
of deletes, and a reporting graph should not wait for this hub's housekeeping. A
run that is still open is never pruned, however far past the cap it sits.

The accumulated text, tool trail and token totals for an open run live in the
API process. A restart mid-run loses the accumulation, not the run: the events
were already published, the row already exists, and the `close` call carries the
authoritative output and usage.

## Gotchas

- **A connection is not an agent.** It has no prompt, no tools and no model
  here, so it cannot be chatted with, put in a flow or given a task. What it
  reports is history, not a handle.
- **Deleting a connection keeps its runs.** They are the record of work that
  really happened; removing them with the credential would erase it.
- **An OTLP run is written when its trace completes**, so it never appears as
  `running` and never shows a live stream. That is the shape of the protocol,
  not a limitation of the hub: use a tracer for a run you want to watch.
- **A run reported by a client that then crashes stays `running`** until
  something closes it, the same as any orphaned run. Retention will not remove
  it either, because it cannot tell an abandoned run from a slow one.

Related: [imported-agents](imported-agents.md), [connectors](connectors.md), [sessions-and-runs](sessions-and-runs.md), [costs](costs.md), [agents](agents.md).
