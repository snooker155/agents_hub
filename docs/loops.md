# Loops

A loop wraps a [flow](flows.md) and re-runs it, feeding back a reviewer's
complaints, until an agent judges the result good enough.

## The exit criterion

Written as a sentence, not a predicate: *"every claim carries a source"*. That
is an opinion, so an agent scores the result against it after each pass. By
default the judge is the flow's own final node; you can nominate a different
agent.

## The bounds that stop it

A loop that will not converge must still end. Set some of:

- **max iterations** — the hard stop
- **target score** — good enough, numerically
- **patience** — stop after N passes with no improvement
- **cost ceiling** — stop when spend reaches a limit
- **wall clock** — stop after a duration

Set at least one you trust. A loop with a vague criterion and no bounds is the
most expensive object in the product: one iteration is a whole flow.

## Running

`run_loop_tool` refuses until approved, and the refusal carries the estimate.
The run goes to the background and returns its id; iterations appear on the page
as they complete. `get_loop_run_tool` reports progress, `stop_loop_run_tool`
ends it.

## If it is interrupted

A loop runs inside the backend, so a restart ends it mid-run. After every
iteration the run records its position: how many passes it has done, the last
output, the reviewer's last verdict, the best score, the patience counter and
what it has spent. `POST /api/loops/runs/{id}/resume` starts at the pass after
the last one that finished, with the feedback that pass was going to get. The
watchdog resumes a run whose heartbeat has gone quiet by itself, twice at most.
Iterations already done keep their rows and their scores: a resume continues the
trajectory rather than starting a new one.

## Gotchas

- The judge sees the result, not the conversation. If the criterion depends on
  context the flow did not produce, it cannot be judged.
- Feeding back "the reviewer's complaints" only helps if the flow's first node
  actually reads them.
- A resumed loop counts its ceilings from the position it restored, so the
  iteration cap and the cost ceiling still mean what they said. The wall clock
  restarts with the process: it bounds one sitting, not the whole run.

Related: [flows](flows.md), [costs](costs.md).
