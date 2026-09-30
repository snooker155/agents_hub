# Instances

An instance is a live copy of an agent: its own state, its own label, its own
history. Where an [agent](agents.md) is a definition and a run is one unit of
work, an instance is the copy that does the work and keeps what it did.

Most instances come and go with their work: a chat conversation, a task run, a
flow step. A **resident instance** is the one you start on purpose: **Run** on
an agent's page (or `POST /api/instances`) starts a copy that lives in a
process of its own until you stop it, and that you can talk to and give tasks
to. It replaces what used to be a node. A **replica** is a resident instance
a [service](services.md) started to match its desired state; a **runner** is
a replica bound to no agent, which answers any agent's chat turn in its own
process, and is where the chat, `/v1`, the widget and Telegram run since the
backend itself runs no agent.

## States

- **starting**: coming up
- **active**: running a turn right now
- **standby**: alive, waiting for the next message
- **finished**: completed its work
- **stopped**: ended deliberately
- **failed**: ended badly

`live` counts starting, active and standby together. That is the number to
watch when you want to know what is running.

## Resident instances

### Starting one

Run opens a form: workspace, [environment](environments.md), an optional
label, and the instance's inputs (below). Submitting it starts the process and
opens the instance page right away; the state turns from starting to standby
once the process is listening.

```
POST /api/instances
{"agent_id": "swe_agent", "workspace": "default", "environment_id": null,
 "label": "reviewer", "take_tasks": false, "publish": false, "concurrency": 4}
```

`ah instance start <agent>` does the same from the CLI; `ah instance list`,
`stop`, `restart` and `logs` manage it, and `ah instance message <id> "text"
-c <conversation>` writes to it and prints the answer.

### The carrier

One resident instance is one process, `runtime/instance_run.py`, or one
container running it: the environment decides which. A replica of a service
carries the service's id (`service_id`) and shows it on its page and in the
list; a runner's process is started without an agent. Two instances never share
a process, because the environment, the process variables and the working
directory belong to a process. The instance row records the carrier:
`carrier_mode` (`local` or `docker`), `carrier_host`, `pid` or
`container_name`, `carrier_status`, `carrier_log_file`, `heartbeat_at`.
Signals and docker calls only work from the host that started it; a stop asked
from another host is written to the row and the process stops itself on its
next heartbeat.

**Restart** replaces the process and keeps the instance: its id, its
conversations and its runs stay. Every process an instance has had is a row of
its carrier history (`GET /api/instances/{id}/carriers`). **Stop** ends the
process; the instance keeps its history and can be started again. A message
written to a stopped resident instance starts it again rather than being
answered by the backend: the backend never runs a resident instance's agent.

The carrier writes its own log (starts, state changes, errors) to
`instance_logs/instance_<id>.log`, shown on the instance page. Each run keeps
its own log as before.

### Inputs

What a resident instance listens to. Both can be switched on the running
instance (`PATCH /api/instances/{id}/inputs`) without a restart.

- **The mailbox**, always: the instance page, the public address, a direct
  port. See conversations below.
- **Take tasks** (`take_tasks`): the instance also runs tasks assigned to its
  agent in its workspace, one at a time, when the workspace runs tasks on
  instances (execution mode `node` in the orchestrator settings). Tasks of an
  agent then run only on copies started for them, which is how you cap the
  resources an agent's tasks may use. An `orchestrator` instance that takes
  tasks routes new tasks instead: this is what the Orchestrator page starts.
- **Parallel conversations** (`concurrency`, default 4, at most 32): how many
  runs the instance answers at once. Two messages of one conversation are never
  answered at the same time: the second waits for the first one's answer.

### Conversations

Every message belongs to a conversation of its instance, and each conversation
has its own history: a run carries only the earlier turns of its own
conversation. The instance page writes to the main one (`"main"` on the API)
and can open more; a caller of the public address names its own
`conversation_id`, so two callers never see each other's turns.
`GET /api/instances/{id}/conversations` lists them.

### Publishing

**Publish** gives the instance a public address through the hub and a token
(publishing again rotates it; withdrawing stops it at once):

```
POST /api/external/{token}/messages
{"message": "review PR 42", "conversation_id": "ci-bot", "wait_seconds": 60}
```

- By default the call waits up to `wait_seconds` (60, at most 300) and returns
  the answer with `200` (`status` `completed`, `output`), or `202` with where the
  message stands (`queued` or `running`) and a `poll_url`
  (`GET /api/external/{token}/messages/{msg_id}`).
- `"stream": true` returns Server-Sent Events: `accepted`, the run's live
  events (`meta`, `token`, `tool_start`, `tool_end`, `thinking`, `done`) and a
  closing `reply` with the answer.
- `POST /api/external/{token}/run` (`{"prompt": ...}`) is the older address:
  it creates a task for the instance's agent and returns `202` with its id.

An inbound secret set on the instance makes every public call require a
signature, the same scheme [notifications](notifications.md) uses for what
the hub sends. The public routes are throttled per client address:
`AGENTS_HUB_EXTERNAL_RATE_PER_MINUTE` requests in any 60 seconds (default 30,
0 turns it off), counted in memory per API replica. Known and unknown tokens
share the window, so guessing tokens is as slow as flooding a real instance.
Past the limit the route answers `429` with `Retry-After`. Tokens are compared
in constant time. Every call lands in the instance's connection history
(`GET /api/instances/{id}/connections`), shown on its Access tab.

An agent that asks for its own HTTP port (`http_expose`, or a `service` node
type in its definition), or an instance started with `direct_port`, also
answers on its container port (`runtime/http_server.py`: `POST /run` with
`{"prompt", "conversation_id"}`, `GET /health`, `/status`, `/logs`). Messages
sent there land in the same mailbox.

### Live output

Every run of a resident instance streams to the channel `instance:<id>` of the
dashboard stream: a `meta` event naming its message, conversation (or task),
then tokens, tool calls and thinking, `done`, and `instance_stream_end`. The
instance page opens a live block for the conversation it shows.

## The timeline

An instance's timeline is what it has actually been doing in one
conversation: recent turns, which tools it called, and how each run ended.
This is the level at which "the agent is stuck" becomes visible, because a
stuck instance usually shows the same tool call repeating.

## The mailbox

Messages are queued to an instance and claimed atomically, so none is answered
twice. A mailbox holds two kinds of message: a plain `message`, answered with
the conversation's history (a service replica uses the service conversation's
history, across replicas), and a `turn`, a whole chat request written there
by the backend (chat/routing.py, [services](services.md)) and executed by
the carrier as the chat pipeline, its events posted back to the backend for
the conversation's channel and the instance's. A `job` is one agent
invocation the backend hands over (an eval case, a replay, a decomposition,
the agent part of an entity chat), answered with a result document on the
row. A runner takes turns and jobs; a plain message to it must name the
agent that answers (`agent_id`), and becomes a turn of that agent. A resident instance is woken the moment a message is written: through
Redis when `AGENTS_HUB_BROKER_URL` is set, otherwise by checking its mailbox
every `AGENTS_HUB_INSTANCE_POLL_SECONDS` (default 0.5). A message sitting in
the mailbox of a standby instance means nothing picked it up, which is usually
a carrier problem rather than an agent problem: check the Process tab.

A message to a copy that is busy with a task run is also handed to that run
through [steering](steering.md): the agent reads it before its next model step.
It stays in the mailbox until one side takes it, so it is answered once, by the
run, or by the copy when it is free if the run ended first. A copy that is not
resident and has finished is revived with its history rebuilt from its runs.

## From nodes to instances

Nodes were folded into instances by migration 0029: every node became the
resident instance it carried (a node without one got one), with its process
fields, inputs (a worker node takes tasks, a service node does not; one
conversation at a time) and publication, its runs linked to the instance and
its connection history moved over. A node process that was running keeps
running and can be stopped from the instance page. `/api/nodes` and the Nodes
page are gone; `/nodes/<id>` links open the instance the node became.

Related: [sessions-and-runs](sessions-and-runs.md), [containers](containers.md), [environments](environments.md), [service-health](service-health.md), [steering](steering.md).
