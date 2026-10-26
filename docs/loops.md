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

## The pages

`/loops` lists the loops with each one's last run (status, score, passes). A
loop has a page of its own at `/loops/{id}`, and each of its runs an address
at `/loops/{id}/runs/{run_id}`:

- the graph of the flow the loop repeats, the node that is working lit. The
  Graph button in the header folds it away and back.
- under it, **Run**, **Setup** and **Artifacts**. Run is drawn as the flow
  page's chat: what the run was asked for is the person's message (with what
  was attached to it), each pass is a reply with its score and verdict, its
  output (a few lines, the whole of it in a modal), the reviewer's comments,
  the rubric criteria and what was fed into the next pass, and the outcome
  closes the run, whole. The box at the foot asks for the next run (Enter or
  the round button starts it, the button stops a live one) and takes files and
  hub records as the flow chat's box does. Artifacts holds the files and views
  the passes' node runs produced, as on the flow page.
- on the left, **New run** over the run history, newest first, with Resume on
  a run that can be picked up again
- on the right, the scores across the run pinned on top and under them the
  work log of each pass (`GET /api/loops/runs/{id}/log`, one entry per
  iteration)
- the Loop Creator, opened from the header into a column at the right edge
  from 2xl, over the page on a narrower screen.

A run is not a task: starting one from the page or from the Loop Creator makes
no task. A loop works on a task only when it is given one: `task_id` on
`POST /api/loops/{id}/run`, or a task attached in the run box. What is attached
is rendered as it is for a chat turn and every pass works from the request
plus it (`context` on the run record, `attached` names it for the page).

## Checkpoint and resume

A loop runs as a process of its own, launched through the same envelope as a flow, a team and a scenario: `POST /api/loops/{id}/run` writes a `pending` record and spawns `runtime/loop_run.py` right there, or queues it for a worker in the `api` role, so a loop keeps running whatever happens to the backend that started it. The iterations run the flow engine inside that process. The run beats a heartbeat every 15 seconds and again as each flow node finishes.
After every iteration the run records its position (a checkpoint) with how many
passes are done, the last output, the reviewer's last verdict, the best score,
the patience counter and spend.

`POST /api/loops/runs/{id}/resume` relaunches a `stopped` or `failed` run from
its checkpoint (400 when there is nothing to resume). The watchdog resumes a run
whose heartbeat has gone quiet by itself, up to 2 times. Iterations already done
keep their rows and their scores: a resume continues the trajectory rather than
starting a new one.

A loop run on a task claims it as executor kind `loop`. Retry and review now apply:
the workspace's orchestrator retries a failed loop up to `orchestrator.max_retries`
times, and review starts on every resolve when the code_reviewer agent is available.

## Rubric

A loop can use an outcome rubric instead of the exit criterion and evaluator. Set an optional `rubric` and `grader` (UI on the Loops page, and the Loop Creator's `create_loop_tool` / `modify_loop_tool`). With a rubric, each pass is graded per criterion instead of by the evaluator. The loop stops when the rubric passes, the loop's `target_score` divided by 100 is the pass threshold on the mean score, and the unmet criteria with their feedback are what the next pass is told to fix. See [outcomes](outcomes.md).

## Gotchas

- The judge sees the result, not the conversation. If the criterion depends on
  context the flow did not produce, it cannot be judged.
- Feeding back "the reviewer's complaints" only helps if the flow's first node
  actually reads them.
- A resumed loop counts its ceilings from the position it restored, so the
  iteration cap and the cost ceiling still mean what they said. The wall clock
  restarts with the process: it bounds one sitting, not the whole run.

The [system workspace](system-workspace.md) ships one loop of its own,
`system_loop`, scheduled through a job of kind `loop` that starts paused.

Related: [flows](flows.md), [outcomes](outcomes.md), [costs](costs.md), [system-workspace](system-workspace.md).
