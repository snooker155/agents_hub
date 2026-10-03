#!/usr/bin/env python
"""
One live run of the lab scenario under the evals runner, repeated.

The lab (docs/playground.md, "The lab environment") is the one playground
world that runs real code through the sandbox and keeps a budget, so a live
pass over it exercises the playground runner, the experiment contract, the
views it publishes and the scenario target of the evals (docs/evals.md) in
one go. This script builds a small lab from the template, wraps it in an eval
set with one case and a regex grader, and runs it ``--repeats`` times on one
cheap model, then prints the per-attempt table and the aggregate the Evals
page would show. It spends real money: the defaults (6 ticks, 2 experiments,
3 repeats, ``gpt-4o-mini``) cost cents, and ``--cost-ceiling`` stops the sweep
when the estimate crosses it.

Runs in process against the local state root, like ``ah`` in direct mode::

    python scripts/live_lab_eval.py --provider openai --model gpt-4o-mini --repeats 3

The scenario and the eval set are left in place under the workspace
``--workspace`` (``lab-smoke`` by default), so the run can be opened on the
Playground and Evals pages afterwards; ``--cleanup`` deletes both at the end.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--provider", default="openai")
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--agent", default="researcher",
                    help="the registered agent every lab role is cast with (its own model is overridden)")
    ap.add_argument("--workspace", default="lab-smoke")
    ap.add_argument("--ticks", type=int, default=6)
    ap.add_argument("--max-experiments", type=int, default=2)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--cost-ceiling", type=float, default=1.0)
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()

    from agents.registry import get_agent
    from evals import runner, store as eval_store
    from evals.models import Case, EvalSet, GraderSpec, RunConfig
    from playground import store as sim_store
    from playground.models import Scenario
    from playground.scenario_templates import scenario_from_template
    from workspace import create_workspace_folder

    if not get_agent(args.agent):
        print(f"[FAIL] agent {args.agent!r} is not registered", flush=True)
        sys.exit(1)
    create_workspace_folder(args.workspace)

    payload = scenario_from_template("lab", workspace=args.workspace, agent_id=args.agent)
    payload["name"] = f"Lab smoke ({args.model})"
    payload["max_ticks"] = args.ticks
    payload.setdefault("env_params", {})["max_experiments"] = args.max_experiments
    scenario = sim_store.save_scenario(Scenario.from_dict(payload))
    print(f"[ok]   scenario {scenario.scenario_id}: {len(scenario.roles)} roles, "
          f"{args.ticks} ticks, {args.max_experiments} experiments", flush=True)

    evalset = EvalSet(
        name=f"Lab smoke ({args.model})", workspace=args.workspace,
        description="Live check of the lab scenario under the evals runner (scripts/live_lab_eval.py)",
        target={"kind": "scenario", "id": scenario.scenario_id},
        cases=[Case(input="Start the study on the whiteboard question and report what the lab found.")],
        graders=[GraderSpec(kind="regex", params={"pattern": r"(?is)hypothes|experiment|report"})],
    )
    eval_store.save_eval_set(evalset)
    cfg = RunConfig(target={"kind": "scenario", "id": scenario.scenario_id},
                    provider=args.provider, model=args.model, repeats=args.repeats,
                    label=f"{args.model} x{args.repeats}")
    print(f"[ok]   eval set {evalset.eval_set_id}, {args.repeats} repeats on {args.provider}/{args.model}",
          flush=True)

    started = time.time()

    def _progress(event: dict) -> None:
        print(f"       {json.dumps(event, default=str)[:200]}", flush=True)

    run = runner.run_eval(evalset.eval_set_id, [cfg], workspace=args.workspace,
                          cost_ceiling=args.cost_ceiling, on_progress=_progress)
    results = eval_store.list_results(run.eval_run_id)
    print(f"[ok]   eval run {run.eval_run_id} {run.status} in {time.time() - started:.0f}s, "
          f"cost ${run.total_cost:.4f}", flush=True)
    print("       attempt  ok     score  passed  duration_ms  tokens_in  tokens_out  error", flush=True)
    for r in sorted(results, key=lambda x: x.attempt):
        print(f"       {r.attempt:>7}  {str(r.ok):5}  {r.score:5.2f}  {str(r.passed):6}  "
              f"{r.duration_ms:>11}  {r.inbound_tokens:>9}  {r.outbound_tokens:>10}  {(r.error or '')[:60]}",
              flush=True)
    summary = runner.summarize(run.eval_run_id, [cfg])
    print(f"       summary: {json.dumps(summary, default=str)[:600]}", flush=True)
    failed = [r for r in results if not r.ok]

    if args.cleanup:
        eval_store.delete_eval_set(evalset.eval_set_id)
        sim_store.delete_scenario(scenario.scenario_id)
        print("[ok]   cleaned up the scenario and the eval set", flush=True)

    if run.status != "completed" or len(results) != args.repeats or failed:
        print(f"[FAIL] {len(failed)} of {len(results)} attempts failed, run status {run.status}", flush=True)
        sys.exit(1)
    print("all attempts completed", flush=True)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    main()
