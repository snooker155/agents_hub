# Outcomes

A task can have an outcome: a markdown rubric that defines done, with max attempts, an optional grader model and an optional pass threshold. After every completed agent run on the task, an independent model grades the result and decides whether to retry or pass.

## The rubric format

A markdown rubric is a list of criteria, each a bullet point:

```markdown
* **Accuracy**: factual claims cite evidence
* **Completeness**: covers the four requested areas
* **Clarity**: jargon-free for the target audience
```

Each top-level bullet is one criterion. If the rubric has no bullets, its headings are used as criteria. A rubric with neither becomes one criterion named "Overall" (at most 30 criteria total).

## Grading

After every completed run of the task, whether an agent, a flow, a team, a loop or a scenario worked it, an independent model grades the result criterion by criterion (orchestrator, code_reviewer and decomposer runs are skipped). For an agent run the result is the run's final answer and the grader also sees a short list of its tool calls; for the other executors it is the result they stored on the task. The grader never sees the agent's conversation. Each criterion gets passed or not, a score from 0 to 1, and feedback.

The outcome passes when every criterion passes, or when the mean score reaches the `threshold` if one is set (0 to 1). If it passes, the task resolves as usual. If not and attempts remain, the same agent or executor starts again. An agent and a flow read a "Definition of done" section plus "Outcome review of your previous attempt" with the grader's feedback per criterion in their instruction; a team or a loop gets the same text in the goal it starts from. Whatever waits on the task (an orchestrator continuation) waits for the last attempt.

Once attempts are used up, the task is blocked with the unmet criteria and a dashboard notification is sent.

## Attempts

`max_iterations` is between 1 and 10, default 3. Every grading after a completed run uses one attempt; a Grade now grading does not. An unreadable grade counts as a failed attempt, and two grader failures in a row block the task.

## Choosing a grader model

The grader model is the outcome's `grader`, else `AGENTS_HUB_OUTCOME_GRADER_MODEL`, else the workspace's default model, else the global default. The same model grades every criterion on every iteration.

## Grade now

`POST /api/tasks/{id}/outcome/grade` grades the latest completed run without relaunching or changing the status, and does not use up an attempt. Use this to experiment with a rubric or grader without retriggering work.

## What the agent is told

When a retry happens, the agent's new instruction includes its prior attempts' feedback, structured under "Outcome review of your previous attempt" with criterion names and the grader's feedback. The agent adjusts its approach and tries again.

## Blocking

When attempts run out with unmet criteria, the task is blocked with a reason that lists each unmet criterion and its feedback, an `outcome_limit` entry goes to the task's activity log, and a dashboard notification is sent. To try again, raise the attempts or edit the rubric and restart the task: editing keeps the gradings, so attempts already used still count. Removing the outcome clears its gradings.

## API and audit

`GET/PUT/DELETE /api/tasks/{id}/outcome` manage the rubric and settings. `GET /api/tasks/{id}/outcome/evaluations` lists all grading results with iteration, score, per-criterion feedback, grader model, tokens and cost. The task create and update routes accept `outcome` with the same validation.

Writes are audited as `task.outcome.set`, `task.outcome.delete`, `task.outcome.grade`.

The same grader is available in [eval sets](evals.md) as the grader kind `rubric`; it calls a model, so it costs tokens.

## In loops

Loops take an optional `rubric` and `grader` (UI on the Loops page, and the Loop Creator's `create_loop_tool` / `modify_loop_tool`). With a rubric, each pass is graded per criterion instead of by the loop's evaluator. The loop stops when the rubric passes, the loop's `target_score` divided by 100 is the pass threshold on the mean score, and the unmet criteria with their feedback are what the next pass is told to fix.

## Cost

The grader's tokens are added to a run record as `loop.aux_calls`, so they show on the [Costs](costs.md) page and count toward the task's money cap from the next run on: the graded run for an agent, the team's own run for a team, the task's latest run (a flow's or a loop's last node) for a flow or a loop, and for a scenario the run of the last role turn of its last tick (every role's turn is a run, whether a persona on a model or a real agent plays it).

Related: [loops](loops.md), [evals](evals.md), [tasks](tasks.md).
