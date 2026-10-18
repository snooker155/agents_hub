# Costs

Token and estimated USD spend, broken down by workspace, agent, model or
project, with container hours priced on top and every counted run listed.

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
at its own model. A call a [fallback model](agent-loop.md) answered is priced at the
fallback's rate, not the agent's.

## Container hours

A docker-mode run also spends container time, not just tokens. The hours its
container lived (`started_at` to `finished_at`) are priced per vCPU-hour
against its effective `cpus` (`0.05` USD/vCPU-h by default, so a plain 2-cpu
run costs `0.10` USD an hour). This is a live setting
(`AGENTS_HUB_CONTAINER_HOUR_PER_CPU` in `.env`), not a catalog entry, resolved
the same way other container defaults are. The line is added to the run's
cost (`common.pricing.container_cost_usd`) on top of its tokens and the model
calls made beside its own loop above, and the run page shows it next to the
run's container profile when one applies. Zero for a local-mode run, or a
docker-mode run missing either timestamp (recorded before this was tracked).

The [outcome](outcomes.md) grader is different: it grades a whole attempt after it
ended, so each grading is a run of its own (agent `outcome_grader`, channel `outcome`),
tied to the task and to the work it graded, and added to that work's total when it is
a team, loop or scenario run. Every run in a scenario, a flow or a team keeps its own
cost; the total of such a run is the sum of its runs, plus its gradings.

## Attribution

Every run record carries `project_id` when the run's task belongs to a
project. It is stamped once, at creation, by the two places every run record
is actually written (`managers/runs/store.py` for an agent run,
`common/entity_runs.py` for the top-level run of a flow, loop, team or
scenario), and an update to an existing run never overwrites it. A run
created before this shipped carries no project and shows as "(unknown)", not
as a blank row.

A node a flow or a team runs, a delegated subtask and a grading made inside a
run are charged to the same project as the run that made them. The totals
sum these leaf runs one by one; an entity run's own total is their sum and is
never added on top.

## Runs

The Costs page has two tabs. **Overview** holds the totals and the breakdowns. **Runs** (`/costs?tab=runs`) lists every run those numbers
count, one row each with its agent, model, tokens and estimated cost. Its
filter bar holds the same scope and date range as the Overview (moved out of
the page header while the tab is open), so the unfiltered rows add up to the
totals, plus an agent and a status filter of its own. Rows load in batches
with a "Load more" button and sort by start time, cost, tokens, agent or
model. A "to" date includes that whole day, on both tabs. A run whose model
the catalog has no price for shows "no price" rather than $0. Each row opens
the run, whose page shows the same estimate next to its token counts.

The list comes from `GET /api/costs/runs?workspace=&since=&until=&agent_id=&status=&sort=&order=&limit=&offset=`
(`common/costs_report.py` `costs_runs`).

## What actually costs money

In rough order of surprise:

- **Scenarios**: roles times ticks. A 5-role, 50-tick run is 250 model calls.
- **Loops**: a whole flow, repeated until a judge is satisfied or a bound stops
  it. Unbounded loops are the classic way to spend a lot by accident.
- **Teams**: members times rounds, plus everyone reading everyone.
- **Flows**: one call per agent node, once.
- **Chat**: one call per turn, plus any delegation.
- **Voice**: the [assistant](assistant.md#voice) pays its workspace's
  transcription price per recording and its speech price per 1000 characters
  read aloud. A recording is a `voice` run (Messages lists it as "Voice
  input"); speech is added to the turn's run.

This is why the tools that start the first four refuse until you have approved
them, and why the refusal carries the estimate: the number arrives before the
decision, not after.

## Reducing it

- Set loop bounds and scenario tick limits before the first run.
- Use a smaller model per agent where the work is mechanical; the resolution
  order makes that a one-field change.
- Fewer team members. Three focused beats eight watching.

Related: [models](models.md), [loops](loops.md), [playground](playground.md), [tasks](tasks.md).
