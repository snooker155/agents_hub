# A LangGraph graph in Agents Hub, without moving it

You have a LangGraph flow that works. It lives in your repository, with your
prompts, your models, your tools and your deployment. You want the hub's chat,
run history, cost accounting and a live view of what the graph is doing.

This example does that without changing the graph. The whole integration is one
process that loads your compiled graph by name and translates LangGraph's own
stream events into the frames the hub renders.

```
your repo (unchanged)          this adapter              Agents Hub
┌───────────────────┐        ┌──────────────┐         ┌──────────────┐
│ graph = ….compile │◀──────▶│ adapter.py   │◀── HTTP▶│ chat, runs,  │
│ nodes, tools, LLM │ import │ /run /stream │  NDJSON │ costs, logs  │
└───────────────────┘        └──────────────┘         └──────────────┘
```

## Run it in a minute

```bash
pip install -r requirements.txt
AGENTHUB_GRAPH=demo_graph:graph uvicorn adapter:app --port 8420
```

Then, in another terminal:

```bash
curl localhost:8420/health
curl -N -X POST localhost:8420/run/stream \
     -H 'content-type: application/json' \
     -d '{"prompt":"what is the price of the team plan?"}'
```

The stream that comes back is what the hub renders:

```json
{"type": "node_start", "node": "triage", "depth": 0}
{"type": "token", "token": "Reading"}
{"type": "node_end", "node": "triage", "ok": true, "next": "pricing"}
{"type": "node_start", "node": "pricing", "depth": 0}
{"type": "tool_start", "name": "lookup_pricing", "input": "{\"product\": \"team\"}"}
{"type": "tool_end", "name": "lookup_pricing", "output": "$99/mo"}
{"type": "done", "ok": true, "output": "…", "error": null}
```

`GET /graph` returns the same graph's shape, which is what the agent page draws:

```bash
curl -s localhost:8420/graph
```

`demo_graph.py` is a stand-in for your graph: ordinary LangGraph code with a
conditional edge and a tool, which knows nothing about the hub. It runs on a
deterministic fake model when no provider key is set, so the example works
offline.

## Point it at your own graph

One environment variable, in the `module:attribute` spelling `langgraph.json`
already uses:

```bash
AGENTHUB_GRAPH=my_project.agent:graph uvicorn adapter:app --port 8420
```

Either a compiled graph or a zero-argument factory that returns one. Two more
variables cover graphs whose state is not a message list:

| Variable | Default | When you need it |
| --- | --- | --- |
| `AGENTHUB_GRAPH` | — | Always. Your graph, as `module:attribute`. |
| `AGENTHUB_INPUT_KEY` | `messages` | Your state takes the prompt under another key. |
| `AGENTHUB_OUTPUT_KEY` | input key | The answer lives under a different key than the input. |

The bundled graphs: `demo_graph:graph` (a branch and a tool) and
`approval_graph:graph` (stops to ask a person; takes `AGENTHUB_INPUT_KEY=request`
and `AGENTHUB_OUTPUT_KEY=steps`).

Nothing else about the graph is this adapter's business: which models it calls,
which tools it binds, what it is allowed to touch, how it is deployed.

## Import it into the hub

Agents → **Import agent**, then either:

- the **repository URL**, if this adapter sits in a repo the hub can clone, and
  let the hub build it from `Dockerfile.agenthub`; or
- the **URL of an already-running service**, if the graph is deployed and you
  just want the hub to watch it. Nothing is cloned in that case.

The readiness report names anything missing, and the agent is registered either
way, so the gaps stay visible on the agent card rather than in a dialog you
dismissed. After that the graph is reachable from chat, tasks and hub flows like
any other agent.

## How the events map

LangGraph already narrates itself. `astream_events` carries the node name in
each event's metadata, which is what makes the translation almost mechanical:

| LangGraph event | Hub frame | Shows up as |
| --- | --- | --- |
| `on_chat_model_stream` | `token` | text appearing in the chat bubble |
| `on_chat_model_end` | `usage` | tokens and cost on the run |
| `on_tool_start` / `on_tool_end` | `tool_start` / `tool_end` | the tool trail |
| node `on_chain_start` / `on_chain_end` | `node_start` / `node_end` | which node is running |
| a suspended graph (`interrupt()`) | `interrupt` | the run parked, with its question |
| root `on_chain_end` | final `output` | the answer of record |

`node_start` and `node_end` render as steps inside this agent's own reply, not
as separate bubbles: the answer still comes from one agent. `next` says which
branch a conditional edge actually took, which LangGraph never states outright:
the adapter holds each `node_end` for the microseconds until the following node
starts, and names it. `GET /graph` serves the topology
(`get_graph().to_json()`, normalised), which the agent page draws as a read-only
mirror of the graph, refetched on demand and on every re-check.

## A graph that stops to ask

`approval_graph.py` is a second bundled graph: it calls `interrupt()` and waits
for a person. Serve it instead of the demo:

```bash
AGENTHUB_GRAPH=approval_graph:graph AGENTHUB_INPUT_KEY=request \
AGENTHUB_OUTPUT_KEY=steps uvicorn adapter:app --port 8420
```

```bash
curl -N -X POST localhost:8420/run/stream -H 'content-type: application/json' \
     -d '{"prompt":"ship 4.2","run_id":"run-1"}'
# … {"type":"interrupt","question":"Approve this plan?","choices":["approve","reject"], …}

curl -N -X POST localhost:8420/resume -H 'content-type: application/json' \
     -d '{"run_id":"run-1","value":"approve"}'
# … {"type":"done","ok":true,"output":"done: the plan was carried out"}
```

The stream ends with `interrupt` rather than `done`, and the hub records the run
as `awaiting_input` with the question on it: the same state, and the same task
page, its own agents reach through `ask_user`. When somebody answers, the hub
posts to `/resume` and the graph continues **where it stopped**, because the run
id it was given is the thread its checkpointer filed the suspension under.

That is the whole reason this endpoint exists. Re-running the graph with the
answer in its input, which is how the hub resumes agents that keep no state,
would be a different execution that merely reads the same way.

Note what the pause needs from your side: a checkpointer. `interrupt()` does not
work without one, and an in-memory one forgets every paused run on restart.

## What you keep, what you give up

Yours: the models, the tools, the checkpointer, the prompts, the deployment, the
dependency tree. The hub never imports your Python.

Given up, and this follows from the process boundary rather than from this
example:

- **Token accounting is only as good as the `usage` frames.** The adapter sends
  them whenever a model reports `usage_metadata`, which the LangChain chat
  models do. A graph calling a provider SDK directly reports nothing, and its
  cost columns read zero.
- **No hub tools and no hub guards.** The capability guard, the repetition
  limiter and the context-window guard work by intercepting an agent's own loop.
  Your graph's safety stays your repository's business.
- **A pause needs a checkpointer.** Without one `interrupt()` does not work at
  all, and with an in-memory one a restart forgets every paused run.

See [docs/imported-agents.md](../../../docs/imported-agents.md) for the contract
itself and [EXTERNAL_CONNECTIONS.md](../../../EXTERNAL_CONNECTIONS.md) for the
push-mode design, where your graph runs on your own triggers and reports to the
hub instead of being called by it.
