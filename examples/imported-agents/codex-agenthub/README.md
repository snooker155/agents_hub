# Codex as an imported Agents Hub agent

A worked example of the agent-import contract, wrapping
[Codex](https://github.com/openai/codex), OpenAI's own coding agent CLI.
Nothing about Codex is reimplemented here; this repository adds three files:

| File | Why it exists |
| --- | --- |
| `agent-hub.json` | The manifest the hub reads. Declares the id, the runtime block and the environment Codex needs. |
| `server.py` | Translates `POST /run` into one `codex exec --json` invocation and back, and streams the same run over `POST /run/stream`, turning the CLI's own JSONL events into the hub's frames. |
| `Dockerfile.agenthub` | Packages the CLI (Node) plus the adapter (Python) so the service can be started from the manifest. |

## Not verified against a live `codex`

The `codex` CLI was not installed in the environment this adapter was written
in. `server.py` implements the event shapes (`thread.started`, `turn.started`,
`item.started`/`item.completed` with `item.type` of `agent_message` |
`command_execution` | `file_change` | `reasoning`, and `turn.completed` with
`usage.{input_tokens,cached_input_tokens,output_tokens}`) from documentation
rather than a captured transcript, and does not assume a specific
non-interactive/approval flag beyond `codex exec --json`. Set `CODEX_ARGS` to
whatever your installed version's `codex exec --help` names for that, if
anything. Before relying on this in production: build the image, run
`codex exec --help` inside it, and run a real prompt to confirm the event
shapes still match; adjust `CodexTranslator` if they do not.

## Run it

```bash
docker build -f Dockerfile.agenthub -t codex-agenthub .

docker run -d --name codex-agent \
  -p 8440:8440 \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -v /path/to/the/repo/codex/should/edit:/work \
  codex-agenthub

curl localhost:8440/health
```

## Import it

In the dashboard: **Agents → Add Codex** (opens the import dialog preselected
on this example), or **Agents → Import from repo** pointed at a standalone
copy of this directory:

```bash
../prepare_example_repo.sh codex-agenthub
```

Enter `http://localhost:8440` as the endpoint once the container is up.

## Cost

Codex is not known to report a dollar figure of its own the way Claude Code's
`total_cost_usd` does, only token counts on `turn.completed`. This adapter
therefore sends a `usage` frame with no `cost_usd`, and the hub prices the run
from those token counts against its own catalog, the same as any other agent
that reports usage but no cost of its own.

## What you give up

Same as the Claude Code example: the key and the model choice are the
container's own, not the hub's configured providers; the hub supplies no
tools (Codex has its own); and safety is whatever this container and the
CLI's own approval mode provide, not the hub's guards, which only see an
agent's own loop.
