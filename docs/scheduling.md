# Scheduling and the Plan page

Future work: notifications, agent runs, agent tasks, flow and loop triggers and
memory consolidation, plus the inbox showing what fired.

## Job kinds

- **notification**: a reminder. Lands in the inbox and the bell, and in
  Telegram when asked.
- **agent_run**: runs an agent at the appointed time with no task. Each firing
  opens a new conversation with the agent (titled after the job, with the date
  for a recurring one) and sends it the job's message, recorded with
  `message_origin` "schedule"; the answer stays in that conversation and the
  bell links to it. The agent is required.
- **agent_task**: creates and starts a real task at the appointed time. With an
  agent named, that agent runs it; without, the orchestrator routes it. The
  task is graded against the agent's default criterion only when the job's
  `use_agent_outcome` is on (off by default, see [outcomes](outcomes.md)).
- **flow**: triggers a flow run, with an optional JSON seed merged into its
  initial state and a concurrency cap so a recurring flow job cannot stack up
  runaway instances.
- **loop**: starts a run of a loop. A firing is skipped, and recorded as an
  error, while a run of the same loop is already active.
- **heartbeat**: one tick of a [proactive agent](proactive.md). Owned by the
  agent's profile, never created by hand: the firing checks the agent's quiet
  hours, daily budget and tick limit first, and the task's structured answer
  is written back onto the journal row as its outcome.

`agent_task`, `flow` and `loop` jobs may also carry an `environment_id` (see
[environments](environments.md)) and a `budget_usd`, both copied onto every
task the job creates. These three kinds, shown as production deployments
rather than reminders, get their own page: see
[deployments](deployments.md).

## Recurrence

`none`, `hourly`, `daily`, `weekly` or `cron`. A recurring job re-arms itself
after each firing.

`cron` jobs carry a standard 5-field cron expression (`cron`) and a timezone
(`timezone`, an IANA name such as `Europe/Berlin`, default UTC). By default
(`catch_up: false`), the next run is the next cron occurrence after now, so a
backend that was down for a while rolls forward to the next future slot
instead of replaying every tick it missed. A job created with `catch_up: true`
instead advances one occurrence per firing from the slot it just fired,
even when that slot is still in the past: such a job is due again immediately,
so the scheduler's next tick fires it again, and a long outage is worked
through one missed occurrence at a time rather than jumped over. Either way a
single firing only ever runs the job once.

`hourly`, `daily` and `weekly` jobs also carry a timezone. The interval is
applied to the job's local wall-clock time, not the UTC instant, so a daily
job stays at the same local hour across a daylight-saving change: the real
elapsed time between firings shifts by an hour instead of the local time
drifting.

## Scheduling from a conversation

Ask in the chat: "remind me tomorrow to review the report", "every morning
generate a summary". The main agent does not schedule anything itself: it hands
the request to the Planner (`@planner`), the system agent that owns the Plan and
Deployments pages, and reports back what was scheduled. You can also talk to the
Planner directly. It confirms the resolved time back to you, because relative
times are where this goes wrong.

The Planner's tools:

| Tool | Does |
| --- | --- |
| `schedule_notification` | A reminder in your notification inbox. |
| `schedule_agent_run` | An agent run with no task: a new conversation with the agent each time the job fires. |
| `schedule_task` | Agent work as a tracker task, created and started when the job fires, with the deployment fields below and `use_agent_outcome`. |
| `schedule_flow` | A flow run, with a seed for its state and a cap on runs going at once. |
| `schedule_loop` | A loop run; a firing while the previous run is still going is skipped. |
| `schedule_memory_consolidation` | Folds a memory pool and its recent sessions into a new pool. |
| `list_scheduled`, `get_scheduled` | The jobs of the workspace; one job with its next runs and its firing journal. |
| `update_scheduled`, `cancel_scheduled` | Change a pending job, or end it. |
| `pause_scheduled`, `resume_scheduled` | Stop a job and start it again; resuming clears its error count. |
| `run_scheduled_now` | Fires a job once through the hub, like the Run now button. |

Any other agent can be given these tools on its page; only the Planner has them
out of the box. The Planner holds no task tools: tracker tasks are the Task
Manager's (`@task_manager`), so "which jobs do we have" is always answered from
the Plan.

## The scheduler itself

A background service, started with the backend. If jobs stop firing, check that
it is actually running — the health snapshot reports it directly. See
[service-health](service-health.md).

## Lease and idempotency

Two backend replicas, or a tick that overlaps a slow previous fire, could
otherwise fire the same job twice. Each job carries a lease (`lease_owner`,
`lease_until`): a tick claims due jobs atomically, under the store's file lock,
and only a job whose lease is absent or expired can be claimed. The claim
itself stamps the lease, so a second replica reading the same file a moment
later sees it already held and skips the job.

Firing also records `last_fired_slot`, the `run_at` a firing is for, before
running the side effect (creating the notification, task or flow). If the
process crashes between that side effect and clearing the lease, the lease
eventually expires and the job is retried, but the retry sees `last_fired_slot`
already matches the slot and skips the side effect, only finishing the
bookkeeping (clearing the lease, advancing `run_at`, counting the fire in
`fire_count`). A job fires at most once per slot even across a crash.

Calling `fire_job` directly on an unclaimed job (a tool, an admin action, a
run-now click) claims it first, so callers never need to do the two-step
claim-then-fire dance by hand; a job whose lease is already held elsewhere
comes back as locked instead of firing a second time.

## The firing journal

Every firing attempt, including a skipped duplicate slot, writes one record:
when, which slot, whether it was the automatic tick or a manual run-now
(`trigger`), whether it succeeded, and on failure an error type and message.
Error types are `agent_missing`, `flow_missing`, `loop_missing`, `loop_busy`,
`budget_exceeded`, `capacity`, `workspace_missing` and `other`, classified
conservatively: an error matching nothing known lands in `other` rather than
guessed at. `GET /api/plan/jobs/{id}/fires` reads one job's journal,
`GET /api/plan/fires` reads across all of them; both take `only_errors` and
`limit`. Rows older than `AGENTS_HUB_PLAN_FIRES_RETENTION_DAYS` (default 90,
`0` disables it) are pruned by the daily maintenance sweep. The
[Deployments page](deployments.md) is where this journal is actually read,
with an only-failures filter and a link from each row to the task it created.

A firing's `ok` says only that the work was started. The one-job journal route and
the Planner's `get_scheduled` add each firing's `result`
(`plans.service.fire_result`): where the task, flow run, loop run or
conversation it started stands now. `state` is `running`, `waiting` (on a
person: a blocked task, a question), `completed`, `failed`, `stopped` or
`missing` (deleted since), with `status` as the record spells it and `result`
quoting the start of what it produced. The record keeps `flow_run_id` and
`conversation_id` for this; flow firings recorded before them have no result.

## Auto pause

A recurring job that keeps failing pauses itself: `auto_pause_after`
consecutive failures (default 3, `0` turns it off) pauses the job with
`paused_reason: "errors"`, and a failure classified as
`agent_missing`/`flow_missing`/`loop_missing` pauses it immediately regardless
of the counter, with `paused_reason: "target_missing"`: a target that no
longer exists will not start existing again on its own. A success resets the
counter; resuming a job clears both the reason and the counter. `run_at`
still advances on a firing that auto-pauses, so resuming waits for the next
natural slot instead of re-firing a stale one immediately. Run-now works on a
paused job too, recorded with `trigger: "manual"`, so a fix can be retested
without resuming first. Full detail: [deployments](deployments.md).

## Gotchas

- A scheduled task is created at fire time, not now. Editing the job changes
  what will be created, not a task that already exists.
- Times are stored in UTC. The agent should tell you the resolved time; if it
  does not, ask.
- Notification delivery counts as an outbound channel for capability purposes.
- A bad cron expression or an unknown timezone is rejected at create or update
  time with a clear error, not silently accepted.
- While you type a schedule, the Deployments and Plan job forms and the
  proactive heartbeat card show the next few fire times under the field.
  They come from `GET /api/plan/cron/preview` (`cron`, `timezone`, `count`,
  and for hourly, daily or weekly `recurrence` with `start`), which runs the
  scheduler's own next run logic, so the hint and the job never disagree. A
  saved recurring job carries the same list as `upcoming_runs_at`.

Related: [tasks](tasks.md), [telegram](telegram.md), [deployments](deployments.md), [environments](environments.md), [proactive](proactive.md).
