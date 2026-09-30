"""
Prompt suggestion — a revised instructions.md proposed from an eval run's
failed cases (doc item: "автопредложение правки промпта по проваленным
кейсам").

Only an eval run swept on a single agent target can be diagnosed this way:
instructions.md is the agent's own prompt, and a flow/team/loop/scenario run
has no one prompt to rewrite. :func:`build_suggestion` reads the run's own
recorded matrix (``evals.store.build_matrix``), collects the failed cells,
and asks a model — the eval set's own judge model when it has one, else the
workspace default — to propose a revised instructions.md plus a rationale
tied to the case ids it read.

The model call is recorded as a run of its own (agent ``prompt_optimizer``,
channel ``eval``), the same reasoning as an outcome grading's own run
(``tasks/outcome.py`` ``_record_grading_run``): one model call about a whole
eval run is not part of any one cell's own cost, so it is not folded into any
of them.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from evals import graders, store
from evals.graders import _extract_json, _reply_text, split_model_ref
from evals.models import EVAL_CHANNEL, EvalRun, EvalSet, PromptSuggestion, utc_iso

log = logging.getLogger(__name__)

#: The agent id and channel the suggestion's own model call is recorded
#: under — mirrors ``tasks.outcome.GRADER_AGENT_ID`` / ``GRADING_CHANNEL``.
AGENT_ID = "prompt_optimizer"

_SYSTEM = (
    "You revise an AI agent's system prompt (instructions.md) so it stops "
    "making the mistakes shown below. You reply with one JSON object and "
    "nothing else, never prose outside it."
)

_PROMPT = """## The agent's current instructions.md
{instructions}

## Cases it failed in this eval run
{cases}

## How to revise
- Keep everything in the current instructions that is not implicated by a
  failure below; do not rewrite the prompt from scratch.
- Add or change only what would have prevented these specific failures.
- Every change should be traceable to at least one case id cited above.
- If the instructions already cover a failure and the agent still got it
  wrong, say so in the rationale rather than repeating the same instruction
  more forcefully.

Reply with a single JSON object and nothing else:
{{"instructions": "<the full revised instructions.md>", "rationale": "<what changed and why, citing case ids like [case_xxx]>"}}"""

#: How much of a case's fields a suggestion prompt carries, so a handful of
#: verbose failures cannot blow the request past what the judge model reads.
_FIELD_LIMIT = 4000


class SuggestionError(ValueError):
    """The eval run cannot be diagnosed this way: no single agent target, or
    nothing failed."""


def _clip(text: str, limit: int = _FIELD_LIMIT) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "\n[... truncated ...]"


def _case_block(case: Any, result: Any) -> str:
    lines = [f"### {case.case_id}", "Input:", _clip(case.input) or "(empty)"]
    if case.expected:
        lines += ["Expected:", _clip(case.expected)]
    if case.rubric:
        lines += ["Rubric (what should have happened):", _clip(case.rubric)]
    lines += ["Produced:", _clip(result.output or result.error or "(no output)")]
    reasons = [
        str(s.get("detail") or "").strip()
        for s in (result.scores or {}).values() if str(s.get("detail") or "").strip()
    ]
    if reasons:
        lines += ["Grader reasons:"] + [f"- {r}" for r in reasons]
    return "\n".join(lines)


def _failed_cells(eval_run_id: str, cases_by_id: Dict[str, Any], labels: set) -> List[Tuple[Any, Any]]:
    """``(case, result)`` for every failed cell of this run that ran under one
    of ``labels`` (the agent's own config labels) and whose case still exists
    on the set."""
    out = []
    for result in store.list_results(eval_run_id):
        if result.passed or result.config_label not in labels:
            continue
        case = cases_by_id.get(result.case_id)
        if case is not None:
            out.append((case, result))
    return out


def _judge_model(evalset: EvalSet, configs: Optional[List[Any]] = None) -> Dict[str, str]:
    """The model that writes the suggestion: the set's own judge (an
    ``llm_judge`` or ``rubric`` grader naming one) when it has one, else the
    model the sweep itself ran on when a column named one (it just answered,
    so it is known to be reachable), else the workspace default."""
    for g in evalset.graders:
        if g.kind not in ("llm_judge", "rubric"):
            continue
        ref = (split_model_ref(g.params.get("grader")) if g.params.get("grader")
               else {"provider": str(g.params.get("provider") or ""),
                     "model": str(g.params.get("model") or "")})
        if ref.get("model"):
            return ref
    for cfg in configs or []:
        if getattr(cfg, "model", None):
            return {"provider": str(getattr(cfg, "provider", None) or ""), "model": str(cfg.model)}
    try:
        from common.config import settings
        provider = settings.default_provider
        model = {
            "openai": settings.model,
            "anthropic": getattr(settings, "anthropic_model", ""),
            "google": getattr(settings, "google_model", ""),
            "ollama": settings.ollama_model,
            "lmstudio": settings.lmstudio_model,
        }.get(provider, settings.model)
        return {"provider": provider, "model": model}
    except Exception:  # noqa: BLE001 - an unresolved default still lets build_chat_model try its own fallback
        return {"provider": "", "model": ""}


def _record_suggestion_run(evalset: EvalSet, run: EvalRun, ref: Dict[str, str],
                           inbound: int, outbound: int) -> Optional[str]:
    """Record the suggestion's model call as a run of its own. Best-effort:
    a failure here leaves the suggestion built but its cost unrecorded,
    never the other way round."""
    try:
        from managers import run_manager as rm
        run_id = rm.new_unique_run_id()
        when = utc_iso()
        rm.upsert_run({
            "run_id": run_id, "agent_id": AGENT_ID, "channel": EVAL_CHANNEL,
            "workspace": evalset.workspace, "status": "completed", "exit_code": 0,
            "title": f"Prompt suggestion for {evalset.name or evalset.eval_set_id}",
            "provider": ref.get("provider") or "", "model": ref.get("model") or "",
            "created_at": when, "started_at": when, "finished_at": when,
            "eval_run_id": run.eval_run_id, "eval_set_id": evalset.eval_set_id,
            "process": {"token_usage": {"inbound_tokens": inbound, "outbound_tokens": outbound,
                                        "total_tokens": inbound + outbound}},
        })
        return run_id
    except Exception:  # noqa: BLE001 - the suggestion stands; only its own cost record is missing
        log.warning("prompt_suggest: could not record the suggestion's run", exc_info=True)
        return None


def build_suggestion(eval_run_id: str) -> PromptSuggestion:
    """Build (and store) a prompt suggestion from a finished eval run's
    failed cases. Raises :class:`SuggestionError` when there is nothing to
    suggest: no single agent target on this run, or no failed case."""
    from agents import prompt_assembly

    run = store.get_eval_run(eval_run_id)
    if not run:
        raise SuggestionError(f"Eval run not found: {eval_run_id}")
    evalset = store.get_eval_set(run.eval_set_id)
    if not evalset:
        raise SuggestionError(f"Eval set not found: {run.eval_set_id}")

    agent_configs = [c for c in run.configs if c.target_kind == "agent"]
    agent_ids = {c.target_id for c in agent_configs}
    if not agent_ids:
        raise SuggestionError("This eval run has no agent target to suggest a prompt for")
    if len(agent_ids) > 1:
        raise SuggestionError("This eval run swept more than one agent; suggest from a single-agent run")
    agent_id = next(iter(agent_ids))
    labels = {c.resolved_label() for c in agent_configs}

    cases_by_id = {c.case_id: c for c in evalset.cases}
    failed = _failed_cells(eval_run_id, cases_by_id, labels)
    if not failed:
        raise SuggestionError("No failed cases on this eval run")

    instructions = prompt_assembly.read_instructions(agent_id)
    cases_text = "\n\n".join(_case_block(case, result) for case, result in failed)
    prompt = _PROMPT.format(
        instructions=instructions or "(no instructions.md on file)", cases=cases_text,
    )

    ref = _judge_model(evalset, agent_configs)
    from agents.agent_utils import build_chat_model
    try:
        llm = build_chat_model(provider=ref["provider"] or None, model=ref["model"] or None,
                               temperature=0.2)
        reply = llm.invoke([("system", _SYSTEM), ("human", prompt)])
    except Exception as e:  # noqa: BLE001 - any provider failure is a suggestion that could not be built
        raise SuggestionError(f"The model call failed: {type(e).__name__}: {e}") from e

    if not ref["model"]:
        ref["model"] = str(getattr(llm, "model_name", None) or getattr(llm, "model", None) or "")

    parsed = _extract_json(_reply_text(reply)) or {}
    new_instructions = str(parsed.get("instructions") or "").strip()
    rationale = str(parsed.get("rationale") or "").strip()
    if not new_instructions:
        raise SuggestionError("The model did not return a revised prompt")

    usage = getattr(reply, "usage_metadata", None) or {}
    inbound = int((usage.get("input_tokens") if isinstance(usage, dict) else 0) or 0)
    outbound = int((usage.get("output_tokens") if isinstance(usage, dict) else 0) or 0)
    cost = graders.model_call_cost(ref["provider"], ref["model"], inbound, outbound)
    suggest_run_id = _record_suggestion_run(evalset, run, ref, inbound, outbound)

    suggestion = PromptSuggestion(
        eval_run_id=eval_run_id, eval_set_id=evalset.eval_set_id, agent_id=agent_id,
        workspace=evalset.workspace, old_instructions=instructions,
        new_instructions=new_instructions, rationale=rationale,
        case_ids=[case.case_id for case, _ in failed], suggest_run_id=suggest_run_id,
        cost_usd=cost,
    )
    return store.save_prompt_suggestion(suggestion)


def apply_suggestion(suggestion_id: str, *, rerun: bool = False) -> Dict[str, Any]:
    """Write the suggestion's instructions.md through the definition editor's
    own path (so agents/versions.py snapshots the old version, and a rollback
    stays available), mark the suggestion applied, and optionally start the
    eval set again so the before/after can be diffed.

    Returns ``{"suggestion": ..., "eval_run": ...}`` — ``eval_run`` is the new
    sweep's result when ``rerun`` was asked for, else None.
    """
    suggestion = store.get_prompt_suggestion(suggestion_id)
    if not suggestion:
        raise SuggestionError(f"Suggestion not found: {suggestion_id}")
    if suggestion.status != "pending":
        raise SuggestionError(f"Suggestion already {suggestion.status}")
    # The suggestion rewrites the whole file from the text it was made
    # against. An edit made since would be lost without a trace in the
    # suggestion itself, so it has to be made again on the current text.
    from agents import prompt_assembly
    current = prompt_assembly.read_instructions(suggestion.agent_id)
    if (current or "").strip() != (suggestion.old_instructions or "").strip():
        raise SuggestionError(
            "The agent's instructions changed since this suggestion was made; "
            "dismiss it and suggest again from the current text")

    from dashboard.backend.routes.agents import AgentInstructionsUpdate, update_agent_definition
    import asyncio

    async def _write() -> None:
        await update_agent_definition(
            suggestion.agent_id, AgentInstructionsUpdate(instructions=suggestion.new_instructions),
        )
    asyncio.run(_write())

    try:
        from common import audit
        from common.identity import current_user_id
        audit.record(
            "eval.prompt_suggestion_applied",
            actor={"actor_id": current_user_id(), "actor_kind": "user", "actor_name": None},
            workspace=suggestion.workspace, object_type="agent", object_id=suggestion.agent_id,
            details={"suggestion_id": suggestion.suggestion_id, "eval_run_id": suggestion.eval_run_id,
                    "case_ids": list(suggestion.case_ids)},
        )
    except Exception:  # noqa: BLE001 - the write stands; only its audit entry is missing
        log.warning("prompt_suggest: could not audit apply of %s", suggestion.suggestion_id, exc_info=True)

    suggestion.status = "applied"
    suggestion.decided_at = utc_iso()

    new_run: Optional[Dict[str, Any]] = None
    if rerun:
        from evals.runner import run_eval
        # The same columns as the sweep the suggestion came from, so the
        # before and after differ only in the prompt: without them the set
        # would run on the agent's own model, which may not be the one the
        # failures were seen on.
        original = store.get_eval_run(suggestion.eval_run_id)
        eval_run = run_eval(suggestion.eval_set_id, list(original.configs) if original else None,
                            workspace=suggestion.workspace)
        suggestion.applied_run_id = eval_run.eval_run_id
        new_run = {**eval_run.to_dict(), "matrix": store.build_matrix(eval_run.eval_run_id)}

    store.save_prompt_suggestion(suggestion)
    return {"suggestion": suggestion.to_dict(), "eval_run": new_run}


def dismiss_suggestion(suggestion_id: str) -> PromptSuggestion:
    suggestion = store.get_prompt_suggestion(suggestion_id)
    if not suggestion:
        raise SuggestionError(f"Suggestion not found: {suggestion_id}")
    if suggestion.status != "pending":
        raise SuggestionError(f"Suggestion already {suggestion.status}")
    # The suggestion rewrites the whole file from the text it was made
    # against. An edit made since would be lost without a trace in the
    # suggestion itself, so it has to be made again on the current text.
    from agents import prompt_assembly
    current = prompt_assembly.read_instructions(suggestion.agent_id)
    if (current or "").strip() != (suggestion.old_instructions or "").strip():
        raise SuggestionError(
            "The agent's instructions changed since this suggestion was made; "
            "dismiss it and suggest again from the current text")
    suggestion.status = "dismissed"
    suggestion.decided_at = utc_iso()
    return store.save_prompt_suggestion(suggestion)


__all__ = [
    "SuggestionError", "build_suggestion", "apply_suggestion", "dismiss_suggestion",
]
