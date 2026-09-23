# Claude Code as an imported Agents Hub agent

A worked example of the agent-import contract, wrapping
[Claude Code](https://github.com/anthropics/claude-code), Anthropic's own
coding agent CLI. Nothing about Claude Code is reimplemented here; this
repository adds three files:

| File | Why it exists |
| --- | --- |
| `agent-hub.json` | The manifest the hub reads. Declares the id, the runtime block and the environment Claude Code needs. |
| `server.py` | Translates `POST /run` into one `claude -p` invocation and back, and streams the same run over `POST /run/stream`, turning the CLI's own `stream-json` events into the hub's frames. |
| `Dockerfile.agenthub` | Packages the CLI (Node) plus the adapter (Python) so the service can be started from the manifest. |

The hub never imports Claude Code's own code. It sends a prompt over HTTP and
records the answer, which is what keeps the CLI's own runtime out of the
hub's process.

## Run it

```bash
docker build -f Dockerfile.agenthub -t claude-code-agenthub .

docker run -d --name claude-code-agent \
  -p 8430:8430 \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -v /path/to/the/repo/claude/should/edit:/work \
  claude-code-agenthub

curl localhost:8430/health
```

## Import it

In the dashboard: **Agents → Add Claude Code** (a shortcut that opens the same
import dialog preselected on this example), or **Agents → Import from repo**
pointed at this directory (`../prepare_example_repo.sh claude-code-agenthub`,
see below) or your own fork of it. Enter `http://localhost:8430` as the
endpoint once the container is up.

## Trying it without a remote

This example ships inside the Agents Hub repository, so it is not a git
repository of its own:

```bash
../prepare_example_repo.sh claude-code-agenthub
```

## Cost, in full

Claude Code prices its own call and reports the dollar figure on the closing
`result` event as `total_cost_usd`. The adapter puts that figure on the
`usage` frame as `cost_usd`, and the hub (`agents.remote_agent._credit`)
writes it to the run as `reported_cost_usd`, which `managers.runs.groups`
and the Costs page prefer over pricing the reported token counts against
the hub's own catalog. Practically: a Claude Code run's cost in the run
record and on the Costs page is Anthropic's own bill for that call, not an
estimate.

This only happens for a **streamed** run (chat, a flow node with someone
watching). The synchronous `/run` endpoint returns the same figures inside
its response body, but nothing on the hub side reads them from there, the
same limitation the bundled aider example has for its own `/run`.

## What you give up

- **The key is the container's, not the hub's own model access.** Claude Code
  reads `ANTHROPIC_API_KEY` (and optionally `ANTHROPIC_BASE_URL`) itself; the
  hub's own configured providers are irrelevant to what this agent runs on.
- **No hub tools.** Claude Code edits files and runs shell commands with its
  own tool loop; the tool list in the manifest is documentation, not a grant.
- **No hub-side guards.** Tool-repetition limits, the context-window guard and
  the capability guard all work by intercepting an agent's own loop, which
  runs in this container. Safety here is the CLI's own permission model
  (`--permission-mode bypassPermissions`, since this container is the
  sandbox) plus whatever the repository it edits can tolerate.
- **The CLI's own JSON shape is not a versioned contract.** `server.py`
  documents the fields it reads, verified against the CLI installed while
  writing this adapter; a future Claude Code release could rename or add
  fields, which is why unrecognised event types are skipped rather than
  guessed at.
