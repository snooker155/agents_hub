# Imported agents

An agent that already exists, already works and has its own dependencies does
not need rebuilding here. Import it from its git repository: the hub clones it,
checks whether it can run, and adds it to the agent list either way.

Distinct from the [marketplace](marketplace.md), which shares definitions built
*in* this product. An imported agent's behaviour stays in its own repository.

Distinct too from a [connection](connections.md), which is the same idea with
the direction reversed: there the external agent runs on its own trigger and
reports in, and the hub never calls it.

## Where it runs

Outside this process. The hub never imports the agent's Python, so its
dependency tree stays its own; prompts are forwarded over HTTP. To the rest of
the product it looks like any other agent, so chat, tasks and flows reach it
through the unchanged path.

## The contract

One manifest file at the repository root (`agent-hub.json`, `.agent-hub.json` or
`agenthub.json`) declaring the agent's id, name and a runtime block, and one
HTTP endpoint:

- **`POST <run_path>`** — receives `{prompt, run_id, workspace}`, replies
  `{ok, output, error}`. Required; everything else is optional.
- **`GET <health_path>`** — any 2xx/3xx. Used by the readiness check and by the
  agent card.
- **`POST <stream_path>`** — optional. Answers with NDJSON or SSE frames
  (`token`, `thinking`, `tool_start`, `tool_end`, `usage`, `done`). The agent's
  tokens then appear in the chat bubble live, exactly like a built-in agent's,
  because the frames are translated onto the same event vocabulary.
- **`GET <graph_path>`** — optional. An agent that is internally a graph
  publishes its own shape here, and the hub draws it. See
  [the shape of an agent](#the-shape-of-an-agent).
- **`POST <resume_path>`** — optional. An agent that stops to ask a person
  declares this and is *continued* through it. See
  [pausing to ask](#pausing-to-ask).
- **Packaging** — a Dockerfile named in the manifest, or an already-running
  service whose URL you give at import time.
- **`runtime.env`** — what the agent needs (model keys, tokens). Required
  entries that are unset are reported as missing rather than failing mid-run.

An agent with no manifest is still importable: the readiness report names each
missing piece, and the agent is registered in a "needs setup" state so the gaps
stay visible where the agent is rather than in a dismissed dialog.

## The shape of an agent

Some imported agents are a graph inside: a LangGraph flow, a crew, a state
machine. Rendering one as a single box that lights up loses the only thing worth
watching, which is where in the graph a run currently is and which branch it
took.

Two optional pieces cover that, and an agent that declares neither is unchanged:

- **Node frames on the stream.** `{"type": "node_start", "node": "triage"}` and
  `{"type": "node_end", "node": "triage", "ok": true, "next": "pricing"}`. They
  render as steps inside the agent's own reply, not as separate bubbles: the
  answer still comes from one agent. `next` is the branch actually taken, which
  is the question a conditional edge raises.
- **`runtime.graph_path`.** A `GET` returning `{framework, nodes, edges}`, which
  the agent page draws as a read-only mirror. It is fetched at import and at
  every re-check, not per run: a graph's shape changes when its repository is
  redeployed, not between requests.

The mirror is deliberately not the [flow](flows.md) canvas. A flow is something
this hub executes against agents it owns; a mirrored graph is a picture of
something it does not run, so it has no Run button and no editing.

Node identity is the remote's business. The hub draws what it is told, bounds
it, and drops edges pointing at nodes that were never declared rather than
inventing them.

## Pausing to ask

An imported agent may stop and wait for a person. It ends its stream with an
`interrupt` frame instead of `done`:

```json
{"type": "interrupt", "question": "Approve this plan?",
 "choices": ["approve", "reject"], "key": "i-7", "node": "approve"}
```

The run is then recorded as `awaiting_input` with the question on it, which is
the same state, and the same task page, an agent of this hub's own reaches by
calling `ask_user`. Answering it posts to `<resume_path>`:

```json
{"run_id": "the run that paused", "value": "approve", "key": "i-7"}
```

and the agent streams whatever it does next, pausing again if it needs to.

**Why not just re-run it with the answer?** That is how this hub resumes its own
agents, and it is right for them: they keep nothing between runs, so replaying
the conversation *is* the resume. An imported agent that suspended onto a
checkpointer is the other case. It has somewhere to come back to, and starting
it again from the top is a different execution that merely reads the same way.
An agent that declares no `resume_path` is re-run, exactly as before.

The bundled example ships a graph that does this
(`approval_graph.py`; serve it with `AGENTHUB_GRAPH=approval_graph:graph`).

## The import flow

1. **Check repository** — clone and analyse. Nothing is registered yet.
2. **Read the readiness report** — each gap is listed with its fix.
3. **Register** — the agent joins the list, report attached.
4. **Re-check** — re-run the checks after filling a gap in, for example once the
   service is up at the endpoint you supplied.

Two worked examples ship with the product. Under
`examples/imported-agents/aider-agenthub`, a three-file adapter that makes
[Aider](https://github.com/Aider-AI/aider) importable without changing aider
itself. Under `examples/imported-agents/langgraph-agenthub`, an adapter for a
LangGraph graph: it loads whichever compiled graph `AGENTHUB_GRAPH` names, in
the `module:attribute` spelling `langgraph.json` already uses, and translates
LangGraph's own stream events into the frames above. A team with a working graph
integrates by setting one environment variable, with no diff in the graph.

`examples/imported-agents/langgraph-agenthub-js` is the same adapter for a
LangGraph.js graph, serving the same contract and reporting the same frames, so
a graph looks the same here whichever runtime it runs on. Its own README lists
the four places the JS event stream differs from the Python one, which is the
part worth reading before writing an adapter of your own.

## Claude Code and Codex

Two more bundled examples wrap coding agent CLIs directly: **Claude Code**
under `examples/imported-agents/claude-code-agenthub` and **Codex** under
`examples/imported-agents/codex-agenthub`. Both follow the same three-file
shape as the Aider example: a manifest, a Dockerfile, and an adapter that
translates the CLI's own JSON output into the frame vocabulary above.

**Adding one.** On the Agents page, the "Add Claude Code" and "Add Codex"
buttons next to "Import from repo" open the import dialog preselected on the
matching preset, with no repository URL to type. Under the hood this calls
`GET /api/agent-import/presets` to list what is bundled, then
`POST /api/agent-import/inspect` and `/register` with `{"preset": "claude-code"}`
(or `"codex"`) in place of `repo_url`. The example directory is copied into
scratch space the same way `prepare_example_repo.sh` makes one clone-able, so
a preset import needs no network and no git repository of its own.

**What each CLI needs.** Claude Code runs `claude -p <prompt> --output-format
stream-json --verbose` in the run's mounted workspace, so the container needs
`ANTHROPIC_API_KEY` (and optionally `CLAUDE_MODEL`, `ANTHROPIC_BASE_URL`).
Codex runs `codex exec --json <prompt>` and needs `OPENAI_API_KEY` (and
optionally `CODEX_MODEL`, `CODEX_ARGS` for a version-specific non-interactive
flag). Both need their own key: the hub's own configured providers play no
part in what these agents run on, since the model call happens inside the
CLI's own process, not this hub's.

**What the run record shows.** Claude Code prices its own call and reports it
on the closing `result` event as `total_cost_usd`. The adapter puts that
figure on the stream's `usage` frame as an optional `cost_usd`, and the hub
credits it to the run as `reported_cost_usd`, preferred over catalog pricing
wherever a run's cost is read (`managers.runs.groups.runs_cost`, the Costs
page). Practically: launching Claude Code from chat shows Anthropic's own
bill for that call in the run record, not an estimate. Codex is not known to
report a dollar figure of its own, so its runs are priced from its reported
token counts against the hub's catalog, the same as any other agent that
sends a plain `usage` frame.

**Limits.** The CLI runs inside the agent's own container and needs its own
key: nothing about the hub's configured model providers reaches it. Cost
credit only happens on the streamed path (chat, a flow node with someone
watching); the synchronous `/run` endpoint returns the same figures inside
its response body, but the hub does not read them from there, same as every
other bundled example's `/run`. The Codex adapter's event shapes were written
from documentation rather than a captured transcript, since the CLI was not
installed while writing it; its README says so and names what to verify
against a real install.

## Importing an A2A agent

An agent that speaks [A2A](a2a.md), the Agent2Agent protocol, needs no manifest
and no repository: it is already running and its Agent Card already says where
its endpoint is and what it can do. Paste the card URL (it ends in
`/.well-known/agent-card.json`) where the dialog asks for a repository URL, and
the hub reads the card instead of cloning. Everything after that is the same:
the same readiness report, the same registry record, the same agent page.

A repository may also declare `runtime.kind = "a2a"` with the endpoint under
`runtime.url`, for an agent whose code lives in git but whose service runs
somewhere else.

The hub serves the other direction too: every agent here has its own A2A card,
so an outside orchestrator can drive it without knowing this API. The agent page
shows that URL with a copy button.

## What you give up

The process boundary that keeps a foreign agent's dependencies out also hides
its internals:

- **Token and cost accounting depend on the agent.** Callbacks observe nothing
  across a process boundary. A remote that sends a `usage` frame is accounted
  for normally; one that does not leaves the cost columns at zero. A remote
  that also knows its own dollar cost (Claude Code prices its own call) may
  add `cost_usd` to that frame, and the hub prefers that reported figure over
  its own catalog pricing everywhere a run's cost is read.
- **No hub tools.** The imported agent uses its own tool layer. The tool list in
  its manifest is documentation, not a grant.
- **The graph is a report, not an instrumented truth.** The hub draws the shape
  the agent describes and the nodes it announces. An agent that under-reports
  its nodes looks simpler than it is.
- **In-run guards do not apply.** Tool-repetition limits, the context-window
  guard and the [capability guard](tools-and-capabilities.md) work by
  intercepting an agent's own loop. A remote agent's safety is its repository's
  business.

## Gotchas

- Streaming is used only when both halves are present: the repository declared
  the endpoint *and* something here is listening. Otherwise the single POST
  runs, which every imported agent must support anyway.
- A `done` frame is authoritative, but a stream that ends without one still
  yields the tokens it already sent.
- An import whose service is not running imports fine and simply cannot run.
  That is the intended state, not a failure.

Related: [agents](agents.md), [a2a](a2a.md), [marketplace](marketplace.md), [containers](containers.md), [tools-and-capabilities](tools-and-capabilities.md).
