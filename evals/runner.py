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
from typing import Any, Callable, Dict, List, Optional

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
    """
    cells = len(evalset.cases) * max(1, len(configs))
    judge_specs = [g for g in evalset.graders if g.kind in COSTED_GRADERS]

    per_config = []
    for cfg in configs:
        provider, model = _resolve_model(cfg)
        unit = _run_cost(provider, model, avg_inbound, avg_outbound)
        per_config.append({
            "label": cfg.resolved_label(),
            "model": model,
            "estimated_cost": round(unit * len(evalset.cases), 4),
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
             eval_run_id: str, workspace: Optional[str]) -> EvalResult:
    """Execute one cell: invoke the agent on the case, then grade the output."""
    from managers import run_manager as rm

    result = EvalResult(
        eval_run_id=eval_run_id, case_id=case.case_id,
        config_label=cfg.resolved_label(),
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
    rm.open_run(
        run_id,
        cfg.agent_id,
        workspace=workspace,
        title=f"Eval {evalset.name or evalset.eval_set_id}: {case.case_id}",
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
        scores, combined, passed = grade_all(output, case, evalset.graders)
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

    total = len(evalset.cases) * len(configs)
    done = 0
    spend = 0.0
    stopped_reason: Optional[str] = None

    try:
        for cfg in configs:
            for case in evalset.cases:
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

                result = run_case(case, cfg, evalset, run.eval_run_id, ws)
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


def summarize(eval_run_id: str, configs: List[RunConfig]) -> Dict[str, Any]:
    """One number per config, plus the counts behind it.

    The counts are not decoration: "0.82" and "0.82 over 3 of 40 cases" are
    different claims, and only one of them is worth acting on.
    """
    results = store.list_results(eval_run_id)
    summary: Dict[str, Any] = {}
    for cfg in configs:
        label = cfg.resolved_label()
        cells = [r for r in results if r.config_label == label]
        if not cells:
            summary[label] = {
                "score": 0.0, "passed": 0, "total": 0, "errors": 0, "cost": 0.0,
            }
            continue
        summary[label] = {
            "score": round(sum(c.score for c in cells) / len(cells), 4),
            "passed": sum(1 for c in cells if c.passed),
            "total": len(cells),
            "errors": sum(1 for c in cells if not c.ok),
            "cost": round(sum(c.cost for c in cells), 6),
            "avg_duration_ms": int(sum(c.duration_ms for c in cells) / len(cells)),
            "inbound_tokens": sum(c.inbound_tokens for c in cells),
            "outbound_tokens": sum(c.outbound_tokens for c in cells),
        }
    return summary


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
    "run_eval", "run_case", "summarize", "project_cost", "case_from_run",
    "EvalStopped",
]
