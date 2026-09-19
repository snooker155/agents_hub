from __future__ import annotations

from typing import Optional
from pathlib import Path

import os

from langchain_openai import ChatOpenAI

from common.config import settings


class ReasoningChatOpenAI(ChatOpenAI):
    """ChatOpenAI that preserves the reasoning of OpenAI-compatible servers.

    LM Studio (and other OpenAI-compatible servers) return native model
    reasoning as a separate ``reasoning_content`` / ``reasoning`` field on the
    message — gpt-oss never uses inline ``<think>`` tags, only this field.
    Base ChatOpenAI drops unknown response fields, so the reasoning is lost
    before any callback sees it. These overrides copy it into the message's
    ``additional_kwargs["reasoning_content"]``, where the chat callbacks and
    ``reasoning.native_reasoning`` already look for it.
    """

    @staticmethod
    def _delta_reasoning(payload: dict) -> str:
        reasoning = payload.get("reasoning_content") or payload.get("reasoning")
        return reasoning if isinstance(reasoning, str) else ""

    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info)
        try:
            response_dict = (
                response if isinstance(response, dict) else response.model_dump()
            )
            for gen, res in zip(result.generations, response_dict.get("choices") or []):
                reasoning = self._delta_reasoning((res or {}).get("message") or {})
                if reasoning.strip():
                    gen.message.additional_kwargs.setdefault("reasoning_content", reasoning)
        except Exception:
            pass
        return result

    def _convert_chunk_to_generation_chunk(
        self, chunk, default_chunk_class, base_generation_info
    ):
        gen_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if gen_chunk is None:
            return None
        try:
            choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
            if choices:
                reasoning = self._delta_reasoning((choices[0] or {}).get("delta") or {})
                if reasoning:
                    # Chunk merging concatenates string values in additional_kwargs,
                    # so the aggregated message carries the full reasoning text.
                    gen_chunk.message.additional_kwargs["reasoning_content"] = reasoning
        except Exception:
            pass
        return gen_chunk


# ── Native reasoning (thinking_level → model API) ─────────────────────────────
# An agent's ``thinking_level`` is a model parameter (decoupled from the think
# scratchpad tool) that turns on the provider's native reasoning so capable
# models think in the API itself. The mapping is best-effort: providers/models
# without a reasoning knob are left untouched.

# thinking_level → OpenAI-style effort level (also used to size Anthropic
# budgets). "off" / None disable native reasoning entirely.
_LEVEL_EFFORT = {"low": "low", "medium": "medium", "high": "high"}

# OpenAI models that accept `reasoning_effort`; other models 400 on the param.
_OPENAI_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5")

# Models that reason by default and reject that combined with function tools on
# /v1/chat/completions ("use /v1/responses or set reasoning_effort to 'none'").
# The 400 lands even when the request carries no reasoning_effort of its own, so
# there is no thinking level low enough to stay on chat completions: every agent
# here is a tool-calling agent, and the Responses API is the endpoint that
# serves them. Turning reasoning off wholesale is the alternative, and a
# reasoning model with its reasoning disabled is the worse trade.
_OPENAI_RESPONSES_ONLY_PREFIXES = ("gpt-5.6",)


def needs_responses_api(model: Optional[str]) -> bool:
    """True when `model` can only do tool calls on the Responses API.

    Gateways publish the same model vendor-prefixed ("openai/gpt-5.6-sol"), so
    the id is matched after its vendor segment as well as whole.
    """
    mdl = (model or "").lower()
    return (mdl.startswith(_OPENAI_RESPONSES_ONLY_PREFIXES)
            or mdl.rpartition("/")[2].startswith(_OPENAI_RESPONSES_ONLY_PREFIXES))

# Ollama models known to support think mode; Ollama errors on `think=true`
# for models without it, so anything unrecognised keeps the default behavior.
_OLLAMA_REASONING_MARKERS = ("qwen3", "deepseek-r1", "qwq", "gpt-oss", "magistral")

# Anthropic models on the adaptive-thinking API (budget_tokens is rejected
# there); older models still need `enabled` + an explicit token budget.
_ANTHROPIC_ADAPTIVE_MARKERS = ("opus-4-6", "opus-4-7", "opus-4-8", "sonnet-4-6", "fable")


def build_chat_model(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    streaming: bool = False,
    thinking_level: Optional[str] = None,
):
    """Build a LangChain chat model for the given provider.

    provider: 'openai' | 'anthropic' | 'google' | 'ollama' | 'lmstudio' | None/'inherit'
    Falls back to the global DEFAULT_PROVIDER env setting when not supplied.

    thinking_level: native model reasoning level ('low' | 'medium' | 'high'), or
    'off'/None to disable it. When active, it is mapped to the provider's native
    reasoning parameter (Anthropic thinking budget, OpenAI/LM Studio
    reasoning_effort, Ollama think mode) where the model supports it.
    """
    # Resolve provider: None / 'inherit' → use global DEFAULT_PROVIDER.
    # Read .env directly so already-running subprocesses pick up UI changes
    # (os.environ is frozen at subprocess-start time; .env is always current).
    if not provider or provider == "inherit":
        _dot_env = Path(__file__).resolve().parents[1] / ".env"
        _file_provider: str = ""
        if _dot_env.exists():
            try:
                for _ln in _dot_env.read_text(encoding="utf-8").splitlines():
                    _ln = _ln.strip()
                    if _ln.startswith("DEFAULT_PROVIDER") and "=" in _ln:
                        _file_provider = _ln.partition("=")[2].strip().strip('"\'')
                        break
            except Exception:
                pass
        provider = os.environ.get("DEFAULT_PROVIDER") or _file_provider or settings.default_provider

    temp = temperature if temperature is not None else settings.temperature
    tok = max_tokens if max_tokens is not None else settings.max_tokens
    effort = _LEVEL_EFFORT.get((thinking_level or "").lower())
    # Per-request timeout so a hung backend fails the run instead of blocking
    # it forever (0 or negative disables it).
    req_timeout = settings.llm_request_timeout if settings.llm_request_timeout > 0 else None

    # User-defined custom backends (Settings page). Resolved by id after the
    # built-ins, so a built-in provider name always wins. Lazily imported to keep
    # agent_utils free of an import-time dependency on the providers package.
    if provider and provider not in ("openai", "anthropic", "google", "ollama", "lmstudio"):
        from providers import get_backend, build_custom_model
        _backend = get_backend(provider)
        if _backend is not None:
            return build_custom_model(
                _backend,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                streaming=streaming,
                thinking_level=thinking_level,
            )

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        url = base_url or os.environ.get("OLLAMA_BASE_URL") or settings.ollama_base_url
        mdl = model or os.environ.get("OLLAMA_MODEL") or settings.ollama_model
        if not mdl:
            raise ValueError("Ollama model is not configured. Set OLLAMA_MODEL in Settings.")
        # `reasoning=True` makes Ollama return the thinking separately in
        # `reasoning_content` (which native_reasoning extraction picks up).
        # Ollama rejects think mode on models without it, so only opt in for
        # known reasoning families; None keeps the model's default behavior.
        reasoning = True if (
            effort and any(m in mdl.lower() for m in _OLLAMA_REASONING_MARKERS)
        ) else None
        return ChatOllama(
            model=mdl, base_url=url, temperature=temp, reasoning=reasoning,
            client_kwargs={"timeout": req_timeout} if req_timeout else None,
        )

    if provider == "lmstudio":
        url = (base_url or os.environ.get("LMSTUDIO_BASE_URL") or settings.lmstudio_base_url).rstrip("/")
        mdl = model or os.environ.get("LMSTUDIO_MODEL") or settings.lmstudio_model
        if not mdl:
            raise ValueError("LM Studio model is not configured. Set LMSTUDIO_MODEL in Settings.")
        # ReasoningChatOpenAI keeps the reasoning_content field that models
        # like gpt-oss return separately (no inline <think> tags).
        # LM Studio honours reasoning_effort for models that support it
        # (gpt-oss) and ignores it for the rest.
        return ReasoningChatOpenAI(
            model=mdl,
            base_url=f"{url}/v1",
            api_key="lm-studio",  # LM Studio ignores the key value
            temperature=temp,
            max_tokens=tok,
            streaming=streaming,
            reasoning_effort=effort,
            timeout=req_timeout,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        key = api_key or os.environ.get("ANTHROPIC_API_KEY") or settings.anthropic_api_key
        mdl = model or os.environ.get("ANTHROPIC_MODEL") or "claude-opus-4-6"
        if effort:
            if any(m in mdl.lower() for m in _ANTHROPIC_ADAPTIVE_MARKERS):
                thinking = {"type": "adaptive"}
            else:
                # Older models: explicit budget, which must stay below max_tokens.
                budget = 8192 if effort == "high" else 4096
                tok = max(tok, budget + 4096)
                thinking = {"type": "enabled", "budget_tokens": budget}
            # The API rejects a custom temperature when thinking is on
            # (and rejects temperature entirely on Opus 4.7+), so omit it.
            return ChatAnthropic(model=mdl, api_key=key, max_tokens=tok, thinking=thinking,
                                 streaming=streaming,
                                 default_request_timeout=req_timeout)
        return ChatAnthropic(model=mdl, api_key=key, temperature=temp, max_tokens=tok,
                             streaming=streaming,
                             default_request_timeout=req_timeout)

    if provider == "google":
        # No `streaming` here on purpose: the installed ChatGoogleGenerativeAI
        # exposes no such field, so agents on Google models answer in one
        # response even with the global streaming setting on (their runs stop at
        # LLM/tool boundaries rather than mid-completion). Same for Ollama above.
        from langchain_google_genai import ChatGoogleGenerativeAI
        key = api_key or os.environ.get("GOOGLE_API_KEY") or settings.google_api_key
        mdl = model or os.environ.get("GOOGLE_MODEL") or "gemini-2.0-flash"
        return ChatGoogleGenerativeAI(model=mdl, google_api_key=key, temperature=temp,
                                      timeout=req_timeout)

    # Default: OpenAI (or inherit global settings)
    mdl = model or os.environ.get("OPENAI_MODEL") or settings.model
    common = dict(
        model=mdl,
        max_tokens=tok,
        api_key=api_key or os.environ.get("OPENAI_API_KEY") or settings.openai_api_key,
        base_url=base_url or os.environ.get("OPENAI_BASE_URL") or None,
        streaming=streaming,
        timeout=req_timeout,
    )
    # `stream_usage` is deliberately not forced here. A streamed completion only
    # carries a usage block when `stream_options.include_usage` is requested, and
    # langchain-openai already turns that on by itself — but only when the model
    # points at the real OpenAI API, because a custom base_url means a gateway
    # that may reject the option. Overriding that would trade exact token counts
    # on OpenAI (already handled) for broken requests elsewhere; where usage is
    # missing, StatsCollectorCallback falls back to its own estimate.
    if effort and (mdl or "").lower().startswith(_OPENAI_REASONING_PREFIXES):
        # Reasoning models accept reasoning_effort but reject a custom
        # temperature, so omit it.
        common["reasoning_effort"] = effort
    else:
        common["temperature"] = temp
    if needs_responses_api(mdl):
        # langchain-openai rewrites the whole request for that endpoint:
        # max_tokens → max_output_tokens, reasoning_effort → reasoning.effort,
        # and the tool schemas into their Responses shape.
        common["use_responses_api"] = True
    return ChatOpenAI(**common)


# ── Callbacks moved to agents/callbacks/ ──────────────────────────────────────
# Re-exported here for backward compatibility with existing imports
# (`from agents.agent_utils import SharedProgressCallback`, etc.).
from agents.callbacks.control import (  # noqa: E402,F401
    SharedProgressCallback,
    RunStopCallback,
)
from agents.callbacks.guards import (  # noqa: E402,F401
    ToolRepetitionGuard,
    ToolRepetitionError,
)
