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

## Budgets

A budget belongs to a workspace: a **hard limit**, a **soft limit** and a
**period** (`total`, `daily` or `monthly`, in UTC). A limit of `0` is off, which
is the default, so the feature is opt-in and existing workspaces keep behaving
as they did.

The hard limit is enforced at run launch, not merely displayed: when the
period's estimated spend already meets it, the run is refused rather than
started. Enforcement fails open: a pricing or lookup error means no enforcement,
never a wedged workspace.

Evaluation-channel runs are excluded from the aggregation, so measuring your
agents never eats the budget your agents run on.

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

Related: [models](models.md), [loops](loops.md), [playground](playground.md).
