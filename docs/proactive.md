# Proactive agents

An agent with a pulse of its own: it wakes up on a schedule, looks at its
sources, decides whether there is anything to do, acts within a budget, writes
to you only when it has something to say, and remembers between wakings what
it has already seen.

## What a tick is

A tick is an ordinary scheduled job of kind `heartbeat` on the [Plan
page](scheduling.md), owned by the agent's proactive profile rather than
created by hand. When it fires, the scheduler first runs the tick through its
gates (quiet hours, the day's budget and tick count, whether the previous tick
is still running) and then creates a task of the agent, exactly as an
`agent_task` job would. The task's text is the tick's prompt: the brief, what
the previous tick left behind, the day's allowance, the rules, and the answer
format. So a tick shows up among the [tasks](tasks.md) and the runs, is priced
like any run on the [Costs page](costs.md), and obeys the task's
[environment](environments.md), its money cap and the capability guard.

A tick that a gate stops is not a failure. It is written to the firing journal
with its reason as the outcome (`quiet_hours`, `budget`, `rate`, `busy`,
`disabled`) and the pulse carries on. "Nothing to do" is a normal result, and
it makes no noise: no inbox entry, no Telegram message.

## The profile

The profile lives on the agent record (`proactive`, `agents/registry.py`), next
to the handoff and loop settings, and is edited on the **Pulse** card of the
agent page's **Pulse** tab or through `PUT /api/agents/{id}/proactive`. System
agents ship without one; the operator switches it on. Its fields:

| Field | Meaning |
|---|---|
| `enabled` | The pulse is on. Switching it on creates the heartbeat job; switching it off cancels the job but keeps its id, so the tick feed survives an off-and-on. |
| `interval_minutes`, `cron`, `timezone` | The schedule. A cron expression wins when given; an interval must divide an hour or a day evenly (5, 10, 15, 20, 30 minutes; 1, 2, 3, 4, 6, 8, 12 or 24 hours). |
| `quiet_hours` | `{"from": "22:00", "to": "07:00"}` in the profile's timezone. May cross midnight. |
| `daily_budget_usd`, `max_runs_per_day` | Caps on one calendar day of the profile's timezone. `0` means none. |
| `tick_budget_usd`, `environment_id` | Copied onto every tick's task: the per-run cap and the environment. |
| `brief` | The standing instruction: what to watch, and when acting is warranted. |
| `notify` | Where an acted tick is delivered: `dashboard` (the inbox), `telegram`, `slack`, `webhook`. |
| `workspace` | The workspace the ticks run in; the agent's own by default. |
| `auto_pause_after` | Failed runs in a row that pause the pulse. `0` turns it off. |
| `triggers` | Event sources that wake the agent besides the clock; see [Triggers](#triggers-besides-the-clock). |

Validation happens on save: a bad cron expression, an unknown timezone, a
half-filled quiet window or a negative budget is refused with a message naming
the field, not stored.

## Schedule and quiet hours

The interval is turned into a cron expression (`*/15 * * * *`, `0 */3 * * *`,
`0 0 * * *`) and the job carries it with the profile's timezone, so everything
the [scheduler](scheduling.md) does for cron jobs applies: leases, the
once-per-slot guarantee, the next-occurrence rollforward after a backend
outage.

A tick due inside the quiet window does not start. The job's next run is moved
to the window's end rather than to the next cron slot, so a pulse quiet from
22:00 to 07:00 ticks once at 07:00 and then resumes its interval. **Wake now**
on the Pulse card passes the quiet hours, since a person asked, but still obeys
the budget, the tick limit and the busy gate.

## Budgets and limits

Each tick that starts a task is priced when its run finishes
(`common.pricing.run_cost_usd` over the task's runs) and the cost is written
onto its journal row. The day's spend is the sum of those rows over the
profile's local calendar day, and the day's tick count is the number of rows
that started a task. A tick that would exceed either is skipped with outcome
`budget` or `rate`. A tick still running counts as a run and as zero spend
until it finishes.

One tick at a time: while the previous tick's task is still active (queued,
running, parked on a question or an approval), the next one is skipped with
outcome `busy`.

A run that fails feeds the job's consecutive-error counter, the same one the
scheduler's [auto pause](scheduling.md) uses; after `auto_pause_after`
failures in a row the pulse pauses itself with `paused_reason: "errors"` and
an inbox entry says so. A successful run resets the counter. An agent that no
longer exists pauses the pulse on the first firing, as it does any job.

## The tick's answer

Every tick must end with a single JSON object:

```json
{"outcome": "acted | quiet | blocked", "summary": "...", "next_check": "..."}
```

The schema is set on the tick's run alone (the launcher's `output_schema`
parameter, folded into the agent's spec at build time), never on the agent
record, so the agent's ordinary runs keep their own answer format. The loop's
structured-output extension validates and repairs it like any
[output schema](agent-loop.md).

- `acted`: the agent did or said something. The summary is what the person
  reads, delivered to the profile's channels.
- `quiet`: nothing to do. Recorded, never delivered.
- `blocked`: the agent needs a decision or a permission it lacks. Delivered
  once; a following tick blocked on the same summary is recorded but not
  delivered again, until the reason changes.

`next_check` is what the agent asks itself to look at next time. The next
tick's prompt carries it, together with the previous outcome and summary and
how long ago that was, so a tick never starts from nothing. A failed run is
recorded as `error` with the run's error as its summary.

## Triggers besides the clock

A profile's `triggers` list names the events that wake the agent without
waiting for the schedule. One door, `proactive.service.wake_agent`, and six
sources that knock on it (`proactive/events.py`):

| Kind | What wakes the agent | Filter |
|---|---|---|
| `webhook` | A signed `POST /api/webhooks/agents/{id}/wake` | `name`: only requests carrying that name |
| `file` | A workspace file added, uploaded or rewritten | `pattern` (a glob on the path or name, `*.md`), `source` (`upload`, `agent`, `local`) |
| `task` | A task of the workspace changed status | `statuses`: the new statuses that count; empty means any |
| `eval` | An eval run finished with at least one failing cell | `eval_set_id` |
| `telegram` | An unanswered message on Telegram | none |
| `slack` | An unanswered message on Slack | none |
| `discord` | An unanswered message on Discord | none |
| `teams` | An unanswered message on Microsoft Teams | none |
| `mail` | An unanswered message on mail | none |
| `agent` | Another agent called the `wake_agent` tool | `from`: the agent ids allowed to; empty means any |
| `watch` | A [watcher](watchers.md) noticed a change in what it observes (new mail, a changed web resource) | `watcher_id`, picked from the workspace's watchers |

An event is a small record (`kind`, a one-line `summary`, when, a few
details) appended to the heartbeat job's `pending_events`, and the job's next
run is pulled to now plus the batching window
(`AGENTS_HUB_HEARTBEAT_EVENT_WINDOW_SECONDS`, 30 by default) unless it is
already sooner. Ten file saves within the window become one tick whose prompt
lists ten events under "What woke you"; the tick that starts takes every
pending event and clears the list. At most 50 events wait, oldest dropped
first.

An event-driven tick goes through the same gates as a scheduled one. Inside
the quiet hours it waits for the window's end with its events kept; a pulse
whose previous tick is still running, or whose day's budget or tick count is
spent, keeps the events for its next slot. A paused pulse keeps them too and
ticks when it is resumed. An agent's own tick tasks never wake it through a
`task` trigger.

The webhook is signed with the workspace's inbound secret exactly like the
[task-filing webhook](notifications.md), and as fail-closed: no secret, no
wake. The body is `{"workspace", "summary", "name"?, "data"?}`; the agent's
pulse must run in that workspace and have a `webhook` trigger accepting the
`name`. The answer is `403` otherwise, `404` for an unknown agent, `409` for
a pulse that is off.

The `wake_agent(agent_id, message)` tool lets one agent nudge another's
pulse. It grants nothing for the capability guard: the message joins the
target's next tick, nothing comes back, and the target's `from` list decides
whom it listens to. **Wake now** on the Pulse card and the
`.../proactive/wake` route fire a tick immediately instead; they are for a
person, the tool and the webhook are for machines and other agents.

## Security

`webhook`, `telegram`, `slack`, `discord`, `teams`, `mail`, `file` and `watch`
triggers bring in text nobody here wrote. A profile with one of them is checked
by the [capability guard](tools-and-capabilities.md) on save like a tool list:
the trigger counts as `ingests_untrusted`, a delivery channel other than the
inbox counts as `can_exfiltrate` (the summary is agent-written), and together
with a tool that reads private data they form the trifecta the guard refuses in
`block` mode. `capability_override` on the agent lets it through, as it does for
tools, and `warn` mode logs instead of refusing.

For such a profile every tick also runs with the agent's outbound tools
(`notify_user`, `git_publish`, anything the capability model marks
`can_exfiltrate`) set to `always_ask` through the [tool
policy](tool-policy.md), for that run alone: the tick prepares the action,
the task parks on approval, and the person approves it from the inbox or the
task page. The agent record's own policy is not changed.

A tick's task inherits the job's per-run budget and environment; the day's
budget and the tick limit come from the profile. A profile that should never
reach the network gets an [environment](environments.md) with network `none`.

## Memory between ticks

After each tick the agent's primary [memory pool](memory.md), when it has one,
gets a core block named `heartbeat` rewritten with the last outcome, the
summary and the next check. Core blocks are rendered into the system prompt,
so the agent sees its own last state without a tool call, and it may edit the
block itself with the block tools during a tick. Personal memory follows the
usual per-workspace rules.

## Notifications

An acted tick creates one inbox notification titled with the agent's name and
the first line of its summary, with the summary as the body, on every channel
the profile lists ([notifications](notifications.md) covers Telegram, Slack
and webhooks). A blocked tick does the same with a warning severity, once per
reason. A quiet or skipped tick creates nothing. The tick itself makes no
"task started" entry either; it only speaks when it has something to say.

Every acted tick is also recorded in the [audit log](audit.md) as
`agent.heartbeat.acted`, with the task id and the cost; enabling, disabling,
pausing, resuming and waking the pulse are recorded as `agent.proactive.*`.

## The Pulse tab

On the agent page, the Pulse tab. It shows the state (on, paused and why), the
schedule, the next tick, how many events are waiting, today's ticks and spend
against the limits, and the buttons **Pause**, **Resume** and **Wake now**.
The form edits the profile, including the triggers (kind plus its one
filter). Below it is the tick feed: every tick with its outcome, summary,
next check, cost and a link to its task. Runs of quiet and skipped ticks fold
into one row ("12 quiet ticks"), so a pulse that mostly finds nothing to do
stays readable. The job itself also appears on the Plan and
[Deployments](deployments.md) pages under the `heartbeat` kind.

The Dashboard shows a **Pulse** card once any pulse is on: how many agents
have one, and how many ticks of the last day acted, stayed quiet or were
skipped (`GET /api/proactive/summary?workspace=`).

## The journal over time

Every tick is one row of the scheduler's firing journal. A five-minute pulse
writes 288 of them a day, most of them quiet, so the daily maintenance sweep
folds a day's quiet and skipped rows older than
`AGENTS_HUB_HEARTBEAT_COMPACT_DAYS` (7 by default, `0` disables it) into one
row per day and outcome carrying their `count` and summed cost. Acted,
blocked and error rows are kept one by one. The feed counts a folded row for
what it stands for. Rows older than `AGENTS_HUB_PLAN_FIRES_RETENTION_DAYS`
are removed like any other firing.

## API

- `GET /api/agents/{id}/proactive` : `{profile, schedule, job, usage, ticks}`.
  `limit` caps the ticks (50 by default).
- `PUT /api/agents/{id}/proactive` : a partial profile; only the keys present
  change. Answers like the GET. `job_id` in the body is ignored.
- `POST /api/agents/{id}/proactive/pause`, `.../resume`, `.../wake`.
- `GET /api/proactive/summary?workspace=&hours=` : the Dashboard widget's
  numbers, per agent and in total.
- `POST /api/webhooks/agents/{id}/wake` : the signed wake, see
  [Triggers](#triggers-besides-the-clock).

## Gotchas

- An agent with no brief still ticks; it is told to check its usual sources
  and act only when clearly warranted. Write the brief.
- The daily budget is a sum over finished ticks. Two ticks cannot run at once,
  so the overshoot is at most one tick's cost; set `tick_budget_usd` to bound
  that.
- In node execution mode the tick's task is picked up by the agent's node and
  the run is built without the per-run schema. The prompt still asks for the
  JSON answer, and an answer that is not JSON is read as `acted` with the
  text as its summary.
- Disabling the pulse cancels the job; the Plan page shows it as cancelled
  until the pulse is switched on again, which revives the same job.
- A wake does not fire a tick by itself. It moves the next run to within the
  batching window, and the scheduler's tick (every 20 seconds) starts it, so
  an event-driven tick begins up to a minute after the event.
- The demo workspace's `demo_support` ships with a pulse: weekdays at 09:00
  Berlin time, woken early when `supplier-questions.md` is re-uploaded, with
  a seeded tick history ([demo](demo.md)).

Related: [scheduling](scheduling.md), [agents](agents.md), [memory](memory.md), [notifications](notifications.md), [costs](costs.md), [environments](environments.md), [deployments](deployments.md), [tasks](tasks.md), [agent-loop](agent-loop.md), [audit](audit.md), [tools-and-capabilities](tools-and-capabilities.md), [tool-policy](tool-policy.md), [telegram](telegram.md), [files](files.md), [evals](evals.md), [demo](demo.md), [watchers](watchers.md).
