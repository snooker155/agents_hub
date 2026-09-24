# Scheduling and the Plan page

Future work: notifications, agent tasks and flow triggers, plus the inbox
showing what fired.

## Job kinds

- **notification** — a reminder. Lands in the inbox and the bell, and in
  Telegram when asked.
- **agent_task** — creates and starts a real task at the appointed time. With an
  agent named, that agent runs it; without, the orchestrator routes it.
- **flow**: triggers a flow run, with an optional JSON seed merged into its
  initial state and a concurrency cap so a recurring flow job cannot stack up
  runaway instances.
- **loop**: starts a run of a loop. A firing is skipped, and recorded as an
  error, while a run of the same loop is already active.

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

Any agent with the schedule tools can do it: "remind me tomorrow to review the
report", "every morning generate a summary". The agent confirms the resolved
time back to you, because relative times are where this goes wrong.

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

Related: [tasks](tasks.md), [telegram](telegram.md), [deployments](deployments.md), [environments](environments.md).
