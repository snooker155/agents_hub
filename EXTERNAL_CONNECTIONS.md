# External connections

How code that already exists somewhere else becomes visible in this product,
and how someone finds that path without being told it exists.

Status: shipped. Both halves and the UI for them, including the Connect group
with connections and connectors side by side, two tracer libraries, and an
OpenTelemetry receiver for teams who would rather write nothing at all. What
remains is in Open questions at the end; the one proposal that was dropped
rather than built is recorded below so it is not rediscovered as an oversight.

## The case

A company has a working LangGraph flow: their repository, their prompts, their
models, their deployment, their triggers. They want a UI and monitoring. They do
not want their agents rewritten as hub definitions, and they will not accept an
integration that means touching the graph.

That is not a niche. It is most teams who already build agents, and it is the
largest addressable use of this product that does not require them to adopt it
first. The service should be usable as *only* that: a window onto external
agents, with none of its own agents defined.

## Two modes

|  | **control** | **observe** |
| --- | --- | --- |
| Who starts a run | the hub | their own trigger (API, queue, cron, CI) |
| Transport | hub POSTs to the agent | the agent POSTs to the hub |
| Integration on their side | an HTTP adapter, one file | a callback handler, one line |
| Status | **ships** | **ships; the UI for it is below** |
| Gets: chat, tasks, flows | yes | no, it is a mirror |
| Gets: runs, costs, logs, live view | yes | yes |
| Good for | an agent they want the hub to drive | a production flow they want to watch |

Control is the current `RemoteAgent` path: `agents/remote_agent.py`,
`agents/importer/`, `docs/imported-agents.md`, with a worked LangGraph example
in `examples/imported-agents/langgraph-agenthub/` and its JavaScript twin in
`examples/imported-agents/langgraph-agenthub-js/`.

Observe is the mode that answers "we already run this in production". It is the
easier sell and the harder build, because the hub currently has no way to learn
about a run it did not start.

## Part 1: the frame contract, extended

Today's vocabulary (`RemoteAgent._handle_stream_event`): `token`, `thinking`,
`tool_start`, `tool_end`, `tool_error`, `usage`, `done`, `error`.

A graph is a shape, and the frames describe a line. Two additions:

```json
{"type": "node_start", "node": "triage", "depth": 0, "input": "…"}
{"type": "node_end",   "node": "triage", "ok": true, "output": "…", "next": "pricing"}
```

`depth` distinguishes a subgraph's nodes from the top level, so a graph with
subgraphs does not render as a flat list that matches no picture of itself.
`next` is what makes a conditional edge legible: the branch the run actually
took, which is the single most asked question when watching a graph.

**Shipped.** `RemoteAgent` translates these to `graph_node_start` /
`graph_node_end` rather than forwarding them under their own names: `node_start`
already means "a node of a hub *flow* started" on the chat stream, and the chat
opens a bubble per flow node. Forwarding them unchanged would split one imported
agent's answer into flow bubbles in a conversation running no flow. They render
as steps inside the agent's own reply.

**Topology.** A manifest may declare `runtime.graph_path`, and the hub fetches

```json
{"ok": true, "framework": "langgraph",
 "nodes": [{"id": "triage", "label": "triage", "kind": "node"}],
 "edges": [{"source": "triage", "target": "pricing", "conditional": true}]}
```

once at import and again on recheck. LangGraph hands this over itself
(`get_graph().to_json()`), so the hub never parses anyone's Python to draw their
graph. Normalisation belongs in the adapter, not the hub: the hub should not
learn one framework's JSON shape.

**Rendering.** Reuse the flow canvas read-only, not the flow *engine*. A mirrored
graph is a picture with live state, not something `flow/engine.py` can execute:
that engine runs nodes that reference hub agents. Mixing the two would produce a
Run button that cannot work. The mirror gets its own node type and no Run.

**Shipped.** `runtime.graph_path` is parsed into the descriptor, fetched at
registration and at every re-check, bounded and validated by
`remote_agent.normalize_topology` (dangling edges dropped, labels clipped, size
capped), and drawn by `components/GraphMirror.jsx` on the agent page.
`POST /api/agent-import/{id}/topology` re-fetches it on its own, without the
side effects of a full re-check.

## Part 2: observe mode, the ingest API

### The record

A **connection** is the new first-class object: something outside that reports
in. It is not an agent definition (it has no prompt, no tools, no model here)
and not a connector (it does not reach out to a third-party service).

```json
{
  "id": "billing-graph",
  "name": "Billing graph",
  "kind": "langgraph",
  "mode": "observe",
  "token_hash": "…",
  "workspace": "default",
  "topology": {"nodes": [], "edges": []},
  "last_seen": "2026-09-20T12:00:00Z",
  "stats": {"runs_24h": 412, "failures_24h": 3, "cost_24h": 11.40}
}
```

Stored beside the other file-backed registries, in `.agents_hub/connections.json`,
with the token hashed. It appears in the agent list as an agent of kind
`external`, so chat, sessions and cost pages need no special case, and it is
marked not-runnable in control terms.

### The endpoints

**Shipped**, in `dashboard/backend/routes/ingest.py`:

```
POST /api/ingest/runs                 open a run       -> {run_id, session_id}
POST /api/ingest/runs/{run_id}/events one or many frames, the same vocabulary
POST /api/ingest/runs/{run_id}/close  final outcome
POST /api/ingest/topology             the shape of the graph behind it
GET  /api/ingest/self                 who am I, what does this hub accept
```

Management is a separate router, `/api/connections`, authenticated as the
operator: creating a connection *issues a credential*, and one prefix with two
authentication models would hand every reporting service a key to the dashboard.
User-facing documentation is `docs/connections.md`.

Batched events matter: a chatty graph would otherwise make one HTTP call per
token. The body takes `{"events": [...]}`, and the tracer buffers with a small
flush interval.

### Why this is cheap to build

Nearly all of it exists, aimed at subprocesses instead of at foreign services:

- `managers/run_manager.py` `open_run` / `close_run_from_result` already create
  and finish run records, with `**extra` for provider, model and workspace.
- `common/session_service.py` `get_or_create_chat_session` and
  `add_run_to_session` already group runs.
- `common/live_runs.py` `record_run_event` already folds relayed events into a
  live turn keyed by `run_id`, which is exactly the shape an external run has.
- `dashboard/backend/routes/sessions.py` `POST /{session_id}/events` already
  fans relayed events onto the broker, the same path the dashboard renders.
- Token and cost crediting already accepts a `usage` frame, including cache
  reads (`agents/callbacks/run_statistics.cached_input_tokens`).

So the new route is mostly authentication, validation, and reuse. The frame
translation is the one piece that should be *extracted* rather than duplicated:
`RemoteAgent._handle_stream_event` becomes a shared
`common/agent_frames.py:translate()` used by both the pull adapter and the push
ingest, so the two modes can never drift into rendering differently.

### Authentication

Per-connection tokens, not the global API token. `common/auth.py:is_authorized`
currently gates everything under `/api` on one shared token, which is wrong for
this: a connection's token must be revocable on its own and must not open the
dashboard. `/api/ingest` gets the treatment `/api/external/{token}/run` was
meant to have: exempt from the global guard, authorised by its own token,
scoped to one connection, revocable from its page.

Rate limiting and a payload cap belong here too. An ingest endpoint is the first
surface in this product a stranger can POST to at volume.

### What a connection may not do

Ingest writes run records, events and usage. It does not create tasks, trigger
flows, write memory or call tools. A reporting channel that can act is a
remote-execution API by another name.

## Part 3: the tracer

The integration a customer will actually accept is one line, no adapter, no
process:

```python
from agents_hub import HubTracer

graph.invoke(state, config={"callbacks": [
    HubTracer(url="https://hub.internal", token=..., connection="billing-graph")
]})
```

LangGraph propagates callbacks into every node, so this sees model tokens, tool
calls, node boundaries and usage without the graph knowing. Shipped as its own
package, `agents-hub-langgraph`, because a customer will not pip-install this
product into their production image.

**Shipped**, as `clients/agents-hub-langgraph/`, its own distribution with its
own `pyproject.toml`. Two things in it were not obvious from the design:

* **Node identity has to come from `run_id`, not names.** LangChain names a
  chain *start* and gives the matching *end* nothing but the same id. Matching
  ends by name closes the wrong node the moment a graph loops or nests a
  subgraph. A node is the chain whose name equals its `langgraph_node`; every
  chain inside it carries the same metadata and is not a node.
* **The recommended usage is one tracer per invocation**, so a server creates
  them by the thousand. A worker thread and an `atexit` handler per tracer would
  be a leak that only appears in production. The thread is started lazily and
  ends after 30 idle seconds; the exit flush is one handler over a weak set.

The bounded queue was in the design; the batch buffer behind it was not, and a
run whose `open` failed used to hold its frames forever. Both are capped now.

Two follow-ons, once the endpoint exists and is stable:

- ~~**A JS/TS tracer.**~~ Shipped as `clients/agents-hub-langgraph-js/`, with no
  dependencies at all: a LangChain callback handler may be a plain object and
  `fetch` is in the runtime.
- ~~**An OpenTelemetry receiver.**~~ Shipped: `POST /api/ingest/v1/traces`,
  `connections/otlp.py` (the wire) and `connections/otel.py` (the meaning). A
  team already exporting traces points a second exporter here and writes no
  code. It was right to build it second: it is translated *into* the frame
  vocabulary the tracer defined, so an imported run and a reported one are the
  same record and the same page.

  Neither half took the obvious shape.

  **The wire is hand-decoded.** `opentelemetry-proto` drags in `protobuf`, a
  version-pinned native wheel, onto every install of this hub so that some
  installs can accept OTLP. The decoder that replaces it is smaller than that
  dependency's import line, reads only the fields the product uses, and skips
  the rest by length, which is also what makes it forward compatible. Both
  encodings are accepted because the SDKs differ and neither is negotiable from
  here: the Python exporter speaks protobuf only, the JavaScript one defaults to
  JSON, and a receiver that took one of them would turn "point your exporter at
  this" into "and change your exporter's protocol".

  **The meaning is buffered, not streamed.** Spans are exported when they *end*,
  so the root — the only span that knows the input, the answer and the outcome —
  arrives last, often in a later request than its children. A trace is held
  until its root lands and then written in one go, sorted back into start order;
  recording spans as they arrived would produce runs whose steps run backwards.
  A trace whose root never comes is recorded anyway after a timeout, because
  those are the runs most worth seeing.

  The judgement call is which spans are steps of the graph. A span qualifies by
  naming itself one — the span whose name equals the `langgraph_node` in its
  metadata — and otherwise by being a direct child of the root. Presence of the
  metadata alone is not enough: every span *inside* a node inherits it, so that
  rule would promote each of a node's internals into a step. It is the same rule
  the Python tracer arrived at from the other side, where a node is the chain
  whose name equals its `langgraph_node`.

## Part 4: where this lives in the UI

### The decision

A new top-level nav group, **Connect**, holding two items, plus entry points
from the pages where the need is felt. Not one or the other: a dedicated home so
the capability is discoverable when someone is looking for it, and inline
buttons so it is discoverable when someone is not.

```
Main            Chat, Dashboard
Workspace       …
Connect         Connections      ← new: external agents, graphs, services
                Connectors       ← moved: Telegram, GitHub/GitLab, mail, Blender
Infrastructure  Agents, Instances, Marketplace, …
```

Why they belong in one group despite being opposite directions: to a user both
answer "how do I attach something that is not in here". The group name says
that, the two items separate the direction, and each page states it in a line:

- **Connections** — something of yours runs elsewhere and reports here.
- **Connectors** — this service reaches out to a system you already use.

This also fixes an existing problem: Telegram configuration lives inside
Settings today, which is where nobody looks for it.

### Connections page

A list of what is attached, each row: name, kind badge (LangGraph, CrewAI, HTTP
agent, OpenAI Assistant), mode badge (control or observe), health dot, last
seen, runs and cost over 24h. Empty state is the important screen, because it is
the one a new evaluator sees, and it should read as three choices, not as a
form:

```
┌──────────────────────────────────────────────────────────────┐
│  Nothing connected yet.                                      │
│                                                              │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐          │
│  │ Import an    │ │ Watch a run- │ │ Connect a    │          │
│  │ agent repo   │ │ ning service │ │ LangGraph    │          │
│  │              │ │              │ │ graph        │          │
│  │ hub clones   │ │ hub calls a  │ │ one line in  │          │
│  │ and runs it  │ │ URL you give │ │ your code    │          │
│  └──────────────┘ └──────────────┘ └──────────────┘          │
│                                                              │
│  Not sure? Every option leaves your code where it is.        │
└──────────────────────────────────────────────────────────────┘
```

The third tile is the observe path and ends on a screen with a generated token
and a copyable snippet, in the language they picked. Copy, paste, run, the page
says "first run received" and moves on by itself. That moment, the page changing
state because their own code called it, is the whole demo.

### Connection detail

Tabs: **Overview** (topology mirror with the live node lit up, or a plain run
list when no topology was reported), **Runs**, **Costs**, **Setup** (the token,
the snippet, rotate and revoke).

The existing `ImportedAgentPanel` and its readiness report move here as the
Setup tab for control-mode connections. That is where a to-do list of missing
pieces belongs.

### Entry points elsewhere

- **Agents page**: the existing Import button gains a sibling, "Connect
  external", both leading into the same wizard. `pages/AgentManager.jsx` already
  hosts `ImportAgentModal`.
- **Flows page**: "Mirror an external graph", which is the phrase a LangGraph
  user searches for. It creates an observe connection and lands on its canvas.
- **Dashboard**: when a workspace has connections, a card with their volume, so
  an install used mainly to watch external agents has them on its front page.
- **Chat**: external agents already appear in the picker; control-mode ones stay
  selectable, monitored-only ones are listed as "monitored, not callable" rather
  than hidden, because hiding them makes people think the connection failed.

### One nav for everyone (decided, not built)

An earlier draft of this document proposed a "monitoring-only" profile: a setup
flag that reordered the sidebar and folded the definition-side pages away for an
install that only watches external agents.

**Dropped.** Nothing in the UI is hidden based on how a hub is used. The concern
behind it was real — an install with no agents of its own shows pages that are
empty by design — but every remedy is worse than the symptom. A deployment flag
makes two products out of one and a support question out of every screenshot; a
per-browser preference gives each colleague a different sidebar; deriving it
from the data rearranges someone's menu the first time they add a connection.
An empty page that is always in the same place is easier to live with than a
menu that moves.

### Naming

"Connections" over "Integrations" (which reads as third-party SaaS, which is the
other item) and over "External agents" (too narrow: a graph, a crew and a single
HTTP endpoint are all valid). The wizard never says "import" for observe mode:
nothing is imported, and the word is what makes people think their code will be
copied.

## Phasing

1. **The example.** Done: `examples/imported-agents/langgraph-agenthub/`, a
   working LangGraph adapter with node frames and topology, no changes to the
   graph it serves. Done for LangGraph.js too, as
   `examples/imported-agents/langgraph-agenthub-js/`: the same contract, the same
   frames, and four differences in the event stream that only a real run shows.
   `parent_ids` does not exist there, so the root chain is found by run id; a
   node that throws delivers no error event at all, so the open node is closed
   from the catch block; `__interrupt__` arrives *on* the stream, which is
   strictly better than Python, where the question exists only in the return
   value; and a node's label sits under `data.name` in the topology JSON. The
   control-mode pair now matches the observe-mode pair, where both tracers
   already ship.
2. **Node frames and the mirrored canvas.** Done: `graph_node_start` /
   `graph_node_end` in the chat timeline, `runtime.graph_path` end to end, and a
   read-only `GraphMirror` on the agent page, and the same mirror in the chat's
   process panel, lit live from the node frames (`components/graphRun.js` folds
   them into the open-node stack the highlight needs). Control mode now looks
   like what it is.
3. **Ingest API.** Done: `/api/ingest/*` and `/api/connections/*`, the
   connection record with a hashed, rotatable token (`connections/store.py`),
   the run lifecycle onto the existing run and session machinery
   (`connections/service.py`), and the frame translation extracted out of
   `RemoteAgent` into `common/agent_frames.py` so both directions render
   identically. Limits and a per-connection rate limit are enforced; the
   accumulator for an open run is in-process by design, and the `close` call
   carries the authoritative output and usage.
4. **The tracer packages.** Done for Python (`clients/agents-hub-langgraph/`)
   and JavaScript (`clients/agents-hub-langgraph-js/`). Both are tested against
   real graphs rather than against a memory of the callback API, which is the
   only way either would have been right. LangGraph.js reports a suspended node
   through the *error* callback as Python does, but it also reports `__start__`
   as a node; and LangChain.js turns a handler into one by copying its **own**
   properties, so a class instance passed as `callbacks: [tracer]` is accepted
   and then never called. Neither would have been caught by a synthetic test.

   The OpenTelemetry receiver followed, as step 4b rather than a fourth client:
   it reuses `connections/service.py` unchanged, so an imported trace produces
   the same run record, session, live events and retention as a reported run.
   Its tests run against a payload a real exporter really sent
   (`tests/fixtures/otlp_langgraph.{bin,json}`), for the same reason the tracers
   were tested against real graphs.

   Three things only a live export could have shown, two of them faults in code
   that had already shipped. The documented `tool_start` field is `input`, but
   the trail recorded `args`, an undocumented one, so every reported tool call
   was stored with an empty input — and a tool that *failed* was left out of the
   trail entirely. A reported run lost its input at close, because the payload
   written then replaces the one seeded at open, heavy key for heavy key. And
   the instrumentation in the field puts a whole stacktrace in a span's status
   message, run together with the exception's repr and no separator, so the run
   record now takes the type and message from the structured exception event
   beside it and leaves the stacktrace where it came from.
5. **The UI.** Mostly done: the Connect nav group, the Connections list with
   the three-choice empty state, the connection page (reported graph, runs with
   the path each took, setup with a rotatable token and a generated snippet),
   the entry points on Agents and Flows, and a dashboard card that appears only
   when something is attached.

   The connectors moved too: Telegram, GitHub/GitLab and Blender are now
   `components/connectors/` rendered by a Connectors page in the same group,
   with `/settings/telegram` and its siblings redirecting there, and the i18n
   coverage that guarded them moved with them.

Each step is useful shipped alone, and 1 to 3 are what a customer with an
existing graph needs before they will look at the rest.

## Open questions

- ~~**Retention.**~~ Done, as a per-connection *count* rather than a window:
  `connections/retention.py`, enforced by the daily maintenance pass and on
  demand from the connection's page, with the limit visible and editable where
  the connection is. An age limit was the wrong instrument — thirty days of a
  few hundred runs an hour is a quarter of a million rows before it notices.
  Sampling is still not implemented and is the next lever if the cap alone is
  not enough: it would keep the counts while dropping most payloads.
- ~~**Interrupts.**~~ Done for observe mode: an `interrupt` frame, a run state
  of `awaiting_input` carrying the question, an answer endpoint on the operator
  side and a poll on the client side, plus `wait_for_answer()` in the tracer.
  Three things it taught us. LangGraph suspends by *raising* `GraphInterrupt`
  through the node, so the callback that reports a node dying and the one that
  reports a node waiting are the same callback. `__interrupt__` is added to what
  `invoke` returns, after the root chain has already ended, so no callback ever
  carries the question — the graph itself has to be asked, which is why the
  tracer takes `graph=`. And a parked run is not held open: the answer is
  collected, the run finishes, and the work that follows is a new run linked by
  `resumed_from`.

  **Control mode is done too**, and cost less than expected because the state
  already existed: an `awaiting_input` result with a `pending_question` is what
  this hub's own agents return from `ask_user`, so a remote agent returning the
  same shape parks the same way and appears on the same task page with no UI
  work at all. What was new is `runtime.resume_path`, `RemoteAgent.resume`, a
  `resume` argument threaded through `invoke_agent`, and one branch in the task
  answer route: an agent that declares the endpoint is continued, everything
  else is re-run with the answer in its prompt as before.
- ~~**Multi-tenancy.**~~ Closed as **not a goal**. The hub is a single-tenant
  install by design: no accounts, no authorisation model, one operator. So the
  question "which tenant does this token belong to" has no subject, and building
  one would have meant authenticating people across every route in the product.

  What the work turned into instead is filing. A connection lives in a
  workspace like everything else; created without one it lives in `default`
  rather than following the reader everywhere; its runs are filed there and the
  client cannot steer that; the binding is fixed once it has reported, because
  moving it would move the history; and every single-connection endpoint decides
  this in one place, answering 404 from elsewhere the way the rest of the
  product does.

  The connection token is an identity for a machine: it says which process is
  reporting, so runs land under the right connection and one reporter can be
  stopped without touching the others. It is kept as a hash because a value that
  speaks for a connection should not sit in plain text, not because it is
  guarding anything from a person.

  A per-run `tenant` label — one connection reporting for many end customers —
  remains unbuilt and unrelated: that is grouping and cost attribution.
