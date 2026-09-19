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
- **llm_judge** — a model grades the answer. The only grader that costs money,
  and the only one whose verdict is itself a model's opinion.

Reach for a deterministic grader whenever the question allows one. An LLM judge
on a question that a regex could answer adds cost and noise.

(`final_agent` is not a grader. It is a [loop](loops.md) evaluator mode, where
the flow's terminal agent reviews the flow's own result.)

## Cost

A sweep is cases times configs model calls before you learn anything, so the
projected spend is reported before it starts, and the workspace
[budget](costs.md) is re-checked between cells: a runaway sweep stops the sweep,
not just one run.

Eval runs are tagged as an evaluation channel, which keeps measurement spend out
of production cost and budget aggregation while leaving every cell inspectable
in the normal run views.

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
