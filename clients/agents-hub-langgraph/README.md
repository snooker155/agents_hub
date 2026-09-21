# agents-hub-langgraph

Your LangGraph flow runs where it already runs, on your own triggers. This
reports what it does to an Agents Hub, so there is a UI and a run history.

```bash
pip install -e clients/agents-hub-langgraph     # from this hub's repository
```

Not on PyPI yet, so it installs from the repository your hub came from, which is
also how you get the version that matches it.

```python
from agents_hub_langgraph import HubTracer

tracer = HubTracer(url="https://hub.internal", token="ahc_…")
tracer.report_graph(graph)                       # once, so the hub can draw it

graph.invoke(state, config={"callbacks": [tracer]})
```

That is the integration. No adapter process, no endpoint to expose, no diff in
the graph. `AGENTS_HUB_URL` and `AGENTS_HUB_TOKEN` are read from the environment
when the arguments are left out, so a deployment can be configured without a
code change.

Get the token by creating a connection in the hub:

```bash
curl -X POST $AGENTS_HUB_URL/api/connections \
     -H 'content-type: application/json' \
     -d '{"id": "billing-graph", "name": "Billing graph", "kind": "langgraph"}'
```

It is shown once. It authorises that one connection to report runs, and nothing
else: it cannot read the dashboard, create tasks or run anything.

## What the hub gets

| From | Shows up as |
| --- | --- |
| the root chain starting and finishing | a run, with its input, output and duration |
| each graph node | the path the run took, live on the graph picture |
| `on_llm_new_token` | the answer appearing as it is written |
| tool calls | the tool trail on the run |
| `usage_metadata` | tokens and cost |
| `report_graph(graph)` | the graph drawn, branches and all |

## Graphs that stop to ask

`interrupt()` suspends your graph until someone answers. The hub cannot push
that answer to you: in this direction nothing there ever calls your process. So
your run is parked with the question on it, a person answers on the connection's
page, and you collect it:

```python
from langgraph.types import Command

tracer = HubTracer(graph=graph)                  # pass the graph for this

result = graph.invoke(state, config=cfg)
while "__interrupt__" in result:
    answer = tracer.wait_for_answer()            # blocks until somebody answers
    result = graph.invoke(Command(resume=answer), config=cfg)
```

Only your code can resume your graph, so that loop stays yours. `graph=` is what
lets the tracer read *what* the graph is waiting for: LangGraph does not put it
in any callback, so the graph itself is asked. Without it a pause is still
reported, as "waiting in `<node>`".

`wait_for_answer()` returns `None` if nobody answers within its timeout (six
hours by default), so a graph cannot be parked on this forever.

The resumed run is reported as its own run, linked to the one that waited. Runs
here are not held open across a person's lunch break, which is also how this
hub's own agents behave when they ask a question.

## What it will not do to you

This runs inside your process, so it is built to be boring:

- **It never raises into your graph.** Every callback swallows its own errors.
  Pass `on_error=` to see them, read `tracer.errors` for how many there were, or
  rely on the first one being logged once per process on the
  `agents_hub_langgraph` logger. After that it is silent, because a library
  inside somebody's production process does not get to narrate.
- **It never makes your graph wait.** All HTTP is on a worker thread.
- **It never grows without bound.** The queue is capped and drops the oldest
  events if the hub is unreachable.
- **It adds no dependency you do not have.** `langchain-core`, which LangGraph
  already requires, and `urllib` from the standard library.

A hub that is down costs you telemetry, not a run.

## Options

| Argument | Default | What it is for |
| --- | --- | --- |
| `url`, `token` | `AGENTS_HUB_URL`, `AGENTS_HUB_TOKEN` | where to report, and as whom |
| `thread` | none | your own grouping id; runs sharing one land in a single session |
| `title`, `model`, `provider` | none | what the run is called and what it ran on |
| `metadata` | `{}` | anything else you want stored with the run |
| `graph` | none | your compiled graph; lets a pause carry its question, and `report_graph()` take no argument |
| `on_error` | none | called with any exception this package swallows; replaces the one-off warning |
| `flush_interval` | `0.5` | seconds between batches |
| `flush_on_exit` | `True` | wait for the queue at interpreter exit |

`tracer.flush()` blocks until the queue drains. Worth calling in a short-lived
script; `flush_on_exit` already covers the ordinary case. `tracer.close()` does
that and ends the worker thread, for a process about to exit or anywhere a
lingering thread would be a problem of its own.

## One tracer per invocation

A tracer follows one run at a time, which is how a callback handler is used.
Sharing one across concurrent invocations would interleave two runs into one
record, so the second run is refused through `on_error` instead.

## Not using LangGraph?

The endpoints underneath are plain HTTP and language-neutral: open a run, post
frames, close it. See `docs/connections.md` in the hub.
