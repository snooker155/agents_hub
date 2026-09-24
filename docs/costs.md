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
