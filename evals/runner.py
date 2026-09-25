"""
Eval runner — execute an eval set across one or more configs and score it.

A target is an agent, a flow, a team, a loop or a scenario; each kind runs a
case through its adapter in :data:`TARGET_RUNNERS` (the container kinds live
in ``evals.targets``). For an agent the runner reuses the existing machinery rather than reimplementing it: agents are built
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

from evals import store, targets
from evals.graders import COSTED_GRADERS, grade_all
from evals.models import (
    EVAL_CHANNEL, Case, EvalResult, EvalRun, EvalSet, RunConfig, utc_iso,
)
from evals.targets import Outcome

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


def _batch_factor(provider: str, mode: str) -> float:
    """What a call costs relative to live in this mode: half for a provider
    with a batch API in a batch run (evals/batch.py), full otherwise."""
    if mode != "batch":
        return 1.0
    from providers.batch_api import BATCH_PROVIDERS, PRICE_FACTOR
    return PRICE_FACTOR if (provider or "").lower() in BATCH_PROVIDERS else 1.0


def project_cost(evalset: EvalSet, configs: List[RunConfig],
                 avg_inbound: int = 1500, avg_outbound: int = 500,
                 mode: str = "live") -> Dict[str, Any]:
    """Estimate what a sweep will cost, before spending anything.

    A rough per-call token estimate is enough: the number that matters to a user
    about to launch 10 x 100 calls is the order of magnitude, and pretending to
    more precision than the inputs support would be worse than useless.

    A config's ``repeats`` multiplies its own cell count directly: three
    repeats of every case is three times the calls, not a rounding footnote.

    ``mode="batch"`` prices agent cells and judge calls on OpenAI and
    Anthropic at the batch rate (half). Cells that turn out to need a tool,
    or targets that are not agents, run live at full price, so a batch
    estimate is a floor, not a quote.
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
        if cfg.target_kind == "agent":
            unit *= _batch_factor(provider, mode)
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
        judge_cost += (cells * _run_cost(j_provider, j_model, 1200, 120)
                       * _batch_factor(j_provider, mode))
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
        "mode": mode,
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
        spec = get_agent(cfg.target_id) if cfg.target_kind == "agent" else None
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


def _case_files_block(case: Case, work_dir: Optional[str]) -> str:
    """The lines naming a case's workspace files: where they were copied
    (``prepare_work_dir``) or, without a directory, their ids."""
    if not case.file_ids:
        return ""
    from files import service as files_service
    records = files_service.get_files(case.file_ids)
    if not records:
        return ""
    lines = ["Attached files:"]
    for record in records:
        copy = files_service.locate_copy(record, work_dir) if work_dir else None
        where = f"{copy.name}: " if copy is not None else ""
        lines.append(f"- {where}{files_service.describe(record)}")
    if work_dir:
        lines.append(f"They are in your working directory: {work_dir}")
    return "\n".join(lines)


def compose_input(case: Case, work_dir: Optional[str] = None) -> str:
    """The message a target receives for ``case``.

    A case without an artifact or files sends its input unchanged. A case
    with an artifact gets a "Task" block first: the snapshot's title,
    description and context, the documents it carried, and where its files
    were written. A case with workspace files (``file_ids``) gets a block
    naming them and the directory they were copied into.
    """
    art = case.artifact or {}
    files_block = _case_files_block(case, work_dir)
    if not art:
        if not files_block:
            return case.input
        return f"{files_block}\n\n{case.input}" if case.input else files_block
    lines = ["Task"]
    if art.get("title"):
        lines.append(f"Title: {art['title']}")
    if art.get("description"):
        lines.append(str(art["description"]).strip())
    context = str(art.get("context") or "").strip()
    if context:
        lines += ["", "Context:", context]
    for doc in art.get("documents") or []:
        if isinstance(doc, dict) and doc.get("text"):
            lines += ["", f"Document: {doc.get('name') or 'document'}", str(doc["text"])]
    if work_dir and art.get("files"):
        lines += ["", f"Working files: {work_dir}"]
    if files_block:
        lines += ["", files_block]
    block = "\n".join(lines).strip()
    return f"{block}\n\n{case.input}" if case.input else block


def _run_agent_target(case: Case, cfg: RunConfig, evalset: EvalSet, eval_run_id: str,
                      workspace: Optional[str], *, prompt: str,
                      work_dir: Optional[str] = None, attempt: int = 1) -> Outcome:
    """One agent run on the case, recorded as a real run tagged ``eval``.

    ``work_dir``, when set, is the agent's working directory
    (``create_agent(..., workspace=<path>)``).
    """
    from managers import run_manager as rm

    overrides: Dict[str, Any] = {}
    if cfg.provider:
        overrides["provider"] = cfg.provider
    if cfg.model:
        overrides["model"] = cfg.model

    agent_id = cfg.target_id
    try:
        from agents.agent_factory import create_agent
        agent = create_agent(agent_id, work_dir or workspace, **overrides)
    except Exception as e:
        return Outcome(ok=False,
                       error=f"could not build agent {agent_id!r}: {type(e).__name__}: {e}")

    provider = getattr(agent, "provider", "") or ""
    model = getattr(agent, "model", "") or ""

    run_id = rm.new_unique_run_id()
    title = f"Eval {evalset.name or evalset.eval_set_id}: {case.case_id}"
    if attempt > 1:
        title += f" (attempt {attempt})"
    rm.open_run(
        run_id,
        agent_id,
        workspace=workspace,
        title=title,
        channel=EVAL_CHANNEL,
        execution_mode=EVAL_CHANNEL,
        session_type=EVAL_CHANNEL,
        message_origin=EVAL_CHANNEL,
        provider=provider,
        model=model,
        input=prompt,
        link_to_session=False,
    )
    rm.seed_run_input_context(run_id, getattr(agent, "system_prompt", "") or "", prompt)
    outcome = Outcome(run_id=run_id)

    try:
        from agents.agent_invoke import invoke_agent
        invocation = invoke_agent(agent, prompt, run_id=run_id)
        rm.close_run_from_result(run_id, invocation.result, process=invocation.process)
    except Exception as e:
        outcome.ok = False
        outcome.error = f"{type(e).__name__}: {e}"
        return outcome

    ok = bool(getattr(invocation.result, "ok", False))
    outcome.ok = ok
    outcome.output = str(getattr(invocation.result, "agent_output", "") or "") if ok else ""
    outcome.error = None if ok else str(getattr(invocation.result, "error", "") or "agent error")
    outcome.duration_ms = int(invocation.duration_ms or 0)
    tu = (invocation.process or {}).get("token_usage") or {}
    outcome.inbound_tokens = int(tu.get("inbound_tokens") or 0)
    outcome.outbound_tokens = int(tu.get("outbound_tokens") or 0)
    outcome.cost = _run_cost(provider, model, outcome.inbound_tokens, outcome.outbound_tokens)
    outcome.trajectory = [{"run_id": run_id, "kind": "run", "summary": agent_id}]
    return outcome


#: How each target kind runs one case: ``fn(case, cfg, evalset, eval_run_id,
#: workspace, *, prompt, work_dir, attempt) -> Outcome``. Looked up per cell,
#: so a test can swap one entry.
TARGET_RUNNERS: Dict[str, Callable[..., Outcome]] = {
    "agent": _run_agent_target,
    "flow": targets.run_flow_target,
    "team": targets.run_team_target,
    "loop": targets.run_loop_target,
    "scenario": targets.run_scenario_target,
}


def prepare_work_dir(case: Case, eval_run_id: str, workspace: Optional[str],
                     attempt: int = 1) -> Optional[str]:
    """The isolated directory a case with an artifact or workspace files runs
    in, with the artifact's files written and the workspace files copied in;
    None for a case with neither."""
    if not case.artifact and not case.file_ids:
        return None
    from evals.snapshot import isolation_dir, materialize_artifact
    directory = isolation_dir(workspace, eval_run_id, case.case_id, attempt)
    if case.artifact:
        directory = materialize_artifact(case.artifact, directory)
    if case.file_ids:
        from files import service as files_service
        # Only files of the run's own workspace, whatever the stored case says
        # (the routes refuse others; this holds for a case written elsewhere).
        own = [r["file_id"] for r in files_service.get_files(case.file_ids)
               if r.get("workspace") == (workspace or "default")]
        files_service.materialize(own, directory)
    return str(directory)


def run_case(case: Case, cfg: RunConfig, evalset: EvalSet,
             eval_run_id: str, workspace: Optional[str], *, attempt: int = 1) -> EvalResult:
    """Execute one cell: run the target on the case, then grade the output.

    The target's kind picks the adapter in :data:`TARGET_RUNNERS`. ``attempt``
    is the 1-based repeat number for this (case, config) pair, always 1 unless
    ``cfg.repeats`` asks for more.
    """
    kind = cfg.target_kind
    result = EvalResult(
        eval_run_id=eval_run_id, case_id=case.case_id,
        config_label=cfg.resolved_label(), attempt=attempt, target_kind=kind,
    )
    adapter = TARGET_RUNNERS.get(kind)
    if adapter is None:
        result.ok = False
        result.error = f"no runner for target kind {kind!r}"
        return store.save_result(result)

    try:
        work_dir = prepare_work_dir(case, eval_run_id, workspace, attempt)
    except Exception as e:  # noqa: BLE001 - recorded on the cell
        result.ok = False
        result.error = f"could not prepare the case's files: {type(e).__name__}: {e}"
        return store.save_result(result)
    prompt = compose_input(case, work_dir)

    try:
        outcome = adapter(case, cfg, evalset, eval_run_id, workspace,
                          prompt=prompt, work_dir=work_dir, attempt=attempt)
    except Exception as e:  # noqa: BLE001 - a crashing target is a failed cell
        log.warning("eval: %s target %s raised", kind, cfg.target_id, exc_info=True)
        outcome = Outcome(ok=False, error=f"{type(e).__name__}: {e}")

    result.run_id = outcome.run_id
    result.ok = bool(outcome.ok)
    result.error = None if outcome.ok else (outcome.error or f"{kind} error")
    result.output = outcome.output if outcome.ok else ""
    result.trajectory = list(outcome.trajectory or [])
    result.duration_ms = int(outcome.duration_ms or 0)
    result.inbound_tokens = int(outcome.inbound_tokens or 0)
    result.outbound_tokens = int(outcome.outbound_tokens or 0)
    result.cost = float(outcome.cost or 0.0)

    # A failed run scores zero rather than being skipped: "the target errored"
    # is a regression, and dropping the cell would quietly inflate the average.
    if result.ok:
        scores, combined, passed = grade_all(result.output, case, evalset.graders,
                                             run_id=result.run_id)
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
    mode: str = "live",
) -> EvalRun:
    """Run every case against every config, grade, aggregate, persist.

    ``cost_ceiling`` (USD) stops the sweep when accumulated spend crosses it.
    The workspace budget is re-checked per cell as well, so a sweep cannot walk
    past a hard cap one run at a time.

    ``mode="batch"`` hands the sweep to evals/batch.py: agent cells and judge
    calls go through the provider batch APIs at half price and the run is
    ``batch_pending`` until the provider finishes. The ceiling is then checked
    against the projection up front, since a submitted batch cannot stop half way.
    """
    evalset = store.get_eval_set(eval_set_id)
    if not evalset:
        raise ValueError(f"Eval set not found: {eval_set_id}")

    ws = workspace or evalset.workspace
    configs = list(configs or [])
    if not configs:
        baseline = evalset.default_config()
        if baseline is None:
            raise ValueError("No configs given and the eval set has no default target")
        configs = [baseline]

    if mode not in ("live", "batch"):
        raise ValueError(f"unknown eval mode {mode!r} (live or batch)")
    if mode == "batch":
        from evals.batch import start_batch_run
        return start_batch_run(evalset, configs, ws, cost_ceiling=cost_ceiling,
                               on_progress=on_progress)

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
    saved = store.save_eval_run(run)
    if saved.status == "completed" and evalset.suggest_on_failure:
        _maybe_auto_suggest(saved)
    return saved


def _maybe_auto_suggest(run: EvalRun) -> None:
    """A finished sweep with failed cases on an agent target builds a prompt
    suggestion on its own when the set's ``suggest_on_failure`` flag is on
    (evals/prompt_suggest.py). Best-effort: a failure here never turns a
    completed sweep into a failed one, and "nothing to suggest" (no agent
    target, or nothing failed) is not logged as a problem."""
    try:
        from evals.prompt_suggest import SuggestionError, build_suggestion
        build_suggestion(run.eval_run_id)
    except SuggestionError:
        pass
    except Exception:  # noqa: BLE001 - see docstring
        log.warning("eval: auto prompt suggestion failed for %s", run.eval_run_id, exc_info=True)


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


#: A run in one of these statuses has nothing worth repeating: seeding a case
#: from it defaults `expected` to empty (never "what it produced") and keeps
#: the run's error in the case's metadata instead.
FAILED_STATUSES = ("failed", "error")


def _entity_case_io(kind: str, rec: Dict[str, Any]) -> Tuple[str, str]:
    """Best-effort ``(input, output)`` for a finished entity run (flow, team,
    loop, scenario; common/entity_runs.py), used to seed a case.

    A team or loop run keeps its own ``goal`` and ``result``. A scenario has
    neither: it renders like the matrix does (``targets.render_scenario``). A
    flow keeps no goal of its own — its ``title`` is the closest it has to one
    — and no output either, so the output is the last leaf run (node) that
    produced one, the same rule ``targets.run_flow_target`` scores.
    """
    title = str(rec.get("title") or "")
    if kind in ("team", "loop"):
        return str(rec.get("goal") or title), str(rec.get("result") or "")
    if kind == "scenario":
        return title, targets.render_scenario(rec.get("scores") or {}, rec.get("final_state") or {})
    if kind == "flow":
        output = ""
        try:
            from managers.run_manager import get_runs_by_ids
            leaves = targets.leaf_run_ids("flow", str(rec.get("run_id") or ""))
            records = get_runs_by_ids(leaves) if leaves else {}
            for rid in reversed(leaves):
                out = str((records.get(rid) or {}).get("output") or "")
                if out:
                    output = out
                    break
        except Exception:  # noqa: BLE001 - best-effort: an empty output is still a usable case
            log.debug("case_from_run: could not derive flow output for %s", rec.get("run_id"), exc_info=True)
        return title, output
    return title, ""


def _run_target_info(run_id: str) -> Optional[Dict[str, Any]]:
    """Where ``run_id`` came from: an agent run (``runs`` table) or an entity
    run (flow/team/loop/scenario, ``common/entity_runs.py``), whichever store
    holds it. None when neither does.

    Returned as ``{"source", "record", "target_kind", "target_id", "workspace",
    "status"}`` — the shape both :func:`case_from_run` and the ``for-run`` API
    (which eval sets a run fits) read.
    """
    from managers import run_manager as rm

    original = rm.get_run_by_id(run_id)
    if original is not None:
        return {
            "source": "agent", "record": original,
            "target_kind": "agent", "target_id": str(original.get("agent_id") or ""),
            "workspace": original.get("workspace"), "status": str(original.get("status") or ""),
        }
    from common import entity_runs
    rec = entity_runs.get(run_id)
    if rec is None:
        return None
    kind = str(rec.get("kind") or "")
    return {
        "source": "entity", "record": rec,
        "target_kind": kind, "target_id": str(rec.get("entity_id") or ""),
        "workspace": rec.get("workspace"), "status": str(rec.get("status") or ""),
    }


def case_from_run(run_id: str, *, expected: Optional[str] = None,
                  rubric: Optional[str] = None) -> Case:
    """Seed a case from a recorded run — the cheapest way to build a dataset.

    ``run_id`` may be an agent run or an entity run (flow, team, loop,
    scenario), whichever the id belongs to; the case's eval set target then
    matches what actually ran (see ``evals.targets``).

    Defaults ``expected`` to what the run actually produced, which makes the
    first eval a pure regression check: "does this still do what it did" —
    except for a failed or errored run, which has nothing worth repeating:
    ``expected`` then defaults to empty (an explicit ``expected`` still wins)
    and the run's error is kept in the case's metadata, for the rubric field
    to answer instead ("what should have happened").
    """
    info = _run_target_info(run_id)
    if info is None:
        raise ValueError(f"Run not found: {run_id}")

    rec = info["record"]
    metadata: Dict[str, Any] = {
        "target_kind": info["target_kind"], "target_id": info["target_id"],
        "seeded_at": utc_iso(),
    }
    if info["source"] == "agent":
        from managers import run_manager as rm
        proc = rm.get_run_process(run_id) or {}
        input_ctx = proc.get("input_context") or {}
        user_message = str(input_ctx.get("user_message") or rec.get("input") or "").strip()
        produced = str((proc.get("response") or {}).get("text") or rec.get("output") or "")
        metadata["agent_id"] = info["target_id"]
        metadata["model"] = rec.get("model") or ""
    else:
        user_message, produced = _entity_case_io(info["target_kind"], rec)
        user_message = user_message.strip()

    if not user_message:
        raise ValueError("Run has no recorded input to build a case from")

    failed = info["status"] in FAILED_STATUSES
    if failed:
        run_error = rec.get("error")
        if run_error:
            metadata["error"] = str(run_error)
        case_expected = expected
    else:
        case_expected = expected if expected is not None else (produced or None)

    return Case(
        input=user_message,
        expected=case_expected,
        rubric=rubric,
        source_run_id=run_id,
        metadata=metadata,
    )


__all__ = [
    "run_eval", "run_case", "compose_input", "prepare_work_dir", "TARGET_RUNNERS",
    "Outcome", "summarize", "diff_runs", "project_cost",
    "case_from_run", "FAILED_STATUSES", "EvalStopped",
]
