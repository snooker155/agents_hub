# Notifications

The dashboard inbox (the bell icon) is where the hub tells you something
happened: a run failed, spend crossed a limit, a task needs your attention, an
agent called `notify_user`. Everything lands in the inbox; there is no other
delivery channel.

## The inbox

The bell in the header shows the unread count. Opening it lists the
notifications of the selected [workspace](workspaces.md), newest first, each
with a title, a body, a severity (`info`, `warning` or `error`) and a link to
the thing it is about. Mark one as read, or all at once. An agent raises a
notification with `notify_user` (right now) or `schedule_notification` (at a
time you give, as a job on the Plan page, see [scheduling](scheduling.md)).

## Alert rules

A rule raises a notification on its own, without an agent or a person asking
for one. Rules are evaluated whenever a run reaches a terminal status and are
managed per workspace on the Alert rules tab of the [Connectors](/connectors)
page, or through `/api/notify/rules`:

```json
{
  "id": "r1",
  "kind": "run_failed",
  "threshold_usd": 0,
  "agent_id": null,
  "channels": ["dashboard"],
  "enabled": true
}
```

Three kinds:

- **`run_failed`** fires when a run finishes `failed` or `error`. `agent_id`,
  if set, restricts it to that agent.
- **`spend_run_over`** fires when a single run's cost (the same pricing as the
  run page) meets or exceeds `threshold_usd`.
- **`spend_daily_over`** fires when the workspace's total spend for the
  current UTC day meets or exceeds `threshold_usd`. It fires at most once per
  day: the rule remembers the date it last fired and stays quiet until
  tomorrow, however many more runs finish today.

`channels` holds one value, `dashboard`: the inbox entry.

## Gotchas

- **The inbox is per workspace.** A rule only sees the runs of its own
  workspace and writes to that workspace's inbox.
- **A daily rule fires once.** If you raise the threshold after it fired, it
  stays quiet until the next UTC day.

Related: [connectors](connectors.md), [scheduling](scheduling.md), [tasks](tasks.md), [flows](flows.md).
