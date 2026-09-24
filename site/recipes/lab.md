---
title: "A research group in the lab"
description: "Run a scenario where a lead, a theorist, an experimentalist, a critic and a scribe test hypotheses with real sandboxed experiments"
---

# A research group in the lab

The `lab` playground environment is a research group working on one question. Hypotheses form a tree, experiments are Python programs that really run in the sandbox, and the run ends when the top level hypotheses are decided or the experiment budget is spent. Charts, a table, formulas and a report appear as views while the run goes.

## What you get

A scenario created from a template in one click, a hypothesis tree with a status per node on the run page, a dataset of metrics built from every finished experiment, a report document, and a reproducibility eval that repeats the whole run and shows how the repeats agree.

## Before you start

- The playground enabled (`PLAYGROUND_ENABLED=true`, the default)
- A sandbox for `run_code`: Docker running, or `CODE_RUNNER_FALLBACK=local` for plain subprocesses
- A provider key, since each tick is a model call

## Steps

1. Open **Playground** and press **New from template**. Pick **lab**, then choose the agent that plays every role, or a team whose members become the roles.

2. The template fills a sample question, five roles (lead, theorist, experimentalist, critic and scribe), triggered activation with the lead opening the scene, and 30 ticks. Change the question in the environment parameters, and the budget with `max_experiments` and `experiment_timeout`.

3. Press **Estimate** to see the expected cost, then **Run**.

4. Watch the run page. The world view shows the question, a budget meter, the hypothesis tree with a status chip per node (proposed, testing, confirmed, refuted, needs repeat), each hypothesis's experiments with their seed and metrics, the report sections and the formulas.

5. An experiment is a Python program written by the experimentalist. It reads one JSON object from stdin with `params` and `seed`, and prints one JSON object of numeric metrics as its last line. The sandbox has no network and a timeout, so long computations are split into several experiments.

6. The **Views** strip on the run page links to the results table, the chart of the first metric per experiment, the formulas and the report. They update in place on every tick and stay after the run.

7. The run ends with the reason `hypotheses_decided` or `budget_exhausted`, shown on the run meters, or on the usual limits.

8. For reproducibility, press **Reproducibility run** on the scenario page and set the number of repeats. It creates an eval set targeting this scenario and starts the sweep; the Evals page shows the repeats side by side.

![The lab run page with the hypothesis tree and the budget meter](/screenshots/recipes/lab.png)

## Where to read more

The lab section of [Playground](/guide/playground) covers the state, the actions, the experiment contract, casting by team and the stop reasons. Repeats and variance: [Evals](/guide/evals). The sandbox: [Views](/guide/views) for the code kind and `run_code` in [Tools and capabilities](/guide/tools-and-capabilities).
