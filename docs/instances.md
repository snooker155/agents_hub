# Instances

An instance is a live copy of an agent: its own state, its own label, its own
history. Where an [agent](agents.md) is a definition and a run is one unit of
work, an instance is the copy that does the work and keeps what it did.

Most instances come and go with their work: a chat conversation, a task run, a
flow step. A **resident instance** is the one you start on purpose: **Run** on
an agent's page (or `POST /api/instances`) starts a copy that lives in a
process of its own until you stop it, and that you can talk to and give tasks
to.

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

Run opens a form: workspace, an optional label, and the instance's inputs
(below). Submitting it starts the process and
opens the instance page right away; the state turns from starting to standby
once the process is listening.

```
POST /api/instances
{"agent_id": "swe_agent", "workspace": "default",
 "label": "reviewer", "take_tasks": false, "publish": false, "concurrency": 4}
```

`ah instance start <agent>` does the same from the CLI; `ah instance list`,
`stop`, `restart` and `logs` manage it, and `ah instance message <id> "text"
-c <conversation>` writes to it and prints the answer.

### The carrier

One resident instance is one process, `runtime/instance_run.py`, or one
container running it: the workspace's execution mode decides which. Two instances never share
a process, because the process variables and the working directory belong
to a process. The instance row records the carrier:
`carrier_mode` (`local` or `docker`), `pid` or
`container_name`, `carrier_status`, `carrier_log_file`, `heartbeat_at`.

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

- **The mailbox**, always: the instance page and the CLI. See conversations
  below.
- **Take tasks** (`take_tasks`): the instance also runs tasks assigned to its
  agent in its workspace, one at a time, when the workspace runs tasks on
  instances. Tasks of an
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
and can open more.
`GET /api/instances/{id}/conversations` lists them.

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
twice. A mailbox holds three kinds of message: a plain `message`, answered with
the conversation's history; a `turn`, a whole chat request written there by
the backend (chat/routing.py) and executed by the carrier as the chat
pipeline, its events posted back to the backend for the conversation's
channel and the instance's; and a `job`, one agent invocation the backend
hands over (a replay, a decomposition, the agent part of an
entity chat), answered with a result document on the row. A resident instance
is woken the moment a message is written, or else by checking its mailbox
every `AGENTS_HUB_INSTANCE_POLL_SECONDS` (default 0.5). A message sitting in
the mailbox of a standby instance means nothing picked it up, which is usually
a carrier problem rather than an agent problem: check the Process tab.

A message to a copy that is busy with a task run is also handed to that run
through [steering](steering.md): the agent reads it before its next model step.
It stays in the mailbox until one side takes it, so it is answered once, by the
run, or by the copy when it is free if the run ended first. A copy that is not
resident and has finished is revived with its history rebuilt from its runs.

Related: [sessions-and-runs](sessions-and-runs.md), [containers](containers.md), [service-health](service-health.md), [steering](steering.md).
