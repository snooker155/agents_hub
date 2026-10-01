# Services

A service is an agent kept running: the desired state of its copies rather
than one copy started by hand. Where **Run** on an agent's page starts a
single [resident instance](instances.md) you talk to, **Deploy** creates a
service: which agent, in which workspace and [environment](environments.md),
how many replicas at least and at most, how many conversations each answers
at once, whether they take tasks, when an idle replica is stopped, a money
cap per turn and a version pin. The supervisor keeps resident instances
matching it; those instances are the service's replicas and carry its id.

A service can also be a **runner**: a service with no agent. Its replicas
are the same process bound to no agent, and they answer any agent's chat
turn, and a flow's or a team's, in a process of their own. This is where the
chat goes, and it is why the backend process runs no agent.

## Where a chat turn runs

With `AGENTS_HUB_CHAT_EXECUTION=instances` (the default; also on the
Settings page as "Chat execution") every turn of the Chat page, of
`/v1/chat/completions` with an agent model, of an embedded widget and of a
Telegram binding is handed to a service replica:

1. The backend picks the service: the agent's own service in the workspace
   (the one matching the workspace's default environment first, else one
   with no environment recorded), or, when the agent has none, the
   workspace's runner. A runner is created on first use with the defaults
   below; the default workspace's runner is created at startup, so its warm
   replica is up before the first message.
2. It picks the replica: the one already answering that conversation (two
   messages of one conversation are answered one after the other, never side
   by side), else the least loaded one with a free slot, else a new replica
   while the service is below its maximum, else the least loaded one, where
   the turn waits.
3. It subscribes to the conversation's channel, writes the turn into the
   replica's mailbox as a `turn` message (the whole chat request as JSON, who
   sent it, the service's money cap and version pin), and relays what the
   replica posts back until the turn's end marker. A replica that never
   picks the turn up within `AGENTS_HUB_TURN_START_TIMEOUT` seconds (default
   180, enough for a process to boot and build the agent), or one that dies
   before answering, ends the turn with an error the caller sees as a
   failed turn; a turn that produces nothing for the chat timeout is stopped.

The replica runs the very same pipeline the backend used to run: the prompt
and its context, attachments and references, compaction, the run record and
its log, handoffs, the transcript written into the stored chat, journaling
into shared memory, secrets, the workspace budget check. What the Chat page
receives is what it always received (`meta`, tokens, tool calls, `handoff`,
`done`); a mirror tab, the run's page and the replica's instance page follow
the same events, because the replica posts them to the backend
(`POST /api/instances/{id}/events`), which fans them out on the
conversation's channel and the instance's.
What a tool on the replica changes for a page that follows it live is
relayed the same way: a view op or a compute frame the agent produces in the
Studio (docs/views.md) goes to `POST /api/stream/publish` and from there to
the view's channel, so the scene builds up step by step in an open Studio
exactly as it did when the agent ran inside the backend.

The run record carries the replica (`instance_id`), the service
(`service_id`) and the conversation, and is a carrier run: if the replica
dies, the run is failed with it, as any carrier run is. The stop button and
steering work as before; both go through the run record.

`AGENTS_HUB_CHAT_EXECUTION=inprocess` turns this off everywhere and the turn
runs inside the backend, the way it did before services. A worker never
serves chat, and a replica always runs its turn itself, whatever the setting.

## The desired state

| Field | Meaning |
|---|---|
| `agent_id` | The agent; empty for a runner. |
| `workspace`, `environment_id` | Where the replicas run. The environment decides local process or container, variables, limits and network (docs/environments.md). |
| `replicas_min` | Replicas kept running. A replica that dies is replaced on the next tick. |
| `replicas_max` | Replicas at most. The service grows on demand up to it. |
| `concurrency` | Conversations one replica answers at once (its `concurrency` input). |
| `take_tasks` | The replicas also run tasks assigned to the agent in the workspace, when the workspace runs tasks on instances (`take_tasks` input). Never for a runner. |
| `idle_stop_seconds` | A replica beyond the minimum that was idle this long is stopped, least recently active first. 0 keeps every replica. |
| `budget_usd` | A money cap per turn, enforced during the turn like a task's own cap (docs/costs.md): every model call of the turn, and of the agents it delegates to, charges one ledger bound to the turn's context, so several capped turns in one replica never see each other's spend. A turn that reaches its cap ends as a failed turn whose reply names the cap (`error_code: "budget"` on the chat's `done`, the same on a job's result) and the service journals `budget_cap`. Recorded on the run as `budget_usd`. A runner's cap applies to every chat turn and job it answers. |
| `agent_version` | The stored version the replicas build the agent from (docs/agents.md, "Versions"). Empty is the live definition. |
| `status` | `active` or `paused`. A paused service has no replica and takes no turn; a turn for its agent is refused rather than sent to the runner. |

A runner is created with `AGENTS_HUB_RUNNER_MIN` (default 1, the warm
reserve), `AGENTS_HUB_RUNNER_MAX` (4), `AGENTS_HUB_RUNNER_CONCURRENCY` (8)
and `AGENTS_HUB_RUNNER_IDLE_SECONDS` (600). Those are read when the runner
is created; its page changes it after that like any service.

A service replica does not count against the workspace's per-agent instance
capacity: the service's own maximum is the cap.

## The supervisor

`services/supervisor.py` runs on the backend (role `all` or `api`), on the
`services` lease when there are several replicas of the backend, every
`AGENTS_HUB_SERVICES_TICK_SECONDS` (default 10). Each tick, per service:

- a paused service has every replica stopped;
- an active one is brought up to its minimum, and replicas beyond the
  minimum that have been idle for `idle_stop_seconds` are stopped; when the
  maximum was lowered below what runs, idle replicas beyond it are stopped;
- a service whose replicas keep dying (three failures or crashes within five
  minutes) is paused with the reason `crash loop` rather than restarted
  forever; fix the cause, then resume it;
- replicas of a service that no longer exists are stopped.

Everything it does is journaled (`GET /api/services/{id}/events`, the Events
tab): replicas started and why, stopped and why, crashed, the service
paused or resumed, published, changed.

## Replicas

A replica is a resident instance: it has its own page, conversations,
process and logs, and can be interrupted, restarted or stopped there. It
shows the service it belongs to, and the Instances list filters by service
(`?service_id=`). Stopping a replica by hand is fine: the supervisor starts
another one if the service is then below its minimum.

A service conversation has one history across its replicas: a run carries
`service_id` and `conversation_id`, and the next message of the conversation
is answered with every earlier turn of it, whichever replica answered them.

## Writing to one directly

Beyond the chat, a service can be written to directly:

```
POST /api/services/{id}/message
{"message": "review PR 42", "conversation_id": "ci"}
```

The message goes to a replica's mailbox and is answered with the service
conversation's history; `GET /api/services/{id}/messages/{msg_id}` reads
the answer, `GET /api/services/{id}/conversations` lists the conversations.

A runner answers for the agent the message names: add `"agent_id"` to the
body (an agent usable in the workspace, else `400` or `404`). The message
becomes a chat turn of that agent, with the conversation's earlier turns as
history, filed under the same conversation, so the reply is read the same
way. A runner replica's own page takes the same `agent_id`, with a picker
next to its message box.

**Publish** gives the service a public address through the hub, exactly like
a published instance (docs/instances.md, "Publishing"): a token, an optional
inbound secret, a connection history on its Access tab, and
`POST /api/external/{token}/messages` answered by whichever replica the
routing picks, with `wait_seconds`, `stream` and the poll address as for an
instance. A published runner takes `"agent_id"` in every call.

## Jobs

The chat is not the only agent execution the backend hands over. Whatever
used to run an agent inside the backend process now writes a `job` into a
runner's mailbox and reads the result back (`services/jobs.py`,
`runtime/jobs.py`): an eval case, a replay, a task decomposition, a project
graph, a playground world or scenario, and the agent part of every entity
chat (an agent's definition chat, a flow's planner, the page chat, the
project planner). An `invoke` job is one prompt through one agent, recorded
as a run of the replica when the caller keeps a run record (an eval or replay
run); an `entity_turn` job streams the agent's events back on
`entity:<run_id>` for the page that shows them. The runner's money cap
applies to jobs too. With chat execution on `inprocess` every one of these
runs in the backend as before.

## API and CLI

```
GET    /api/services?workspace=            list (runners included)
POST   /api/services                       deploy: {agent_id?, workspace, environment_id?, name?,
                                           replicas_min, replicas_max, concurrency, take_tasks,
                                           idle_stop_seconds, budget_usd?, agent_version?, publish}
GET    /api/services/{id}
PATCH  /api/services/{id}                  change the desired state
POST   /api/services/{id}/pause | resume
DELETE /api/services/{id}                  stop the replicas, keep their runs
GET    /api/services/{id}/replicas
POST   /api/services/{id}/replicas         one more replica now, up to the maximum
GET    /api/services/{id}/events
POST   /api/services/{id}/publish, DELETE  the public address
PUT    /api/services/{id}/inbound-secret, DELETE
GET    /api/services/{id}/connections
```

`ah service list | create <agent> --min 1 --max 3 | show | scale | pause |
resume | delete | publish` does the same from a terminal; `ah service
create` without an agent creates a runner. The cluster map
(`GET /api/cluster`, the Cluster page) lists every service with its live
replicas.

Related: [instances](instances.md), [chat](chat.md),
[hub-as-provider](hub-as-provider.md), [environments](environments.md),
[deployment](deployment.md).
