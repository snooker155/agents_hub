# Costs

Token and estimated USD spend, broken down by workspace, agent, model or
project, with budget caps that are enforced rather than advisory.

## Where the numbers come from

Every run records its token counts. Cost is those counts multiplied by the
model's price from the [models](models.md) catalog. An unpriced model contributes
tokens and zero cost, so check pricing before concluding something was free.

Input is counted in two parts, because the provider bills it in two parts. An
agent loop re-sends the whole conversation on every step, so after the first
call most of each prompt is the same prefix the provider already has: it serves
those tokens from its prompt cache and charges a fraction of the input rate for
them. Runs therefore record how many inbound tokens were cache reads, and those
are priced at the model's cached rate. Without that split a 37-step run was
quoted at roughly ten times what the provider actually charged.

Runs recorded before cache reads were tracked carry no cached count and are
priced as all-fresh input, which is exactly how they were always priced.

Evaluation channels (replays, eval runs) are tracked separately from production
spend.

Some model calls are made for a run beside its own loop: the [tool policy](tool-policy.md)
classifier, a [guardrail](guardrails.md) judge and the repair of an answer that did not
match its schema. Each is listed on the run's `loop.aux_calls` with its tokens, priced
at its own model and counted against the run's money cap while it runs. A call a
[fallback model](agent-loop.md) answered is priced at the fallback's rate, not the
agent's.

## Container hours

A docker-mode run also spends container time, not just tokens. The hours its
container lived (`started_at` to `finished_at`) are priced per hour against
the sandbox `size` its [environment](environments.md) names (`small`
`0.05`, `medium` `0.10`, `large` `0.20` USD/h by default), or, for a run with
no size, per vCPU-hour against its effective `cpus` (`0.05` USD/vCPU-h by
default, so a plain 2-cpu run prices the same as `medium`). These are live
settings (`AGENTS_HUB_CONTAINER_HOUR_SMALL`/`_MEDIUM`/`_LARGE`/`_PER_CPU` in
`.env`), not catalog entries, resolved the same way other container defaults
are. The line is added to the run's cost (`common.pricing.container_cost_usd`)
on top of its tokens and the model calls made beside its own loop above, and
the run page shows it next to the run's container profile when one applies.
Zero for a local-mode run, or a docker-mode run missing either timestamp
(recorded before this was tracked).

The [outcome](outcomes.md) grader is different: it grades a whole attempt after it
ended, so each grading is a run of its own (agent `outcome_grader`, channel `outcome`),
tied to the task and to the work it graded, and added to that work's total when it is
a team, loop or scenario run. Every run in a scenario, a flow or a team keeps its own
cost; the total of such a run is the sum of its runs, plus its gradings.

## Attribution

Every run record carries who it belongs to: `launched_by` (the acting user's
id), `key_id` when the request that started it presented a personal
[API key](api-keys.md), and `project_id` when the run's task belongs to a
project. These are stamped once, at creation, by the two places every run
record is actually written (`managers/runs/store.py` for an agent run,
`common/entity_runs.py` for the top-level run of a flow, loop, team or
scenario): the acting user comes off a contextvar the request guard binds for
the whole request (`common.identity.current_user_id`), the key off a sibling
one (`common.api_keys.current_key_id`), and the project off the run's task.
An update to an existing run never overwrites who first launched it.

A run created before this shipped carries neither field and reports as
"(unknown)" in the report below, not as a blank row.

A launched run's process has no request of its own, so the launcher hands the
user and the key down through its environment (`common/attribution.py`). A
node a flow or a team runs, a delegated subtask and a grading made inside that
process are charged to the same user and key as the run that made them. The
report and the key's monthly spend sum these leaf runs one by one; an entity
run's own total is their sum and is never added on top.

## Pricing on /v1

A call to the hub's own [OpenAI-compatible endpoint](hub-as-provider.md)
(`POST /v1/chat/completions`) is priced from the catalog the moment it is
recorded: `serving_usage.cost_usd` is set once, at write time
(`common.serving.record_usage`, `common.pricing.serving_cost_usd`), the same
per-1M-token prices the Costs page and the accounting report use elsewhere.
An unpriced model records the call at $0, the same fail-open rule the rest of
pricing follows. `common.serving.usage()` (the Models page's serving usage
card) sums it per model and overall.

## Money quota per key

A personal [API key](api-keys.md) can carry its own money cap,
`budget_usd_per_month` (`None` or `0` means none), set when the key is cut
and editable later from the Account page or `PUT /api/auth/keys/{id}`. Its
spend for the current UTC month is its own `/v1` serving cost plus the cost
of the runs it launched (`common.api_keys.key_month_spend_usd`), cached for
30 seconds so a busy key does not turn every request into two table scans.

Once a key's spend reaches its cap, further spend on it is refused with a 429
(the OpenAI error shape on `/v1`, `key_budget_exceeded`): a plain `/v1` call,
an agent answered through `/v1` and a task, flow, loop, team or scenario run
launched with that key at `agents.agent_launcher.prepare_run` /
`runtime.entity_launch.dispatch` before it starts. The cap resets with the
UTC month, not on a rolling 30 days.

## Report

`GET /api/accounting/report?group_by=key|user|project|workspace|agent|model`
(`routes/accounting.py`) combines runs and served `/v1` calls into one table:
one row per group with its run count, call count, tokens in/out/cached and
cost. Evaluation channels are excluded, the same rule the breakdowns above
follow. A served call carries no workspace, project or agent (it answers with
a model, not those), so it only ever contributes to the `key`, `user` and
`model` groupings; grouping by workspace, project or agent counts runs alone.
`format=csv` downloads the same rows as a file. A non-administrator in
`multi` mode sees only their own rows, whichever grouping they asked for: the
same rule the Models page's serving usage already applies.

From a terminal: `ah costs report --by user [--since ISO] [--until ISO]
[--workspace NAME] [--csv] [--out FILE]`.

The Costs page's own **Report** section offers the same grouping, date range
and CSV export.

## Budgets

A budget belongs to a workspace: a **hard limit**, a **soft limit** and a
**period** (`total`, `daily` or `monthly`, in UTC). A limit of `0` is off, which
is the default, so the feature is opt-in and existing workspaces keep behaving
as they did.

The hard limit is enforced at run launch, not merely displayed: when the
period's estimated spend already meets it, the run is refused rather than
started. By default, enforcement fails open: a pricing or lookup error means
no enforcement, never a wedged workspace.

A workspace can also turn on **Fail closed**. With it on, the same errors that
would otherwise let a run through unchecked instead refuse it: a pricing or
lookup failure while estimating spend, or a price catalog too thin to trust
(empty), raises the same kind of refusal as a confirmed over-cap reading. The
trade-off is the opposite of the default: a workspace that would rather lose a
run than risk one no cap could stop turns this on; one where availability
matters more than a stray unenforced run leaves it off (the default).

Evaluation-channel runs are excluded from the aggregation, so measuring your
agents never eats the budget your agents run on.

## Per-run limit

A workspace budget also carries a **per-run limit** (`run_limit_usd`, `0` is
off): a default money cap on a single task's runs, separate from the period
cap above. Where the hard limit protects the month, the per-run limit
protects one task from spending it all in a single sitting between one launch
check and the next.

A task's own `budget_usd` overrides the workspace default when it is set: `0`
marks the task deliberately uncapped even in a workspace with a default, and
`None` (unset) falls back to the workspace's `run_limit_usd`.

The cap travels with the run as three environment variables
(`common/run_budget.py`): the limit, what the task has already spent, and
the catalog's prices, so the running agent can price its own calls and stop
itself without needing database access. `RunBudgetGuard`
(`agents/callbacks/guards.py`) accumulates the cost of each finished LLM call
and raises once the cap is met, which pauses the task in
`awaiting_approval` with `pending_approval.kind: "budget"`, carrying
`spent_usd` and `limit_usd`. The task page's card offers **Continue** (raise
the cap and resume from where it stopped) or **Stop** (the task moves to
`blocked` with the reason "Stopped at budget cap"). `POST
/api/tasks/{id}/approve` takes `budget_usd` for the raised cap, which must
exceed what the task already spent. Spend for this purpose is the same
number the Costs page shows, summed over every run of the task including
resumes, evaluation channels excluded.

**This is enforced only for the built-in LangChain agent loop
(`StandardAgent`).** A CLI backend (Claude Code, Codex), a remote agent or an
imported agent is not stopped mid-run when it crosses the cap. Its spend is
still counted toward the task's total and the cap still applies to the
*next* launch of that task, but nothing interrupts a run already in flight
for those backends.

The same workspace **Fail closed** flag that governs the period budget also
governs the per-run limit. Off (the default), a launch whose cap or spend so
far cannot be computed starts anyway with no cap, and `RunBudgetGuard` treats a
call through a model it cannot price as free. On, `launch_env` refuses the
launch instead of silently handing the run no cap, and the guard blocks a call
through an unpriced model before it runs rather than letting it pass as free,
since either one could otherwise let spend run past a cap that never sees it.

## What actually costs money

In rough order of surprise:

- **Scenarios** — roles times ticks. A 5-role, 50-tick run is 250 model calls.
- **Loops** — a whole flow, repeated until a judge is satisfied or a bound stops
  it. Unbounded loops are the classic way to spend a lot by accident.
- **Teams** — members times rounds, plus everyone reading everyone.
- **Flows** — one call per agent node, once.
- **Chat** — one call per turn, plus any delegation.

This is why the tools that start the first four refuse until you have approved
them, and why the refusal carries the estimate: the number arrives before the
decision, not after.

## Reducing it

- Set loop bounds and scenario tick limits before the first run.
- Use a smaller model per agent where the work is mechanical; the resolution
  order makes that a one-field change.
- Fewer team members. Three focused beats eight watching.

Related: [models](models.md), [loops](loops.md), [playground](playground.md), [tasks](tasks.md), [deployments](deployments.md).
