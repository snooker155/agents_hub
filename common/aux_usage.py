"""
Model calls a run makes beside its own loop, and what they cost.

A run's token totals come from the callbacks on its own model calls
(agents/callbacks/run_statistics.py). Some policies call a model of their own
on the run's behalf: the tool policy's classifier (tools/permission_policy.py),
a guardrail's judge (guardrails/checks.py), the repair of an answer that does
not match its schema (agents/loop_ext/structured.py), and, after the run, the
grader of a task's outcome (tasks/outcome.py). Those calls are not in the
run's totals, so they were free as far as the Costs page and the per-run
money cap could tell.

:func:`record` is the one place they are counted. Each call becomes an entry
``{purpose, provider, model, input_tokens, output_tokens, cached_tokens}`` on
the running loop's ``aux_calls`` (stored on the run as ``loop.aux_calls``),
and its price is added to the process's spend that
``agents.callbacks.guards.RunBudgetGuard`` checks, so a classifier that runs
often is stopped by the same cap as the agent. ``common.pricing.run_cost_usd``
prices every entry at its own model. :func:`record_on_run` does the same for
a call made after the run ended (the outcome grader), writing the entry onto
that run's record instead.

Every function here is best-effort: accounting must never fail the call it
accounts for.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

_KNOWN_PROVIDERS = ("anthropic", "openai", "google", "ollama")


def _provider_of(llm: Any) -> str:
    name = type(llm).__name__.lower() if llm is not None else ""
    for known in _KNOWN_PROVIDERS:
        if known in name:
            return known
    return ""


def _model_of(llm: Any, response: Any) -> str:
    md = getattr(response, "response_metadata", None) or {}
    for key in ("model_name", "model"):
        if md.get(key):
            return str(md[key])
    for attr in ("model_name", "model"):
        value = getattr(llm, attr, None)
        if isinstance(value, str) and value:
            return value
    return ""


def usage_of(response: Any) -> Dict[str, int]:
    """``{input_tokens, output_tokens, cached_tokens}`` of one chat response
    (an AIMessage or an LLMResult), zeros when the provider reported none."""
    try:
        from agents.callbacks.run_statistics import (
            cached_input_tokens, extract_token_usage, normalize_usage,
        )
        if hasattr(response, "generations"):
            usage = extract_token_usage(response)
        else:
            from langchain_core.outputs import ChatGeneration, LLMResult
            usage = extract_token_usage(LLMResult(generations=[[ChatGeneration(message=response)]]))
        prompt, completion, _total = normalize_usage(usage)
        cached = max(0, min(cached_input_tokens(usage), prompt))
        return {"input_tokens": int(prompt), "output_tokens": int(completion),
                "cached_tokens": int(cached)}
    except Exception:  # noqa: BLE001 - an unreadable usage block counts as no tokens
        return {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}


def entry(purpose: str, *, provider: Optional[str] = None, model: Optional[str] = None,
          llm: Any = None, response: Any = None,
          tokens: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """One accounting entry. Provider and model default to what the built
    model and its response say, so a call on the global default is still
    priced."""
    counts = dict(tokens) if tokens is not None else usage_of(response)
    return {
        "purpose": str(purpose),
        "provider": str(provider or _provider_of(llm) or ""),
        "model": str(model or _model_of(llm, response) or ""),
        "input_tokens": int(counts.get("input_tokens") or 0),
        "output_tokens": int(counts.get("output_tokens") or 0),
        "cached_tokens": int(counts.get("cached_tokens") or 0),
    }


def _charge_budget(item: Dict[str, Any]) -> None:
    """Add the call to the spend the run's money cap checks (only a launched
    run with a cap has one; otherwise nothing to do)."""
    try:
        from agents.callbacks.guards import charge_aux_spend
        charge_aux_spend(item["provider"], item["model"], item["input_tokens"],
                         item["output_tokens"], item["cached_tokens"])
    except Exception:  # noqa: BLE001 - the cap is checked again on the agent's next call
        log.debug("aux usage: budget charge failed", exc_info=True)


def record(purpose: str, *, provider: Optional[str] = None, model: Optional[str] = None,
           llm: Any = None, response: Any = None,
           tokens: Optional[Dict[str, int]] = None) -> Optional[Dict[str, Any]]:
    """Count one auxiliary call of the running loop. Returns the entry, or
    None when there is no running loop to put it on."""
    try:
        item = entry(purpose, provider=provider, model=model, llm=llm,
                     response=response, tokens=tokens)
    except Exception:  # noqa: BLE001 - see the module docstring
        log.debug("aux usage: could not build an entry for %s", purpose, exc_info=True)
        return None
    try:
        from agents.agent_loop import current_state
        state = current_state()
    except Exception:  # noqa: BLE001 - no loop module means no run to count on
        state = None
    if state is None:
        return None
    state.aux_calls.append(item)
    _charge_budget(item)
    return item


def record_on_run(run_id: str, item: Dict[str, Any]) -> None:
    """Append an entry to a finished run's ``loop.aux_calls`` (the outcome
    grader grades a run after it closed)."""
    if not run_id or not item:
        return
    try:
        from managers import run_manager
        run = run_manager.get_run_by_id(run_id) or {}
        loop = dict(run.get("loop") or {}) if isinstance(run.get("loop"), dict) else {}
        loop["aux_calls"] = [*list(loop.get("aux_calls") or []), dict(item)]
        run_manager.update_run(run_id, {"loop": loop})
    except Exception:  # noqa: BLE001 - see the module docstring
        log.debug("aux usage: could not add an entry to run %s", run_id, exc_info=True)


__all__ = ["entry", "record", "record_on_run", "usage_of"]
