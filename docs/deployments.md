# Deployments

The Deployments page (`/deployments`, under Workspace) is a working view over
the scheduled jobs that produce real work: `agent_task`, `flow` and `loop`
jobs from the [Plan page](scheduling.md), filtered so a reminder does not sit
in the same list as a recurring pipeline. It adds the two things a
production-shaped schedule needs and the Plan page keeps out of its way: a
per-run money cap and an environment, and a firing journal that answers "did
this actually run, and what happened".

## What a deployment is

A `ScheduledJob` of kind `agent_task`, `flow` or `loop`, the same records the
Plan page manages, listed here through `GET /api/plan/jobs?kinds=agent_task,flow,loop`.
Each row shows its target (the agent, flow or loop it fires), its schedule
(cron and timezone, or a recurrence interval, plus the next occurrence and a
tooltip with the following few), its environment, its per-run budget, status
with a paused-reason badge, consecutive error count, and the outcome of its
last firing.

Creating or editing one sets, on top of the usual schedule fields:

- **environment_id**: the [environment](environments.md) every task this job
  creates runs in. `None` leaves it to the task's own default resolution.
- **budget_usd**: the money cap copied onto every task the job creates (see
  [costs](costs.md)). `None` leaves the task uncapped by the job and subject
  only to the workspace's own default.
- **auto_pause_after**: how many consecutive firing failures pause the job
  automatically; `0` turns auto-pause off. Defaults to `3`.
- **agent_version** (agent_task jobs only): pin the job to a stored agent
  version. Requires `agent_id` and is copied onto the task at fire time. See
  [agents](agents.md).

For an `agent_task` job the environment and budget are passed straight into
`tasks_service.create_task` when the job fires. A `flow` or `loop` job creates
its task through that launcher's own path, so the two fields are applied to
the resulting task afterward, best-effort: a failure there never turns an
otherwise successful firing into a failed one.

## The firing journal

Every attempt to fire a job, not just the successful ones, writes one record:
when it happened, which slot (the `run_at` it was firing for) it was for,
whether it was the automatic tick or a manual run-now, whether it succeeded,
and, on failure, an error type and message. A slot already fired (a retry
after a crash) is recorded too, as a no-op skip, so the journal shows every
attempt rather than only the ones that did something.

Error types: `agent_missing`, `flow_missing`, `loop_missing`, `loop_busy`,
`budget_exceeded`, `capacity`, `workspace_missing`, `other`, plus
`skipped_slot` for the already-fired case. Classification is string based and
deliberately conservative: an error that matches nothing known lands in
`other` rather than a guess, since a wrong bucket could pause a job that would
have recovered, or fail to pause one that never will.

The detail drawer on a deployment shows its journal with an only-failures
filter (`GET /api/plan/jobs/{id}/fires?only_errors=`); each row links to the
task it created, when it created one. `GET /api/plan/fires` gives the same
journal across every job in a workspace, for a wider view.

Journal rows are retained for `AGENTS_HUB_PLAN_FIRES_RETENTION_DAYS` (default
90 days, `0` disables pruning); older rows are removed by the daily
maintenance sweep (`common/maintenance.py`).

## Auto pause

A recurring job that keeps failing pauses itself rather than firing into the
void forever:

- A run of `auto_pause_after` consecutive failures (default 3) pauses the job
  with `paused_reason: "errors"` and raises a notification.
- A failure classified as `agent_missing`, `flow_missing` or `loop_missing`
  pauses the job immediately, regardless of the counter, with
  `paused_reason: "target_missing"`: an agent, flow or loop that no longer
  exists is never going to start existing again on its own.
- A success resets the counter to zero.

`run_at` still advances on a firing that auto-pauses, so a later resume waits
for the next natural slot instead of immediately re-firing a stale one.
Manually pausing a job sets `paused_reason: "manual"`; resuming clears both
the reason and the error counter.

**Run now** fires a job immediately regardless of its schedule, recorded in
the journal with `trigger: "manual"`. It works on a paused job too (manual or
auto-paused), so a fix can be retested without resuming the job first.

## Gotchas

- Pausing does not clear the error count by itself; resuming does. A job
  resumed without addressing the failure can auto-pause again after the same
  number of firings.
- `agent_missing`/`flow_missing`/`loop_missing` pause on the *first* such
  failure, not after `auto_pause_after` attempts: there is nothing to wait
  for, since the target will not reappear between firings.
- The journal records every attempt, including the skipped-slot no-op. A long
  row of `skipped_slot` entries usually means a job's lease kept expiring
  before it finished, not that it is doing nothing.
- A one-off job (`recurrence: none`) is already terminal (`failed` or
  `fired`) after its single firing, so the error counter and auto-pause never
  apply to it.

Related: [scheduling](scheduling.md), [environments](environments.md), [costs](costs.md), [tasks](tasks.md).
