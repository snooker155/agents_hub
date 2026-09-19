# Aider as an imported Agents Hub agent

A worked example of the agent-import contract. [Aider](https://github.com/Aider-AI/aider)
is an existing, tested open-source code agent; nothing about it is reimplemented
here. This repository adds three files and that is the entire integration:

| File | Why it exists |
| --- | --- |
| `agent-hub.json` | The manifest the hub reads. Declares the id, the runtime block and the environment aider needs. |
| `server.py` | Translates `POST /run` into one `aider --message` invocation and back, and streams the same run over `POST /run/stream`. |
| `Dockerfile.agenthub` | Packages aider plus the adapter so the service can be started from the manifest. |

The hub never imports aider's Python. It sends a prompt over HTTP and records
the answer, which is what keeps aider's dependency tree out of the hub's process.

## Run it

```bash
docker build -f Dockerfile.agenthub -t aider-agenthub .

docker run -d --name aider-agent \
  -p 8410:8410 \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -v /path/to/the/repo/aider/should/edit:/work \
  aider-agenthub

curl localhost:8410/health
```

## Import it

In the dashboard: **Agents → Import from repo**, paste this repository's URL,
press **Check repository**, and enter `http://localhost:8410` as the endpoint.

The readiness report tells you what is still missing before it will run. All of
it is fixable after import — the agent is added either way, and its page has a
**Re-check** button.

## Trying it without a remote

This example ships inside the Agents Hub repository, so it is not a git
repository of its own. `prepare_example_repo.sh` in the parent folder makes a
standalone clone-able copy in a temp directory and prints the path to paste into
the import dialog:

```bash
../prepare_example_repo.sh aider-agenthub
```

## The contract, in full

```
POST <run_path>
  {"prompt": "add retry handling to fetch_user", "run_id": "...", "workspace": "/work"}

  {"ok": true, "output": "Applied edits to api/users.py ...", "error": null,
   "steps": [{"name": "aider", "args": {...}, "output": "..."}]}

GET <health_path>   ->  any 2xx/3xx
```

`ok: false` with HTTP 200 is a normal, recorded failure — the body carries the
agent's own explanation. A 5xx or a refused connection is reported as an
unreachable agent instead.

## Streaming

Aider spends minutes reading files and proposing edits. Without a stream the
hub's chat would sit blank for all of it, so this adapter also serves
`POST /run/stream`, declared as `runtime.stream_path` in the manifest. It
answers with NDJSON — one JSON object per line:

```
{"type": "tool_start", "name": "aider", "input": "aider --message ..."}
{"type": "token", "token": "Applied edits to api/users.py\n"}
{"type": "tool_end", "name": "aider", "output": "..."}
{"type": "done", "ok": true, "output": "...", "changed_files": ["api/users.py"]}
```

Those frames land in the chat bubble exactly like a built-in agent's tokens. The
`done` frame is authoritative — the hub prefers it over the accumulated tokens,
so a noisy stream still yields one unambiguous outcome, while a stream that ends
without one still yields the text it already sent.

The non-obvious part is `PYTHONUNBUFFERED=1` in the child environment
(`_child_env`). Without it aider buffers its stdout in 8 KB blocks when writing
to a pipe, and the "stream" arrives as two or three bursts at the very end.

An agent that knows its own token usage should also send
`{"type": "usage", "prompt_tokens": N, "completion_tokens": N}` — the hub
credits it to the run, which is the only way cost accounting can work across the
process boundary. Aider does not report token counts on stdout, so this adapter
omits it and those runs show no cost.

## What you give up

- **No token or cost accounting here.** Aider does not report its usage on
  stdout, so this adapter sends no `usage` frame and the cost columns stay at
  zero. An agent that does know its usage can send one and be accounted for.
- **No hub tools.** Aider edits files with its own machinery; the tool list in
  the manifest is documentation, not a grant.
- **No hub-side guards.** Tool-repetition limits, the context-window guard and
  the capability guard all work by intercepting an agent's own loop, which runs
  in this container. Safety here is this repository's responsibility.
