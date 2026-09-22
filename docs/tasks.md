# Tasks

A task is tracked work: it has a status, an owner, subtasks, dependencies, a
priority, an optional deadline, and a result that outlives the conversation
that produced it.

Use a task when the work matters beyond this turn. Use [chat](chat.md) when it
does not.

## Status

A task moves through twelve statuses: `todo`, `ready`, `pending`,
`in_progress`, `blocked`, `awaiting_input`, `awaiting_approval`, `stopped`,
`resolved`, `reviewing`, `reviewed`, `done`.

- **todo** — not started.
- **ready** — queued for the orchestrator or a worker node to pick up.
- **pending** — assigned but waiting on a manual "start" approval.
- **in_progress** — an agent is running.
- **blocked** — work cannot proceed; `blocked_reason` says why.
- **awaiting_input** — paused because the agent asked the user a question.
- **awaiting_approval** — paused in front of a tool call that needs a yes
  (see [hooks](hooks.md)).
- **stopped** — aborted or paused by the user.
- **resolved** — a worker finished; nothing has reviewed it yet.
- **reviewing** — a reviewer is running.
- **reviewed** — the reviewer recorded a verdict.
- **done** — finished.

Every status change is checked against a fixed transition table and against
who is asking for it (an **actor**): `user` (the dashboard), `agent` (an
assigned agent, through the `update_task`/`stop_task`/`block_task` tools), or
`system` (the run manager, the finalizer, the watchdog, and every internal
cascade). `system` may perform any transition in the table below; `user` is
further limited to the statuses in the "user" column; `agent` may only do the
small set of moves marked **bold** below. Anything else raises
`IllegalTransition` — a 400 from the API, a JSON error from the tool.

### Transition table

| From | To | Who |
|---|---|---|
| todo | ready | system, user, agent |
| todo | in_progress | system, user, **agent** |
| todo | blocked | system, user |
| todo | awaiting_input | system |
| todo | awaiting_approval | system |
| todo | stopped | system, user |
| todo | resolved | system, user |
| todo | reviewing | system |
| todo | done | system, user |
| ready | todo | system, user |
| ready | in_progress | system, user, **agent** |
| ready | blocked | system, user |
| ready | stopped | system, user |
| ready | resolved | system, user |
| ready | reviewing | system |
| ready | done | system, user |
| pending | todo | system, user |
| pending | ready | system, user |
| pending | in_progress | system, user |
| pending | blocked | system, user |
| pending | stopped | system, user |
| pending | resolved | system, user |
| pending | reviewing | system |
| in_progress | ready | system, user |
| in_progress | blocked | system, user, **agent** |
| in_progress | awaiting_input | system |
| in_progress | awaiting_approval | system |
| in_progress | stopped | system, user |
| in_progress | resolved | system, user, **agent** |
| in_progress | reviewing | system |
| in_progress | done | system, user |
| blocked | todo | system, user |
| blocked | ready | system, user |
| blocked | in_progress | system, user, **agent** |
| blocked | stopped | system, user |
| blocked | reviewing | system |
| awaiting_input | ready | system, user |
| awaiting_input | in_progress | system, user |
| awaiting_input | blocked | system, user |
| awaiting_input | stopped | system, user |
| awaiting_input | resolved | system, user |
| awaiting_input | reviewing | system |
| awaiting_approval | ready | system, user |
| awaiting_approval | in_progress | system, user |
| awaiting_approval | blocked | system, user |
| awaiting_approval | stopped | system, user |
| awaiting_approval | resolved | system, user |
| awaiting_approval | reviewing | system |
| stopped | todo | system, user |
| stopped | ready | system, user |
| stopped | in_progress | system, user |
| resolved | ready | system, user |
| resolved | in_progress | system, user |
| resolved | blocked | system, user |
| resolved | stopped | system, user |
| resolved | reviewing | system, agent (the reviewer) |
| resolved | done | system, user |
| reviewing | ready | system, user |
| reviewing | in_progress | system, user, agent (the reviewer) |
| reviewing | blocked | system, user |
| reviewing | stopped | system, user |
| reviewing | resolved | system, user |
| reviewing | reviewed | system, user, agent (the reviewer) |
| reviewed | ready | system, user |
| reviewed | in_progress | system, user |
| reviewed | blocked | system, user |
| reviewed | stopped | system, user |
| reviewed | reviewing | system |
| reviewed | done | system, user |
| done | todo | system, user |
| done | ready | system, user |
| done | in_progress | system, user |
| done | reviewing | system |

Most rows read "system, user" because reassigning an agent
(`POST /api/tasks/{id}/assign`, or the terminal client) is allowed from almost
any status — including a blocked, resolved, reviewed or even a done task — and
lands it on `ready`, `in_progress`, or `reviewing`. The "user" column mirrors
what the dashboard's Kanban board already enforces: dragging a card only ever
targets `todo`, `ready`, `in_progress`, `blocked`, `stopped`, `done`,
`reviewed` or `resolved` — the "waiting" and "reviewing" columns are not drop
targets, because those statuses are entered and left by the system, not by a
person picking a status. The bold rows are the only moves an **agent**
may make through its own tools: picking up ready work (`todo` → `ready`),
starting on work it was given (`todo`/`ready` → `in_progress`), finishing
(`in_progress` → `resolved`), blocking itself with a reason (`in_progress` →
`blocked`), and unblocking itself (`blocked` → `in_progress`). An agent's
`update_task` call can never set `done`, `reviewed`, `reviewing`, an
`awaiting_*` status, `stopped`, or `pending` — those are reserved for the
system and the person watching the task.

Source of truth: `tasks.models.TRANSITIONS`, `USER_TARGETS`,
`AGENT_TRANSITIONS`, and `tasks.service.update_task`'s `actor` parameter.

- **Assign** picks the agent. The orchestrator can do this for you, and its
  reasoning is recorded in the routing log.
- **Start** launches the run. Assignment alone does nothing.
- **Block** records why work cannot proceed, with a reason, rather than failing
  silently.
- **Dependencies** hold a task until the ones it names are done. A sequence is
  just dependencies laid out in a line.
- **Pausing** is not failing. A task waits in `awaiting_input` when its agent
  asked you a question, and in `awaiting_approval` when it stopped in front of a
  tool call that needs your yes (see [hooks](hooks.md)). Both are answered from
  the task page, and both resume the agent where it left off.

## Priority and deadlines

`priority` is one of `low`, `medium`, `high`, `critical` (default `medium`);
an unrecognized value is coerced to `medium` rather than rejected, since
priority is advisory, not a validation gate. `due_at` is an optional ISO 8601
deadline, timezone aware or assumed UTC; a task past its deadline reports
`overdue: true` in its API/tool representation, unless it has reached a
terminal-for-scheduling status (`done`, `reviewed`, `resolved`, `stopped`).

Both feed `tasks.service.order_for_dispatch`, the one helper the orchestrator
and worker polling loops (`runtime/node_run.py`) use to pick which of several
runnable tasks goes next: priority descending (critical first), then `due_at`
ascending with no-deadline tasks sorted last, then `created_at` ascending as
the final tie-breaker.

## Subtasks and decomposition

The **Decomposer** breaks a complex task into actionable subtasks, optionally in
a fixed order. It only acts on tasks a person created, so an agent cannot spawn
unbounded work by decomposing its own output.

## Results

`get_task_result` returns what the agent produced. That is the handoff point
between agents: a reviewer reads the developer's result rather than re-reading
the conversation.

## Gotchas

- A task with an agent assigned but never started sits forever. Assignment is
  not a launch.
- Taskless delegation (`run_agent_tool`) is refused inside a task context on
  purpose: tracked work goes through assign and start, so it stays visible.
- A task's workspace is fixed at creation and decides where its files land.
- An agent's `update_task` call is checked against the transition table above;
  a move the table does not grant to `agent` fails with `IllegalTransition`
  rather than silently changing the status to something else.

Related: [projects](projects.md), [agents](agents.md), [hooks](hooks.md), [sessions-and-runs](sessions-and-runs.md).
