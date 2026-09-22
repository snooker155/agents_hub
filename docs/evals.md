# Evals

Eval sets, graders and model sweeps: measure whether a prompt change helped
instead of guessing.

## The pieces

- **An eval set** — the cases to run. A case is an input plus what a good answer
  looks like.
- **A grader** — how each result is scored.
- **A config** — one agent under one provider and model. A sweep runs every case
  against every config, and each cell of the resulting matrix is a real run you
  can open.

## Graders, cheapest first

- **exact** / **substring** / **regex** — deterministic string checks.
- **json_valid** / **json_schema** — the output parses, and matches a schema.
- **assertions** — structured predicates over the result.
- **tool_called** / **tool_not_called** / **tool_sequence** / **max_tool_calls**
  / **tool_input_matches** / **no_error_tool_results**: trajectory graders,
  described below. Free and deterministic, like the output-based ones, but
  they read what the agent *did* rather than what it said.
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

## Why bother

A prompt change that "feels better" on two examples is not a result. The whole
point of this surface is to turn that into a number you can compare before and
after.

Related: [costs](costs.md), [agents](agents.md), [tools-and-capabilities](tools-and-capabilities.md).
