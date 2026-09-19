# Scheduling and the Plan page

Future work: notifications, agent tasks and flow triggers, plus the inbox
showing what fired.

## Job kinds

- **notification** — a reminder. Lands in the inbox and the bell, and in
  Telegram when asked.
- **agent_task** — creates and starts a real task at the appointed time. With an
  agent named, that agent runs it; without, the orchestrator routes it.

## Recurrence

`none`, `hourly`, `daily` or `weekly`. A recurring job re-arms itself after each
firing.

## Scheduling from a conversation

Any agent with the schedule tools can do it: "remind me tomorrow to review the
report", "every morning generate a summary". The agent confirms the resolved
time back to you, because relative times are where this goes wrong.

## The scheduler itself

A background service, started with the backend. If jobs stop firing, check that
it is actually running — the health snapshot reports it directly. See
[service-health](service-health.md).

## Gotchas

- A scheduled task is created at fire time, not now. Editing the job changes
  what will be created, not a task that already exists.
- Times are stored in UTC. The agent should tell you the resolved time; if it
  does not, ask.
- Notification delivery counts as an outbound channel for capability purposes.

Related: [tasks](tasks.md), [telegram](telegram.md).
