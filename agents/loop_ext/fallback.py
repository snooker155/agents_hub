"""
Loop extension: fallback models per agent (see agents/agent_loop.py).

Active when ``agent.spec.fallback_models`` (catalog ids, ``"provider/model"``)
is non-empty. ``wrap_model`` chains the primary model and every fallback
through ``Runnable.with_fallbacks``: on a rate limit, a server error, an
overload, a connection error, a timeout, or a refusal, the next model in the
list is tried on the same call. A plain bad request, an authentication
failure or a permission error is never retried on another model, since it
would fail there too (a malformed request or a bad key stays malformed or
bad everywhere).

Each fallback chat model is built once, when the extension is built for the
agent (cached on the extension instance, not on the per-run ``LoopState``,
per the agent_loop.py contract: a built agent is cached and reused across
many runs, so nothing here may hold per-run data). ``rebind`` (handed to
``wrap_model`` by ``agents/agent_loop.py``) binds the same tools and
parameters onto a fallback model exactly as the primary was bound, so a
fallback answers with the same tool set.

Refusal and error-class detection both go through a small
``BaseCallbackHandler`` attached per candidate with ``Runnable.with_config``
(the same mechanism ``agents/callbacks/guards.py`` uses for its own guards):
``on_llm_end`` raises :class:`ModelRefused` when the response is a refusal,
``on_llm_error`` records the exception's class so the candidate that
eventually answers can say what it was standing in for. Attaching callbacks
this way (rather than wrapping the model in a ``RunnableLambda``) keeps the
candidate itself a genuine streaming Runnable, so the executor's
token-level streaming of the bound model is unaffected.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.callbacks import BaseCallbackHandler

from agents.agent_loop import LoopExtension

log = logging.getLogger(__name__)


class ModelRefused(RuntimeError):
    """Raised when a model's response was a refusal, so the next model in the
    fallback chain is tried instead."""


def _response_is_refusal(response: Any) -> bool:
    """Whether an ``LLMResult`` (the argument ``on_llm_end`` receives) carries
    a refusal: Anthropic's ``stop_reason: "refusal"`` or OpenAI's `refusal`
    field on the message."""
    try:
        for grp in (getattr(response, "generations", None) or []):
            for g in grp:
                msg = getattr(g, "message", None)
                if msg is None:
                    continue
                meta = getattr(msg, "response_metadata", None) or {}
                if str(meta.get("stop_reason") or meta.get("finish_reason") or "") == "refusal":
                    return True
                ak = getattr(msg, "additional_kwargs", None) or {}
                if ak.get("refusal"):
                    return True
    except Exception:  # noqa: BLE001 - an unreadable response is not treated as a refusal
        return False
    return False


def _response_model_name(response: Any) -> str:
    """The model name the provider reported for this call, if any (mirrors
    ``agents.callbacks.guards._response_model``, kept local to avoid a
    cross-module private import)."""
    try:
        out = getattr(response, "llm_output", None) or {}
        name = out.get("model_name") or out.get("model")
        if name:
            return str(name)
    except Exception:  # noqa: BLE001 - fall through to the per-generation metadata below
        log.debug("fallback: llm_output model name lookup failed", exc_info=True)
    try:
        for grp in (getattr(response, "generations", None) or []):
            for g in grp:
                md = getattr(getattr(g, "message", None), "response_metadata", None) or {}
                name = md.get("model_name") or md.get("model")
                if name:
                    return str(name)
    except Exception:  # noqa: BLE001 - no model name found is reported as empty, not raised
        log.debug("fallback: per-generation model name lookup failed", exc_info=True)
    return ""


def _call_tokens(response: Any) -> Dict[str, int]:
    """``{"input_tokens", "output_tokens", "cached_tokens"}`` of one response,
    zeros when the provider reported nothing."""
    try:
        from agents.callbacks.run_statistics import (
            cached_input_tokens, extract_token_usage, normalize_usage,
        )
        usage = extract_token_usage(response)
        prompt, completion, _total = normalize_usage(usage)
        cached = max(0, min(cached_input_tokens(usage), prompt))
        return {"input_tokens": int(prompt), "output_tokens": int(completion),
                "cached_tokens": int(cached)}
    except Exception:  # noqa: BLE001 - an unreadable usage block prices the call at the run's model, as before
        return {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}


class _CandidateCallback(BaseCallbackHandler):
    """Attached to one candidate model (the primary or one fallback) in the
    chain. Raises :class:`ModelRefused` on a refusal so ``with_fallbacks``
    moves on; otherwise records the answering model on ``state.answered_by``
    with the reason the previous candidate (if any) was skipped, read from
    ``reason_holder`` which every candidate in the same call shares."""

    def __init__(self, state: Any, ref: Dict[str, str], is_primary: bool,
                 reason_holder: Dict[str, str]) -> None:
        super().__init__()
        self.raise_error = True  # tell LangChain to propagate ModelRefused
        self.state = state
        self.ref = dict(ref)
        self.is_primary = is_primary
        self.reason_holder = reason_holder

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        if _response_is_refusal(response):
            self.reason_holder["reason"] = "ModelRefused"
            raise ModelRefused(f"{self.ref.get('provider')}/{self.ref.get('model')} refused the request")
        state = self.state
        if state is None:
            return
        if self.is_primary and getattr(state, "scratch", {}).get("model_switch"):
            # A person switched the run's model (steering mode switch_model):
            # the "primary" here is the switched model, whose own callback
            # (agents/loop_ext/steering.py) records the answer and its price.
            return
        model_name = _response_model_name(response) or self.ref.get("model") or ""
        entry = {
            "provider": self.ref.get("provider") or "",
            "model": model_name,
            "fallback": not self.is_primary,
            "reason": "" if self.is_primary else self.reason_holder.get("reason", ""),
        }
        if not self.is_primary:
            # The run's token totals are priced at the agent's own model
            # (common/pricing.run_cost_usd); a call a fallback answered carries
            # its own tokens and catalog model so its share is priced at the
            # fallback's rate instead.
            entry.update(_call_tokens(response))
            entry["price_model"] = self.ref.get("model") or model_name
        try:
            state.answered_by.append(entry)
        except Exception:  # noqa: BLE001 - a state we cannot annotate still returns the answer
            log.debug("fallback: could not record answered_by", exc_info=True)

    def on_llm_error(self, error: Any, **kwargs: Any) -> None:
        self.reason_holder["reason"] = type(error).__name__


_RETRYABLE_EXCEPTIONS_CACHE: Optional[Tuple[type, ...]] = None


def _retryable_exceptions() -> Tuple[type, ...]:
    """Exception classes that move to the next model: rate limits (429),
    server errors (5xx, including Anthropic's 529 overload), connection
    errors and timeouts, from whichever of the OpenAI/Anthropic Python SDKs
    are installed, plus our own refusal signal.

    Deliberately narrower than each SDK's ``APIStatusError`` base class: that
    base also covers ``BadRequestError``/``AuthenticationError``/
    ``PermissionDeniedError`` (400/401/403), which must never be retried on
    another model (they would fail there too), so only the specific
    retryable subclasses are named.
    """
    global _RETRYABLE_EXCEPTIONS_CACHE
    if _RETRYABLE_EXCEPTIONS_CACHE is not None:
        return _RETRYABLE_EXCEPTIONS_CACHE
    exc: List[type] = [ModelRefused]
    try:
        import openai as _openai
        exc += [_openai.RateLimitError, _openai.InternalServerError,
                _openai.APIConnectionError, _openai.APITimeoutError]
    except Exception:  # noqa: BLE001 - the openai SDK may not be installed
        log.debug("fallback: openai SDK not available for exception classes", exc_info=True)
    try:
        import anthropic as _anthropic
        exc += [_anthropic.RateLimitError, _anthropic.InternalServerError,
                _anthropic.APIConnectionError, _anthropic.APITimeoutError]
        overloaded = getattr(_anthropic, "OverloadedError", None)
        if overloaded is None:
            try:
                from anthropic._exceptions import OverloadedError as overloaded  # noqa: N814
            except Exception:  # noqa: BLE001 - the private module may move between versions
                overloaded = None
        if overloaded is not None:
            exc.append(overloaded)
    except Exception:  # noqa: BLE001 - the anthropic SDK may not be installed
        log.debug("fallback: anthropic SDK not available for exception classes", exc_info=True)
    _RETRYABLE_EXCEPTIONS_CACHE = tuple(exc)
    return _RETRYABLE_EXCEPTIONS_CACHE


def _llm_model_name(llm: Any) -> str:
    for attr in ("model", "model_name"):
        val = getattr(llm, attr, None)
        if isinstance(val, str) and val:
            return val
    return ""


class FallbackExtension(LoopExtension):
    """Chains the primary model and every built fallback through
    ``with_fallbacks``. ``fallbacks`` is ``[(ref, llm), ...]``, built once in
    :func:`extension_for` and reused for every run of this built agent."""

    name = "fallback"

    def __init__(self, primary_ref: Dict[str, str],
                 fallbacks: List[Tuple[Dict[str, str], Any]]) -> None:
        self.primary_ref = dict(primary_ref)
        self.fallbacks = list(fallbacks)

    def wrap_model(self, state: Any, bound: Any, rebind: Any) -> Any:
        if not self.fallbacks:
            return bound
        # Read by the switched model's callback (agents/loop_ext/steering.py):
        # a refusal is this chain's to handle, not an answer to record.
        state.scratch["fallback_active"] = True
        reason_holder: Dict[str, str] = {}
        # ``with_config`` replaces a binding's callbacks rather than adding to
        # them, so keep the ones an earlier extension attached (the switched
        # model's, agents/loop_ext/steering.py).
        earlier = (getattr(bound, "config", None) or {}).get("callbacks")
        earlier = list(earlier) if isinstance(earlier, list) else []
        primary = bound.with_config(
            callbacks=[*earlier, _CandidateCallback(state, self.primary_ref, True, reason_holder)])
        fallback_chains = []
        for ref, llm in self.fallbacks:
            fb_bound = rebind(llm)
            fallback_chains.append(fb_bound.with_config(
                callbacks=[_CandidateCallback(state, ref, False, reason_holder)]))
        return primary.with_fallbacks(fallback_chains, exceptions_to_handle=_retryable_exceptions())


def extension_for(agent: Any) -> Optional[LoopExtension]:
    spec = getattr(agent, "spec", None)
    catalog_ids = list(getattr(spec, "fallback_models", None) or []) if spec is not None else []
    if not catalog_ids:
        return None

    from tools.delegation import resolve_model
    from agents.agent_utils import build_chat_model

    try:
        primary_provider = (agent.effective_provider(getattr(agent, "_llm", None)) or "").strip().lower()
    except Exception:  # noqa: BLE001 - an unreadable provider still lets the primary model run
        primary_provider = (getattr(agent, "provider", "") or "").strip().lower()
    primary_ref = {
        "provider": primary_provider,
        "model": _llm_model_name(getattr(agent, "_llm", None)) or str(getattr(agent, "model", "") or ""),
    }

    built: List[Tuple[Dict[str, str], Any]] = []
    for catalog_id in catalog_ids:
        try:
            provider, model = resolve_model(catalog_id)
        except ValueError:
            log.warning("fallback: '%s' is not an enabled catalog model for agent '%s'",
                        catalog_id, getattr(agent, "agent_id", "?"))
            continue
        try:
            llm = build_chat_model(
                provider=provider, model=model,
                temperature=getattr(agent, "temperature", None),
                max_tokens=getattr(agent, "max_tokens", None),
                streaming=getattr(agent, "streaming", False),
            )
        except Exception:  # noqa: BLE001 - a fallback that fails to build is skipped, not fatal
            log.warning("fallback: could not build '%s' for agent '%s'",
                        catalog_id, getattr(agent, "agent_id", "?"), exc_info=True)
            continue
        built.append(({"provider": provider, "model": model}, llm))

    if not built:
        return None
    return FallbackExtension(primary_ref=primary_ref, fallbacks=built)
