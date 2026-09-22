"""
Eval runner — execute an eval set across one or more configs and score it.

Reuses the existing machinery rather than reimplementing it: agents are built
by ``agent_factory.create_agent`` with the config's provider/model override,
invoked through ``agents.agent_invoke.invoke_agent``, and recorded as real runs
tagged ``channel="eval"``. That tag is what keeps evaluation spend out of the
workspace's production cost and budget aggregation (``routes/costs.py``,
``common/budget.py``), exactly as replays already are — while leaving every
eval cell fully inspectable in the normal run views.

Cost is the live risk here: a sweep is ``len(cases) x len(configs)`` LLM calls
before you learn anything. :func:`project_cost` reports the projected spend
before a sweep starts, and the loop re-checks the workspace budget every cell so
a runaway sweep stops the *eval*, not just one run.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from evals import store
from evals.graders import COSTED_GRADERS, grade_all
from evals.models import (
    EVAL_CHANNEL, Case, EvalResult, EvalRun, EvalSet, RunConfig, utc_iso,
)

log = logging.getLogger(__name__)


class EvalStopped(Exception):
    """The sweep was halted (budget cap, cost ceiling, or an explicit stop)."""


def _run_cost(provider: str, model: str, inbound: int, outbound: int) -> float:
    try:
        from common.pricing import load_price_map, run_cost_usd
        fake = {
            "provider": provider, "model": model,
            "process": {"token_usage": {"inbound_tokens": inbound, "outbound_tokens": outbound}},
        }
        return round(run_cost_usd(fake, load_price_map()), 6)
    except Exception:
        return 0.0


def project_cost(evalset: EvalSet, configs: List[RunConfig],
                 avg_inbound: int = 1500, avg_outbound: int = 500) -> Dict[str, Any]:
    """Estimate what a sweep will cost, before spending anything.

    A rough per-call token estimate is enough: the number that matters to a user
    about to launch 10 x 100 calls is the order of magnitude, and pretending to
    more precision than the inputs support would be worse than useless.

    A config's ``repeats`` multiplies its own cell count directly: three
    repeats of every case is three times the calls, not a rounding footnote.
    """
    judge_specs = [g for g in evalset.graders if g.kind in COSTED_GRADERS]

    cells = 0
    per_config = []
    for cfg in configs:
        repeats = cfg.resolved_repeats()
        cfg_cells = len(evalset.cases) * repeats
        cells += cfg_cells
        provider, model = _resolve_model(cfg)
        unit = _run_cost(provider, model, avg_inbound, avg_outbound)
        per_config.append({
            "label": cfg.resolved_label(),
            "model": model,
            "repeats": repeats,
            "estimated_cost": round(unit * cfg_cells, 4),
        })

    agent_cost = sum(c["estimated_cost"] for c in per_config)

    # Judge calls are small, but there is one per judging grader per cell, and
    # they are priced on the *judge's* model — which is usually not the model
    # under test. Fall back to the first config's model when the grader does not
    # name one, since that is what build_chat_model will resolve to anyway.
    judge_cost = 0.0
    for spec in judge_specs:
        j_provider = str(spec.params.get("provider") or "")
        j_model = str(spec.params.get("model") or "")
        if not j_model and configs:
            j_provider, j_model = _resolve_model(configs[0])
        judge_cost += cells * _run_cost(j_provider, j_model, 1200, 120)
    judge_cost = round(judge_cost, 4)

    return {
        "cases": len(evalset.cases),
        "configs": len(configs),
        "llm_calls": cells * (1 + len(judge_specs)),
        "per_config": per_config,
        "estimated_agent_cost": round(agent_cost, 4),
        "estimated_grader_cost": judge_cost,
        "estimated_total_cost": round(agent_cost + judge_cost, 4),
        "uses_llm_judge": bool(judge_specs),
        # `note` stays English for API consumers; `note_key` lets the UI
        # render the same sentence in the user's language.
        "note_key": "estimateNote",
        "note": (
            "Rough estimate from average token counts — treat it as an order of "
            "magnitude, not a quote."
        ),
    }


def _resolve_model(cfg: RunConfig) -> tuple:
    """(provider, model) this config will actually run on, for cost estimation."""
    provider, model = cfg.provider or "", cfg.model or ""
    if provider and model:
        return provider, model
    try:
        from agents.registry import get_agent
        spec = get_agent(cfg.agent_id)
        if spec:
            provider = provider or (spec.provider or "")
            model = model or (spec.model or "")
    except Exception:
        pass
    if not model:
        try:
            from common.config import settings
            provider = provider or settings.default_provider
            # Each provider names its default model in its own setting; falling
            # back to `settings.model` regardless would pair (say) the lmstudio
            # provider with the OpenAI model name and miss the price map.
            model = model or {
                "openai": settings.model,
                "anthropic": getattr(settings, "anthropic_model", ""),
                "google": getattr(settings, "google_model", ""),
                "ollama": settings.ollama_model,
                "lmstudio": settings.lmstudio_model,
            }.get(provider, settings.model)
        except Exception:
            pass
    return provider, model


def run_case(case: Case, cfg: RunConfig, evalset: EvalSet,
             eval_run_id: str, workspace: Optional[str], *, attempt: int = 1) -> EvalResult:
    """Execute one cell: invoke the agent on the case, then grade the output.

    ``attempt`` is the 1-based repeat number for this (case, config) pair —
    always 1 unless ``cfg.repeats`` asks for more.
    """
    from managers import run_manager as rm

    result = EvalResult(
        eval_run_id=eval_run_id, case_id=case.case_id,
        config_label=cfg.resolved_label(), attempt=attempt,
    )

    overrides: Dict[str, Any] = {}
    if cfg.provider:
        overrides["provider"] = cfg.provider
    if cfg.model:
        overrides["model"] = cfg.model

    try:
        from agents.agent_factory import create_agent
        agent = create_agent(cfg.agent_id, workspace, **overrides)
    except Exception as e:
        result.ok = False
        result.error = f"could not build agent {cfg.agent_id!r}: {type(e).__name__}: {e}"
        return store.save_result(result)

    provider = getattr(agent, "provider", "") or ""
    model = getattr(agent, "model", "") or ""

    run_id = rm.new_unique_run_id()
    title = f"Eval {evalset.name or evalset.eval_set_id}: {case.case_id}"
    if attempt > 1:
        title += f" (attempt {attempt})"
    rm.open_run(
        run_id,
        cfg.agent_id,
        workspace=workspace,
        title=title,
        channel=EVAL_CHANNEL,
        execution_mode=EVAL_CHANNEL,
        session_type=EVAL_CHANNEL,
        message_origin=EVAL_CHANNEL,
        provider=provider,
        model=model,
        input=case.input,
        link_to_session=False,
    )
    rm.seed_run_input_context(run_id, getattr(agent, "system_prompt", "") or "", case.input)
    result.run_id = run_id

    try:
        from agents.agent_invoke import invoke_agent
        invocation = invoke_agent(agent, case.input, run_id=run_id)
        rm.close_run_from_result(run_id, invocation.result, process=invocation.process)
    except Exception as e:
        result.ok = False
        result.error = f"{type(e).__name__}: {e}"
        return store.save_result(result)

    ok = bool(getattr(invocation.result, "ok", False))
    output = str(getattr(invocation.result, "agent_output", "") or "") if ok else ""
    result.ok = ok
    result.error = None if ok else str(getattr(invocation.result, "error", "") or "agent error")
    result.output = output
    result.duration_ms = int(invocation.duration_ms or 0)

    tu = (invocation.process or {}).get("token_usage") or {}
    result.inbound_tokens = int(tu.get("inbound_tokens") or 0)
    result.outbound_tokens = int(tu.get("outbound_tokens") or 0)
    result.cost = _run_cost(provider, model, result.inbound_tokens, result.outbound_tokens)

    # A failed run scores zero rather than being skipped: "the agent errored" is
    # a regression, and dropping the cell would quietly inflate the average.
    if ok:
        scores, combined, passed = grade_all(output, case, evalset.graders, run_id=run_id)
        result.scores, result.score, result.passed = scores, combined, passed
    else:
        result.scores, result.score, result.passed = {}, 0.0, False

    return store.save_result(result)


def run_eval(
    eval_set_id: str,
    configs: Optional[List[RunConfig]] = None,
    *,
    workspace: Optional[str] = None,
    cost_ceiling: Optional[float] = None,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> EvalRun:
    """Run every case against every config, grade, aggregate, persist.

    ``cost_ceiling`` (USD) stops the sweep when accumulated spend crosses it.
    The workspace budget is re-checked per cell as well, so a sweep cannot walk
    past a hard cap one run at a time.
    """
    evalset = store.get_eval_set(eval_set_id)
    if not evalset:
        raise ValueError(f"Eval set not found: {eval_set_id}")

    ws = workspace or evalset.workspace
    configs = list(configs or [])
    if not configs:
        if not evalset.agent_id:
            raise ValueError("No configs given and the eval set has no default agent_id")
        configs = [RunConfig(agent_id=evalset.agent_id, label="baseline")]

    run = EvalRun(eval_set_id=eval_set_id, workspace=ws, configs=configs)
    store.save_eval_run(run)

    total = sum(len(evalset.cases) * cfg.resolved_repeats() for cfg in configs)
    done = 0
    spend = 0.0
    stopped_reason: Optional[str] = None

    try:
        for cfg in configs:
            repeats = cfg.resolved_repeats()
            for case in evalset.cases:
                for attempt in range(1, repeats + 1):
                    if cost_ceiling is not None and spend >= cost_ceiling:
                        stopped_reason = (
                            f"cost ceiling reached (${spend:.4f} of ${cost_ceiling:.2f})"
                        )
                        raise EvalStopped(stopped_reason)
                    try:
                        from common.budget import check_budget
                        check_budget(ws)
                    except EvalStopped:
                        raise
                    except Exception as e:
                        # BudgetExceededError (or anything else the budget layer
                        # raises) halts the whole sweep, not just this cell.
                        stopped_reason = f"budget: {e}"
                        raise EvalStopped(stopped_reason)

                    result = run_case(case, cfg, evalset, run.eval_run_id, ws, attempt=attempt)
                    spend += result.cost
                    done += 1
                    if on_progress:
                        try:
                            on_progress({
                                "eval_run_id": run.eval_run_id,
                                "done": done, "total": total,
                                "spend": round(spend, 6),
                                "case_id": case.case_id,
                                "config_label": cfg.resolved_label(),
                                "attempt": attempt,
                                "score": result.score,
                            })
                        except Exception:
                            pass
        run.status = "completed"
    except EvalStopped:
        run.status = "stopped"
        run.error = stopped_reason
    except Exception as e:
        log.exception("eval run failed")
        run.status = "failed"
        run.error = f"{type(e).__name__}: {e}"

    run.summary = summarize(run.eval_run_id, configs)
    run.total_cost = round(spend, 6)
    run.finished_at = utc_iso()
    return store.save_eval_run(run)


def _population_std(values: List[float]) -> float:
    """Population standard deviation (divide by N, not N-1).

    A repeats sweep measures the variance of *these* attempts, not a sample
    meant to generalise past them, so the population formula is the honest one.
    """
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5


def _aggregate_case(attempts: List[EvalResult]) -> Dict[str, Any]:
    """Repeats/variance stats for one (case, config) pair across its attempts.

    ``unstable`` is the signal worth a badge: attempts disagreeing on pass/fail
    means the mean score is papering over a coin flip, not a stable result.
    """
    scores = [a.score for a in attempts]
    passed_flags = [bool(a.passed) for a in attempts]
    return {
        "attempts": len(attempts),
        "pass_rate": round(sum(1 for p in passed_flags if p) / len(attempts), 4),
        "mean_score": round(sum(scores) / len(attempts), 4),
        "std": round(_population_std(scores), 4),
        "min": round(min(scores), 4),
        "max": round(max(scores), 4),
        "unstable": len(set(passed_flags)) > 1,
    }


def summarize(eval_run_id: str, configs: List[RunConfig]) -> Dict[str, Any]:
    """One number per config, plus the counts behind it, plus per-case variance.

    The counts are not decoration: "0.82" and "0.82 over 3 of 40 cases" are
    different claims, and only one of them is worth acting on. With repeats > 1,
    "cases" below breaks that config's cells down per case: pass_rate, mean
    score, population std, min/max and whether attempts disagreed on pass/fail.
    """
    results = store.list_results(eval_run_id)
    summary: Dict[str, Any] = {}
    for cfg in configs:
        label = cfg.resolved_label()
        cells = [r for r in results if r.config_label == label]
        if not cells:
            summary[label] = {
                "score": 0.0, "passed": 0, "total": 0, "errors": 0, "cost": 0.0,
                "pass_rate": 0.0, "std": 0.0, "min": 0.0, "max": 0.0,
                "unstable_cases": 0, "cases": {},
            }
            continue

        scores = [c.score for c in cells]
        by_case: Dict[str, List[EvalResult]] = {}
        for c in cells:
            by_case.setdefault(c.case_id, []).append(c)
        case_stats = {case_id: _aggregate_case(attempts) for case_id, attempts in by_case.items()}

        summary[label] = {
            "score": round(sum(scores) / len(cells), 4),
            "passed": sum(1 for c in cells if c.passed),
            "total": len(cells),
            "errors": sum(1 for c in cells if not c.ok),
            "cost": round(sum(c.cost for c in cells), 6),
            "avg_duration_ms": int(sum(c.duration_ms for c in cells) / len(cells)),
            "inbound_tokens": sum(c.inbound_tokens for c in cells),
            "outbound_tokens": sum(c.outbound_tokens for c in cells),
            # Attempt-level spread across the whole config, not just one case.
            "pass_rate": round(sum(1 for c in cells if c.passed) / len(cells), 4),
            "std": round(_population_std(scores), 4),
            "min": round(min(scores), 4),
            "max": round(max(scores), 4),
            "unstable_cases": sum(1 for s in case_stats.values() if s["unstable"]),
            "cases": case_stats,
        }
    return summary


def diff_runs(run_a_id: str, run_b_id: str) -> Dict[str, Any]:
    """Compare two eval runs cell by cell, keyed by (case id, config label).

    Uses each run's own recorded results (re-aggregated the same way
    ``summarize`` does), so this works even for a run persisted before this
    field existed, and even when the two runs used different config lists.

    A case/config pair "passed" for the purpose of this diff when its
    pass_rate (across whatever repeats it had) is >= 0.5 — the majority of its
    attempts agreed it passed.
    """
    run_a = store.get_eval_run(run_a_id)
    run_b = store.get_eval_run(run_b_id)
    if not run_a:
        raise ValueError(f"Eval run not found: {run_a_id}")
    if not run_b:
        raise ValueError(f"Eval run not found: {run_b_id}")

    def grouped(eval_run_id: str) -> Dict[Tuple[str, str], Dict[str, Any]]:
        by_key: Dict[Tuple[str, str], List[EvalResult]] = {}
        for r in store.list_results(eval_run_id):
            by_key.setdefault((r.case_id, r.config_label), []).append(r)
        return {key: _aggregate_case(attempts) for key, attempts in by_key.items()}

    a_map = grouped(run_a_id)
    b_map = grouped(run_b_id)
    keys = sorted(set(a_map) | set(b_map))

    rows: List[Dict[str, Any]] = []
    fixed = regressed = same = 0
    a_passed = a_total = b_passed = b_total = 0

    for case_id, config_label in keys:
        a_stats = a_map.get((case_id, config_label))
        b_stats = b_map.get((case_id, config_label))
        a_ok = a_stats["pass_rate"] >= 0.5 if a_stats else None
        b_ok = b_stats["pass_rate"] >= 0.5 if b_stats else None

        if a_stats is None:
            change = "only_b"
        elif b_stats is None:
            change = "only_a"
        elif not a_ok and b_ok:
            change = "fixed"
            fixed += 1
        elif a_ok and not b_ok:
            change = "regressed"
            regressed += 1
        else:
            change = "same"
            same += 1

        if a_stats is not None:
            a_total += 1
            a_passed += 1 if a_ok else 0
        if b_stats is not None:
            b_total += 1
            b_passed += 1 if b_ok else 0

        rows.append({
            "case_id": case_id,
            "config_a": config_label if a_stats is not None else None,
            "config_b": config_label if b_stats is not None else None,
            "a": {"passed": a_ok, "score": a_stats["mean_score"]} if a_stats else None,
            "b": {"passed": b_ok, "score": b_stats["mean_score"]} if b_stats else None,
            "change": change,
        })

    return {
        "cases": rows,
        "summary": {
            "fixed": fixed,
            "regressed": regressed,
            "same": same,
            "pass_rate_a": round(a_passed / a_total, 4) if a_total else 0.0,
            "pass_rate_b": round(b_passed / b_total, 4) if b_total else 0.0,
        },
    }


def case_from_run(run_id: str, *, expected: Optional[str] = None,
                  rubric: Optional[str] = None) -> Case:
    """Seed a case from a recorded run — the cheapest way to build a dataset.

    Defaults ``expected`` to what the run actually produced, which makes the
    first eval a pure regression check: "does this still do what it did".
    """
    from managers import run_manager as rm

    original = rm.get_run_by_id(run_id)
    if not original:
        raise ValueError(f"Run not found: {run_id}")

    proc = rm.get_run_process(run_id) or {}
    input_ctx = proc.get("input_context") or {}
    user_message = str(input_ctx.get("user_message") or original.get("input") or "").strip()
    if not user_message:
        raise ValueError("Run has no recorded input to build a case from")

    produced = str((proc.get("response") or {}).get("text") or original.get("output") or "")
    return Case(
        input=user_message,
        expected=expected if expected is not None else (produced or None),
        rubric=rubric,
        source_run_id=run_id,
        metadata={
            "agent_id": original.get("agent_id") or "",
            "model": original.get("model") or "",
            "seeded_at": utc_iso(),
        },
    )


__all__ = [
    "run_eval", "run_case", "summarize", "diff_runs", "project_cost",
    "case_from_run", "EvalStopped",
]
