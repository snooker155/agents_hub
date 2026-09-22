# A2A (Agent2Agent)

A2A is the open protocol for one agent to call another across products: an agent
publishes a JSON *Agent Card* at a well-known URL, and callers drive it with
JSON-RPC 2.0 over HTTP. The hub speaks it in both directions, so an outside
orchestrator can run an agent here without knowing this API, and an agent that
lives somewhere else can be imported from nothing but its card URL.

## Agent cards

Every registered agent has one:

    GET /api/a2a/agents/<agent_id>/.well-known/agent-card.json
    GET /api/a2a/agents/<agent_id>/.well-known/agent.json      (older clients)

The card carries the agent's name and description, the absolute URL of its
JSON-RPC endpoint, a version taken from the agent's definition history, the
streaming capability, and one skill whose id is the agent id and whose tags are
its domain plus, for an imported agent, the tools its manifest declares. When
the hub has an API token configured, the card declares an `http` bearer scheme,
so a client knows to send it rather than meeting an unexplained 401.

The workspace's default chat agent is also served at the site root:

    GET /.well-known/agent-card.json

With no default configured that path answers 404 with the per-agent URL pattern
in its body, rather than pretending the hub has no cards at all.

The agent page shows this URL with a copy button, which is the whole setup an
external client needs.

## The JSON-RPC endpoint

One endpoint per agent, four methods: `message/send`, `message/stream`,
`tasks/get`, `tasks/cancel`.

    curl -X POST http://localhost:8000/api/a2a/agents/main-agent \
      -H 'Content-Type: application/json' \
      -d '{"jsonrpc": "2.0", "id": "1", "method": "message/send",
           "params": {"message": {"role": "user", "messageId": "m1",
                                  "parts": [{"kind": "text", "text": "Summarise the repo"}]},
                      "configuration": {"blocking": true},
                      "metadata": {"workspace": "default"}}}'

A send creates an ordinary hub task (marked as externally submitted) and starts
a run through the same launcher the dashboard uses, so the work lands in the
task list, the run list and the cost accounting like anybody else's. The reply
is an A2A Task whose `id` is the hub task id and whose `contextId` is the run's
session id. Without `configuration.blocking` it comes back immediately in state
`submitted`; with it the hub waits for the run to finish, up to 120 seconds
(`AGENTS_HUB_A2A_BLOCKING_TIMEOUT`), and answers with the finished Task, the
output carried as a text artifact.

Poll it afterwards with the task id:

    curl -X POST http://localhost:8000/api/a2a/agents/main-agent \
      -H 'Content-Type: application/json' \
      -d '{"jsonrpc": "2.0", "id": "2", "method": "tasks/get",
           "params": {"id": "8f0e...-..."}}'

`tasks/cancel` stops the run behind the task and answers `canceled`; a task that
has already finished is refused with `-32002`, as the spec asks.

Errors use the spec's codes and travel inside an HTTP 200, because an A2A client
reads the envelope: `-32700` parse error, `-32600` invalid request, `-32601`
unknown method, `-32602` invalid params, `-32001` task not found, `-32002` task
not cancelable, `-32004` unsupported operation.

## Streaming

`message/stream` answers `text/event-stream`. Each frame is a full JSON-RPC
response carrying the request id: first the Task, then one
`TaskStatusUpdateEvent` in state `working` per batch of tokens the run produces,
then a `TaskArtifactUpdateEvent` with the answer and a final
`TaskStatusUpdateEvent` with `final: true`. The events come off the same session
channel the dashboard watches, so a client sees the agent's words at the moment
the dashboard does.

## State mapping

| hub run status | hub task status | A2A state |
| --- | --- | --- |
| pending, assigned | todo, ready | `submitted` |
| running | in_progress | `working` |
| (any) | awaiting_input | `input-required`, the question as the status message |
| (any) | awaiting_approval | `input-required`, the pending tool call as the status message |
| completed, done | resolved, done | `completed`, output as a text artifact |
| failed | blocked | `failed`, the error as the status message |
| stopped | stopped | `canceled` |
| anything else | | `unknown` |

The task wins whenever it is parked on a person: a run record can still read
`running` for a moment after the agent asked its question, and reporting
`working` to a client that is in fact being waited on is the one mapping error
that deadlocks a conversation.

## Importing an A2A agent

Paste a card URL where the import dialog asks for a repository URL, for example
`https://agent.example.com/.well-known/agent-card.json`. There is nothing to
clone: the hub fetches the card (http/https only, with a timeout and a size
cap), validates it, and builds the same manifest a repository would have
supplied, with `runtime.kind = "a2a"`, the card's `url` as the endpoint and its
skill ids as the declared tools. Readiness then reports the card instead of the
repository and packaging checks, and the agent is registered as an ordinary
remote agent.

A repository may also declare the runtime itself, when its agent happens to be
running elsewhere:

    {"runtime": {"kind": "a2a",
                 "url": "https://agent.example.com/rpc",
                 "card_url": "https://agent.example.com/.well-known/agent-card.json"}}

Runs of an imported A2A agent send one `message/send` with the prompt as a
single text part and the run id and workspace in `metadata`. A Task that comes
back `completed` yields its artifacts as the output (falling back to the status
message, since many agents answer short questions without an artifact);
`failed`, `rejected` and `canceled` fail the run; `input-required` parks it as
`awaiting_input` with the question on it, and the answer is sent as a second
message carrying the same `taskId`, so the agent continues rather than starting
over. When the card declares `capabilities.streaming`, runs use `message/stream`
and the events become the token frames the chat already renders.

## What is not implemented

- **Push notifications.** The hub never calls a client back, and its card says
  `pushNotifications: false`.
- **Auth beyond bearer.** The card describes the hub's own API token as an
  `http` bearer scheme. OAuth, OpenID and API-key schemes are not served, and an
  imported agent is authenticated the way every imported agent is, with a token
  read from an environment variable named in its descriptor.
- **Multi-turn contexts.** A `message/send` carrying a `taskId` is refused with
  `-32004`: each message to this hub starts its own task. Continuing a
  conversation is implemented in the other direction only, where the hub answers
  an imported agent that paused.
- **`tasks/resubscribe`** and the `tasks/pushNotificationConfig/*` family.
- **Non-text parts.** Only `text` parts are read and produced; a file or data
  part in a request is ignored rather than passed to the agent.
