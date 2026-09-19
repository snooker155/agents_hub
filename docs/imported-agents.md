# Imported agents

An agent that already exists, already works and has its own dependencies does
not need rebuilding here. Import it from its git repository: the hub clones it,
checks whether it can run, and adds it to the agent list either way.

Distinct from the [marketplace](marketplace.md), which shares definitions built
*in* this product. An imported agent's behaviour stays in its own repository.

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
- **Packaging** — a Dockerfile named in the manifest, or an already-running
  service whose URL you give at import time.
- **`runtime.env`** — what the agent needs (model keys, tokens). Required
  entries that are unset are reported as missing rather than failing mid-run.

An agent with no manifest is still importable: the readiness report names each
missing piece, and the agent is registered in a "needs setup" state so the gaps
stay visible where the agent is rather than in a dismissed dialog.

## The import flow

1. **Check repository** — clone and analyse. Nothing is registered yet.
2. **Read the readiness report** — each gap is listed with its fix.
3. **Register** — the agent joins the list, report attached.
4. **Re-check** — re-run the checks after filling a gap in, for example once the
   service is up at the endpoint you supplied.

A worked example ships with the product under
`examples/imported-agents/aider-agenthub`: a three-file adapter that makes
[Aider](https://github.com/Aider-AI/aider) importable without changing aider
itself.

## What you give up

The process boundary that keeps a foreign agent's dependencies out also hides
its internals:

- **Token and cost accounting depend on the agent.** Callbacks observe nothing
  across a process boundary. A remote that sends a `usage` frame is accounted
  for normally; one that does not leaves the cost columns at zero.
- **No hub tools.** The imported agent uses its own tool layer. The tool list in
  its manifest is documentation, not a grant.
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

Related: [agents](agents.md), [marketplace](marketplace.md), [containers](containers.md), [tools-and-capabilities](tools-and-capabilities.md).
