# Evals

Eval sets, graders and model sweeps: measure whether a prompt change helped
instead of guessing.

## The pieces

- **An eval set** — the cases to run. A case is an input plus what a good answer
  looks like.
- **A grader** — how each result is scored.
- **A config**: one target under one provider and model. A sweep runs every
  case against every config, and each cell of the resulting matrix is a real
  run you can open.

## Targets

A config (and a set's default, the baseline column) names a **target**:
`{"kind": "agent" | "flow" | "team" | "loop" | "scenario", "id": "..."}`.
`agent_id` still works everywhere as the old spelling of an agent target, so
a set or config written before targets existed reads as `{"kind": "agent"}`.

What the graders score, per kind:

| Kind | Runs as | `output` is | Cell's `run_id` |
| --- | --- | --- | --- |
| agent | one agent run | the agent's answer | the agent run |
| flow | the flow once, in process, through the task driver | the output of the node that finished last | the flow run |
| team | `run_team` in process, the case input as the goal | the team's result: the synthesis, or the last substantive board message | the team run |
| loop | `run_loop` in process, the case input as the goal | the loop's accepted result | the loop run |
| scenario | `run_simulation` in process, the case input sent as an external trigger to one role before the first tick | the scores and the final state, rendered as compact text | the scenario run |

A container cell also records its **trajectory**: the leaf runs it went
through (node runs, member turns, role decisions; a loop lists each iteration's
flow run followed by its node runs), each `{run_id, kind, summary}`. The
result detail links every leaf to its trace and the container to its page.
Trajectory graders read the tool calls of every leaf run in order, so
`tool_called` or `max_tool_calls` work on a team the same way they do on an
agent.

Overrides, per kind:

- **provider and model**: an agent as before; a flow passes them to every
  agent node; a scenario sets them as the default and on every role, so the
  model under test answers for everyone. A team and a loop ignore them: their
  members and nodes run on their own agents' models and neither runner takes
  an override.
- **settings**: a free dict on the config. A scenario reads `max_ticks` and
  `trigger_agent` (the role the input is sent to, the first role by default).
  A team's `max_rounds` and a loop's `max_iterations` are accepted but not
  applied yet: both runners read the stored team or loop.

A team runs under its own chat session (a conversation id per cell) rather
than minting a task on the board. Leaf runs are opened by the container with
its own channel; when the container finishes they are retagged with the
evaluation channel, so their spend leaves production aggregation the same way
an agent cell's does. The cost estimate treats a container cell as one call,
which understates a team or a loop: set `cost_ceiling` on those sweeps.

## Cases from tasks

A case can carry a task snapshot, `artifact`:

```json
{"task_id": "...", "title": "...", "description": "...", "context": "...",
 "documents": [{"name": "...", "text": "..."}],
 "files": [{"path": "src/app.py", "text": "..."}]}
```

`POST /api/evals/{id}/cases` with `from_task_id` builds it through
`evals.snapshot.snapshot_task(task_id, max_files=50, max_bytes=200_000)`, and
`add_eval_case_tool` takes the same `from_task_id`. At run time the files are
written to an isolated folder, `<workspace>/.eval/<eval_run_id>/<case_id>` (a
temporary folder when the set has no workspace folder), which is the agent's
or the flow's working directory. The title, description, context and
documents are prepended to the case input as a "Task" block that also names
the folder.

What a snapshot copies: the task's title, description and context fields
(priority, deadline, project, blocked reason, the parent task's goal), the
results of the tasks it depends on (as documents), and up to 50 text files
or 200 KB from the task's project folder. What it never copies: secrets
matched by name (`.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`,
`id_rsa*`, `.netrc`, `.npmrc`, `credentials*`, `secrets*` and similar),
anything under `.git` or other VCS folders, dependency and cache folders,
binary files, the workspace settings file, the connections store, API keys,
and any external state such as issues, remote repositories or services. A
task with no workspace copies no files at all.

## Cases with files

A case can also carry workspace files, `file_ids` (see [workspace
files](files.md)): stored once in the workspace, picked in the case editor.
The case then runs in the same kind of isolated folder, with the files copied
in, and its input starts with a block naming them and the folder. A case with
files and no snapshot gets the folder too.

## Cases from a run

The cheapest way to build a dataset is to point at a run that already
happened. `evals.runner.case_from_run(run_id)` accepts any run id: an agent
run, or an entity run (flow, team, loop, scenario; `common/entity_runs.py`).
It works out which store the id belongs to and builds a case whose input is
what the run was given and whose `expected` defaults to what the run
produced: a pure regression check, "does this still do what it did". A
failed or errored run has nothing right to repeat, so `expected` defaults to
empty instead and the run's error is kept in the case's `metadata`; the
rubric field is where "what should have happened" belongs for that case.

`POST /api/evals/{id}/cases` with `from_run_id` is the API this powers, the
same route whichever kind the run is. `GET /api/evals/for-run/{run_id}` tells
a caller what fits before it asks: the run's target kind and id, whether it
failed, every eval set in its workspace whose target matches, and a preview
of the case that would be built (or why one cannot be, e.g. "no recorded
input"), so a dialog can offer to create a set with the right target instead
of asking the user to pick one.

The shared frontend piece is `components/evals/SaveAsEvalCaseDialog.jsx`: a
"To eval case" button opens it from a run's own page (Messages, a team's or a
loop's run panel, the flow editor's run history, a playground scenario run,
and the chat turn itself) or from a chat turn's actions, and it lets the
input, expected and rubric be edited before saving. `ah eval add-case <set>
--from-run <run_id> [--expected ..] [--rubric ..]` does the same from the CLI.

## Graders, cheapest first

- **exact** / **substring** / **regex** — deterministic string checks.
- **json_valid** / **json_schema** — the output parses, and matches a schema.
- **assertions** — structured predicates over the result.
- **tool_called** / **tool_not_called** / **tool_sequence** / **max_tool_calls**
  / **tool_input_matches** / **no_error_tool_results**: trajectory graders,
  described below. Free and deterministic, like the output-based ones, but
  they read what the agent *did* rather than what it said.
- **rubric**: grades the result against a markdown rubric, criterion by
  criterion, with an independent model. Costs tokens. The same grader as
  [task outcomes](outcomes.md).
- **llm_judge** — a model grades the answer. The only grader that costs money,
  and the only one whose verdict is itself a model's opinion.

Reach for a deterministic grader whenever the question allows one. An LLM judge
on a question that a regex could answer adds cost and noise.

(`final_agent` is not a grader. It is a [loop](loops.md) evaluator mode, where
the flow's terminal agent reviews the flow's own result.)

## Trajectory graders

An output grader only sees the final text. These read the tool calls recorded
on the real run behind the case: which tools ran, in what order, with what
input, and whether any of them errored. That lets a case fail for "it never
looked anything up", even when the prose it produced reads fine.

**tool_called**: a named tool ran, optionally within a call-count range.

```yaml
kind: tool_called
params:
  tool: search_docs
  min_times: 1
  max_times: 3
```

**tool_not_called**: a named tool never ran (a forbidden one for this case, say).

```json
{"kind": "tool_not_called", "params": {"tool": "delete_file_tool"}}
```

**tool_sequence**: named tools ran in that order. `contiguous: true` requires
them back to back, otherwise other calls may fall between them.

```yaml
kind: tool_sequence
params:
  tools: [search_docs, read_doc, create_eval_tool]
  contiguous: false
```

**max_tool_calls**: a ceiling on total tool calls in the run, for catching an
agent that loops instead of finishing.

```json
{"kind": "max_tool_calls", "params": {"limit": 6}}
```

**tool_input_matches**: a regex matches the JSON of some call's input to a
named tool.

```yaml
kind: tool_input_matches
params:
  tool: run_eval_tool
  pattern: '"user_approved"\s*:\s*true'
```

**no_error_tool_results**: no tool call in the run returned an error payload
(an `{"ok": false, ...}` envelope, an `"error"` key, or a raised exception).

```json
{"kind": "no_error_tool_results", "params": {}}
```

Each reports a reason naming what it actually saw (which tool, how many times,
what order), never just pass or fail.

## Cost

A sweep is cases times configs model calls before you learn anything, so the
projected spend is reported before it starts, and the workspace
[budget](costs.md) is re-checked between cells: a runaway sweep stops the sweep,
not just one run.

Eval runs are tagged as an evaluation channel, which keeps measurement spend out
of production cost and budget aggregation while leaving every cell inspectable
in the normal run views.

## Batch mode

**Batch mode** in the run form sends the sweep through the provider batch
APIs (OpenAI Batch, Anthropic Message Batches): half the price, results
within 24 hours, most batches within the hour. The run shows `batch_pending`
and a table of its provider batches until they finish; the scheduler checks
them every minute (`AGENTS_HUB_EVAL_BATCH_POLL_SECONDS`, default 60), and
**Check now** does it on the spot.

It happens in two phases. An agent cell goes out as the agent's first model
call, with the same system prompt, tools and parameters its live run would
send. A final answer is recorded as the cell's run at the batch price (the run
record carries `price_factor: 0.5`, so Costs shows what was billed). Then the
`llm_judge` and `rubric` calls of those cells go out as a second batch when
the judge model has a batch API; the other graders score right away.

Some cells run live instead, at once, with the reason on the cell's
trajectory:

| Case | Reason |
| --- | --- |
| The batched answer asked for a tool | One answer is not a whole tool loop, so the cell reruns live from the start |
| Flow, team, loop or scenario target | A container is many calls, not one |
| Google, Ollama, LM Studio, another OpenAI-compatible server | No batch API |
| The agent uses tool search or a structured output schema, or a guardrail checks it | These change the call or the answer, and only apply exactly live |
| The provider refused the batch, or a request failed or expired | The cell still gets a result |

The cost ceiling is checked against the projection before anything is sent,
because a submitted batch cannot stop half way; the estimate in batch mode
prices batchable calls at half and is a floor, since live fallbacks cost full
price. **Cancel** cancels the open batches: answers the provider already
produced are still collected, the rest of the cells are recorded as stopped.

The API takes `"mode": "batch"` on `POST /api/evals/{id}/run` and
`/estimate`; `GET /api/eval-runs/{id}` returns `batch` (each provider batch's
phase, status and counts) for a batch run; `POST /api/eval-runs/{id}/poll`
and `/cancel` are the two actions. The provider key is never stored with the
batch: it is resolved again from the agent or the judge model when the batch
is polled.

## Repeats and variance

A config can set `repeats` (default 1, capped at 10) to run every case that
many times under it instead of once. A model is not deterministic, so one draw
can make a flaky case look solid, or a solid one look flaky. Repeats turn that
into a measured spread instead of a guess.

Each attempt is its own real run and its own row in the score matrix's
underlying results, numbered 1-based. The projected cost multiplies by
`repeats` per config, since it is exactly that many more model calls.

The summary, per config, adds:

- **pass_rate**: the fraction of attempts (across every case) that passed.
- **std**: the population standard deviation of the score across attempts.
- **min** / **max**: the lowest and highest attempt score.
- **cases**: the same four numbers broken down per case, plus **unstable**:
  `true` when that case's attempts disagreed on pass/fail. A case that passed
  3 times and failed twice is not "usually fine", it is unreliable, and the
  badge says so before the mean score can hide it.

With the default `repeats: 1` every attempt-level number collapses to the
single-run number it always was, so nothing about an existing sweep changes
unless a config actually asks for more than one repeat.

A live pass of this path exists as `scripts/live_lab_eval.py`: it builds a
small lab scenario from the template (six ticks, two experiments), wraps it in
an eval set with one case and a regex grader, runs it three times on one cheap
model (`--provider openai --model gpt-4o-mini` by default) and prints the
per-attempt table and the aggregate. It spends real money, cents at the
defaults, under `--cost-ceiling`. Last passed 2026-09-24: three attempts, all
completed, about 20 seconds each, 0.6 cents in total; the scenario and the
eval set stay in the `lab-smoke` workspace for the Playground and Evals pages.

## Diff of two runs

`GET /api/evals/runs/{a}/diff/{b}` compares two runs of the same set cell by
cell, matched by `(case id, config label)`, so it still lines up even if the
two runs used different config lists, or different repeat counts. A cell
counts as "passed" for this comparison when its pass_rate was >= 0.5.

Each case comes back as `{case_id, config_a, config_b, a: {passed, score},
b: {...}, change}`, where `change` is `fixed`, `regressed`, `same`, `only_a`
(the pair existed in the first run only) or `only_b`. The `summary` gives the
counts that matter first: `fixed`, `regressed`, `same`, and each run's overall
`pass_rate`.

The Eval Agent's `run_eval_tool` can do this in the same call: pass
`compare_with_previous: true` and the response includes a `diff` against the
set's most recent prior run, so "did that change help" does not need a second
round trip.

## Prompt suggestions from failed cases

`evals/prompt_suggest.py` turns a finished sweep's failures into a proposed
fix. `build_suggestion(eval_run_id)` requires a run swept on exactly one
agent target with at least one failed case; it reads that agent's current
`instructions.md`, and for each failed case its input, output, expected or
rubric, and the graders' `detail` (their reasons), and asks a model for a revised `instructions.md`: the
eval set's own judge model (an `llm_judge` or `rubric` grader that names one)
when it has one, else the model a column of the sweep ran on (it just
answered, so it is reachable), else the workspace default. It returns the new text
plus a rationale tied to the case ids it read. The model call is recorded as
a run of its own (agent `prompt_optimizer`, channel `eval`), the same
reasoning as an outcome grading's run: one model call about a whole eval run
is not part of any one cell's own cost. The suggestion itself is stored in
`prompt_suggestions` (migration 0028), `pending` until decided.

`POST /api/eval-runs/{id}/suggest-prompt` builds one (a real, billable model
call, served from a worker thread like `start_eval_run`).
`GET /api/eval-runs/{id}/suggestions` lists what exists for a run.
`POST /api/prompt-suggestions/{id}/apply` writes the new instructions
through the same path the definition editor uses (`PUT
/api/agents/{id}/definition`), so `agents/versions.py` snapshots the old
version and a rollback is always available; `{"rerun": true}` also starts the
same eval set again so the before and after can be compared with the
existing diff route. `.../dismiss` marks it decided without touching
anything. An eval set's `suggest_on_failure` flag (default off, shown as a
checkbox in the set's settings) makes a finished sweep with failures build a
suggestion by itself, right after `run_eval` saves the run.

The Evals page shows this as `components/evals/PromptSuggestionPanel.jsx`
beside the score matrix: a "Suggest prompt fix" button when a run has
failures and no suggestion yet, then a diff of the old and new instructions
(`lib/lineDiff.js`), the rationale, and Apply / Dismiss.

## The injection audit

A separate harness asks a different question: not "is the answer right" but
"does the agent resist a hostile page". It serves fixture pages through the real
`fetch_url` path and scores compliance on three independent signals: a canary
string repeated in the answer, a request to an exfiltration sink, and a
forbidden tool call in the run trace. Reporting the attack is not counted as
complying with it.

The static half of that audit costs nothing: it checks that the web scanner
flags each fixture and that hidden text is stripped before an agent sees it.
That part is a property of the code, not of a model, so it belongs in CI.

## Online evals

An eval set measures an agent on cases you wrote. An online eval measures it
on its real traffic: an alert rule of kind `online_eval` grades a share of the
agent's production runs and raises a notification when one scores too low.

```json
{
  "kind": "online_eval",
  "agent_id": "support_agent",
  "sample_rate": 0.1,
  "kinds": ["agent"],
  "graders": [
    {"kind": "llm_judge", "params": {"rubric": "Does it answer the question asked?"}},
    {"kind": "no_error_tool_results", "weight": 0.5}
  ],
  "min_score": 0.7,
  "severity": "warning",
  "channels": ["dashboard", "slack"]
}
```

How it runs:

1. When a run finishes `completed`, each enabled `online_eval` rule whose
   kind and agent filters match decides whether the run is sampled. The decision is a
   hash of the rule id and the run id against `sample_rate`, so it is the same
   on every replica and on every re-evaluation. A sampled run becomes one row
   in `online_eval_jobs`; nothing is graded on the finish path. Eval and
   replay runs are never sampled, and failed runs are left to `run_failed`.
2. The `online_evals` background loop grades pending jobs every ten seconds.
   It runs on one replica at a time, under a lease held by the singleton
   supervisor (the health page lists it). It loads the run's output and its
   structured payload, the same way a trajectory grader does, and runs the
   graders. Each grader gets a case built from the run: the input is the
   message the run answered, and `expected` / `rubric` come from the grader's
   params when set, else from the rule. The score is the weighted mean, and
   the run passes only when every grader passes, as in an eval set.
3. The result lands in `online_eval_results` with the run's definition hash
   and, when known, the stored version it was built from (its experiment arm,
   or the history row with the same hash). Below `min_score`, the rule's
   notification fires with the rule's severity and channels.

Jobs are rows, so a restart picks up where it stopped. A job whose grading
fails is marked `failed` with the error and not retried; a job left `running`
by a process that died is put back and given up after three attempts.

**Kinds.** A rule's `kinds` lists the run kinds it grades: `agent` (the
default, and what a rule written before this field means), `flow`, `team`,
`loop`, `scenario`. For a container run the `agent_id` filter names the
entity (the team id, the loop id...), and the graded output is the
container's own: a team's or a loop's result, a flow's last node output, a
scenario's scores and final state. A container run is offered to the rules
the moment its record reaches a terminal status (common/entity_runs.py runs
the same evaluation a leaf run's close does), so a team, loop, flow or
scenario run is sampled the way an agent run is.

An `llm_judge` grader costs a model call per graded run, so keep
`sample_rate` low for busy agents.

Reading it: the agent's **Overview** tab has a **Live quality** card with the
count, mean score and pass rate, split by definition version, the latest
graded runs and the rules themselves (with a form to add one). The same data
is at `GET /api/agents/{agent_id}/online-evals?limit=` and
`GET /api/agents/{agent_id}/online-evals/summary` (grouped by definition
version and hash, and by rule). Comparing two versions under live traffic is
what [experiments](experiments.md) are for.

## Why bother

A prompt change that "feels better" on two examples is not a result. The whole
point of this surface is to turn that into a number you can compare before and
after.

A run that finishes with a failing cell also wakes every [proactive
agent](proactive.md) whose profile has an `eval` trigger (optionally for that
eval set), with the failing configs and their pass counts as the event.

Related: [costs](costs.md), [agents](agents.md), [tools-and-capabilities](tools-and-capabilities.md), [experiments](experiments.md), [notifications](notifications.md), [proactive](proactive.md).
