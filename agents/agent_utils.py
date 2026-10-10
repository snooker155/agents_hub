from __future__ import annotations

import logging
from typing import Optional
from pathlib import Path

import os

from providers.openai_driver import OpenAIChatModel, summary_refused

from common.config import settings
from common.hostnet import host_service_url


def openai_reasoning_param(effort: str, api_key=None, base_url=None) -> dict:
    """The Responses API ``reasoning`` parameter for a thinking level.

    OpenAI never returns the raw reasoning of its models; asking for a summary
    is the only way any of it reaches the chat. A key refused one before
    (``providers.openai_driver`` remembers the refusal) is asked without it.
    """
    if summary_refused(api_key, base_url):
        return {"effort": effort}
    return {"effort": effort, "summary": "auto"}


#: The hub's own OpenAI-protocol model (providers/openai_driver.py). The old
#: name stays for callers that imported the subclass of ChatOpenAI it replaced.
ReasoningChatOpenAI = OpenAIChatModel


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


def is_openai_reasoning_model(model: Optional[str]) -> bool:
    """True for an OpenAI reasoning model. The ``*-chat-latest`` aliases of
    gpt-5 are chat models and reject any reasoning parameter."""
    mdl = (model or "").lower()
    return mdl.startswith(_OPENAI_REASONING_PREFIXES) and "-chat" not in mdl


def openai_reasoning_kwargs(
    model: Optional[str],
    thinking_level: Optional[str],
    temperature: Optional[float],
    *,
    api_key=None,
    base_url: Optional[str] = None,
    lenient: bool = False,
) -> dict:
    """Endpoint, reasoning and temperature kwargs for an OpenAI-protocol model.

    - A thinking level on a reasoning model asks for a summary of the
      reasoning, which only the Responses API returns; a model served by a
      gateway (``base_url``) stays on chat completions, which may be all the
      gateway serves.
    - Level ``off`` sends the lowest effort the model accepts: with nothing
      sent, OpenAI applies its own default, ``medium`` on most families
      (``providers.reasoning_profile``). At effort ``none`` the model takes a
      temperature again.
    - No level (a utility call that never chose one) sends nothing, and the
      model keeps its provider default.

    ``lenient`` passes a positive level on for any model: OpenAI-compatible
    servers ignore ``reasoning_effort`` where it is not supported, while the
    OpenAI API itself answers 400.
    """
    from providers.reasoning_profile import openai_off_effort

    level = (thinking_level or "").lower()
    effort = _LEVEL_EFFORT.get(level)
    reasoning_model = is_openai_reasoning_model(model)
    sends_effort = bool(effort) and (lenient or reasoning_model)
    off = openai_off_effort(model) if level == "off" else None
    responses = needs_responses_api(model) or (
        sends_effort and reasoning_model and not base_url)
    out: dict = {}
    if responses:
        # The driver rewrites the whole request for that endpoint:
        # max_tokens → max_output_tokens, and the tool schemas into their
        # Responses shape.
        out["use_responses_api"] = True
    if sends_effort:
        # Reasoning models reject a custom temperature, so it is omitted.
        if responses:
            out["reasoning"] = openai_reasoning_param(effort, api_key, base_url)
        else:
            out["reasoning_effort"] = effort
    elif off:
        if responses:
            out["reasoning"] = {"effort": off}
        else:
            out["reasoning_effort"] = off
        if off == "none" and temperature is not None:
            out["forced_temperature"] = temperature
    else:
        out["temperature"] = temperature
    return out

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
_ANTHROPIC_ADAPTIVE_MARKERS = ("opus-4-6", "opus-4-7", "opus-4-8", "opus-5", "sonnet-4-6", "sonnet-5",
                               "fable", "mythos")
# Anthropic models that reject a non-default temperature outright (Opus 4.7
# and later, Sonnet 5 and later, Fable, Mythos): the request leaves it out.
_ANTHROPIC_NO_SAMPLING_MARKERS = ("opus-4-7", "opus-4-8", "opus-5", "sonnet-5", "fable", "mythos")


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

    thinking_level: native model reasoning level ('low' | 'medium' | 'high'),
    'off' to hold the model to its lowest reasoning, or None to leave the
    provider's default (utility calls that never chose a level). When active, it is mapped to the provider's native
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
            except (OSError, ValueError):
                log.debug("build_chat_model: ignored error", exc_info=True)
        provider = os.environ.get("DEFAULT_PROVIDER") or _file_provider or settings.default_provider

    def _temp(mdl: Optional[str]) -> float:
        # The caller's (the agent's) value, else the one set for this model
        # on the Models page, else the global default.
        if temperature is not None:
            return temperature
        from providers.catalog import model_temperature
        own = model_temperature(provider, mdl)
        return own if own is not None else settings.temperature

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
                temperature=_temp(model or _backend.get("default_model")),
                max_tokens=max_tokens,
                streaming=streaming,
                thinking_level=thinking_level,
            )

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        url = host_service_url(
            base_url or os.environ.get("OLLAMA_BASE_URL") or settings.ollama_base_url)
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
            model=mdl, base_url=url, temperature=_temp(mdl), reasoning=reasoning,
            client_kwargs={"timeout": req_timeout} if req_timeout else None,
        )

    if provider == "lmstudio":
        url = host_service_url(
            base_url or os.environ.get("LMSTUDIO_BASE_URL") or settings.lmstudio_base_url).rstrip("/")
        mdl = model or os.environ.get("LMSTUDIO_MODEL") or settings.lmstudio_model
        if not mdl:
            raise ValueError("LM Studio model is not configured. Set LMSTUDIO_MODEL in Settings.")
        # The driver keeps the reasoning_content field that models like
        # gpt-oss return separately (no inline <think> tags). LM Studio honours
        # reasoning_effort for models that support it (gpt-oss) and ignores it
        # for the rest.
        return OpenAIChatModel(
            model=mdl,
            base_url=f"{url}/v1",
            api_key="lm-studio",  # LM Studio ignores the key value
            temperature=_temp(mdl),
            max_tokens=tok,
            streaming=streaming,
            reasoning_effort=effort,
            timeout=req_timeout,
        )

    if provider == "anthropic":
        from providers.anthropic_driver import AnthropicChatModel
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
            return AnthropicChatModel(model=mdl, api_key=key, max_tokens=tok, thinking=thinking,
                                      streaming=streaming,
                                      default_request_timeout=req_timeout)
        if any(m in mdl.lower() for m in _ANTHROPIC_NO_SAMPLING_MARKERS):
            return AnthropicChatModel(model=mdl, api_key=key, max_tokens=tok, streaming=streaming,
                                      default_request_timeout=req_timeout)
        return AnthropicChatModel(model=mdl, api_key=key, temperature=_temp(mdl), max_tokens=tok,
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
        return ChatGoogleGenerativeAI(model=mdl, google_api_key=key, temperature=_temp(mdl),
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
    # The driver asks for usage on streamed completions when the request goes
    # to OpenAI's own API and leaves the option out for a gateway, which may
    # reject it (StatsCollectorCallback estimates there). It also never reads
    # an empty ``OPENAI_BASE_URL`` the backend's .env seeding leaves behind.
    gateway = common["base_url"]
    common.update(openai_reasoning_kwargs(
        mdl, thinking_level, _temp(mdl),
        api_key=common["api_key"], base_url=gateway))
    return OpenAIChatModel(**common)


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

log = logging.getLogger(__name__)
