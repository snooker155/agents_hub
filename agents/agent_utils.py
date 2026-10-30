from __future__ import annotations

import logging
from typing import Any, Dict, Optional
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


# ── Native reasoning (effort → model API) ─────────────────────────────────────
# An agent's effort (``thinking_level`` down here, the name the model stack has
# always used) is a model parameter, separate from the think scratchpad tool:
# it sets how much the provider's model reasons in the API itself. Any value of
# providers.reasoning_profile.EFFORT_ORDER may arrive; each provider gets it
# clamped to what its model accepts. Models without a reasoning knob are left
# untouched.

# The levels a gateway or local server is passed as they are: those servers
# ignore reasoning_effort where it is not supported, the OpenAI API answers 400.
_LENIENT_LEVELS = ("low", "medium", "high")

# Efforts at which an OpenAI model reasons little enough that no summary is
# asked for.
_NO_SUMMARY = ("none", "minimal")


def is_openai_reasoning_model(model: Optional[str]) -> bool:
    """True for an OpenAI reasoning model (o-series, gpt-5 and later). The
    ``*-chat-latest`` aliases of gpt-5 are chat models and reject any
    reasoning parameter."""
    from providers.reasoning_profile import _openai_profile

    mdl = (model or "").lower()
    return mdl.startswith(("o1", "o3", "o4", "gpt-")) and _openai_profile(mdl) is not None


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

    - The effort is clamped to what the model accepts
      (providers.reasoning_profile): ``xhigh`` asked of gpt-5 is sent as
      ``high``, ``off`` as the model's lowest. With nothing sent, OpenAI
      applies its own default, ``medium`` on most families.
    - An effort the model reasons at asks for a summary of the reasoning,
      which only the Responses API returns; a model served by a gateway
      (``base_url``) stays on chat completions, which may be all the gateway
      serves.
    - At effort ``none`` the model takes a temperature again.
    - No level (a utility call that never chose one) sends nothing, and the
      model keeps its provider default.

    ``lenient`` passes low/medium/high on for any model the OpenAI table does
    not know: OpenAI-compatible servers ignore ``reasoning_effort`` where it is
    not supported, while the OpenAI API itself answers 400.
    """
    from providers.reasoning_profile import _openai_profile, clamp_effort

    level = (thinking_level or "").lower()
    profile = _openai_profile(model or "")
    effort = clamp_effort(profile, level) if profile else None
    if not profile and lenient and level in _LENIENT_LEVELS:
        effort = level
    # Off is the model held to its lowest, not reasoning to be shown.
    summary = (bool(effort) and effort not in _NO_SUMMARY and level != "off"
               and profile is not None)
    responses = needs_responses_api(model) or (summary and not base_url)
    out: dict = {}
    if responses:
        # The driver rewrites the whole request for that endpoint:
        # max_tokens → max_output_tokens, and the tool schemas into their
        # Responses shape.
        out["use_responses_api"] = True
    if effort:
        if responses:
            out["reasoning"] = (openai_reasoning_param(effort, api_key, base_url)
                                if summary else {"effort": effort})
        else:
            out["reasoning_effort"] = effort
        # A reasoning model rejects a custom temperature, except at effort
        # none, so it is omitted.
        if effort == "none" and temperature is not None:
            out["forced_temperature"] = temperature
    else:
        out["temperature"] = temperature
    return out


def default_model_for(provider: Optional[str]) -> Optional[str]:
    """The model build_chat_model falls back to for ``provider`` when none is
    named (the global default from Settings / the Models page)."""
    prov = (provider or "").lower()
    if prov == "anthropic":
        return os.environ.get("ANTHROPIC_MODEL") or "claude-opus-4-6"
    if prov == "google":
        return os.environ.get("GOOGLE_MODEL") or "gemini-2.0-flash"
    if prov == "ollama":
        return os.environ.get("OLLAMA_MODEL") or settings.ollama_model or None
    if prov == "lmstudio":
        return os.environ.get("LMSTUDIO_MODEL") or settings.lmstudio_model or None
    if prov in ("", "openai"):
        return os.environ.get("OPENAI_MODEL") or settings.model
    try:
        from providers import get_backend
        backend = get_backend(prov)
        return (backend or {}).get("default_model") or None
    except Exception:  # noqa: BLE001 - an unknown backend names no model
        return None


def resolve_inherited_effort(provider: Optional[str], model: Optional[str],
                             level: Optional[str]) -> Optional[str]:
    """``level``, with ``"inherit"`` (an agent that set no effort of its own)
    replaced by the model's default on the Models page, else its provider's
    (providers.reasoning_profile), else None (nothing sent)."""
    if (level or "").strip().lower() != "inherit":
        return level
    from providers.catalog import model_effort
    from providers.reasoning_profile import reasoning_profile
    own = model_effort(provider, model)
    if own:
        return own
    profile = reasoning_profile(provider, model)
    return profile["default"] if profile else None


# Models that reason by default and reject that combined with function tools on
# /v1/chat/completions ("use /v1/responses or set reasoning_effort to 'none'").
# The 400 lands even when the request carries no reasoning_effort of its own, so
# there is no thinking level low enough to stay on chat completions: every agent
# here is a tool-calling agent, and the Responses API is the endpoint that
# serves them. Turning reasoning off wholesale is the alternative, and a
# reasoning model with its reasoning disabled is the worse trade.
# gpt-5.6 is the first such family; every later one (gpt-6-luna and on) is
# assumed to keep the rule.
_OPENAI_RESPONSES_ONLY_FROM = (5, 6)


def needs_responses_api(model: Optional[str]) -> bool:
    """True when `model` can only do tool calls on the Responses API.

    Gateways publish the same model vendor-prefixed ("openai/gpt-5.6-sol"), so
    the id is matched after its vendor segment as well as whole.
    """
    from providers.reasoning_profile import openai_gpt_version

    mdl = (model or "").lower()
    if "-chat" in mdl:
        return False
    version = openai_gpt_version(mdl)
    return bool(version) and version >= _OPENAI_RESPONSES_ONLY_FROM


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

    thinking_level: the effort, any value of
    providers.reasoning_profile.EFFORT_ORDER ('off' holds the model to its
    lowest reasoning), or None to leave the provider's default (utility calls
    that never chose one). It is clamped to what the model accepts and mapped
    to the provider's native parameter (Anthropic output_config.effort or
    thinking budget, OpenAI/LM Studio reasoning effort, Ollama think mode).
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
    from providers.reasoning_profile import clamp_effort, reasoning_profile

    def _level(mdl: Optional[str]) -> Optional[str]:
        return resolve_inherited_effort(provider, mdl, thinking_level)

    def _effort(mdl: Optional[str]) -> Optional[str]:
        # The level as this provider's model can take it; "off" and "none"
        # mean no reasoning asked for.
        eff = clamp_effort(reasoning_profile(provider, mdl), _level(mdl))
        return None if eff in (None, "off", "none") else eff
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
                thinking_level=_level(model or _backend.get("default_model")),
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
        reasoning = True if _effort(mdl) else None
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
            reasoning_effort=_effort(mdl),
            timeout=req_timeout,
        )

    if provider == "anthropic":
        from providers.anthropic_driver import AnthropicChatModel
        key = api_key or os.environ.get("ANTHROPIC_API_KEY") or settings.anthropic_api_key
        mdl = model or os.environ.get("ANTHROPIC_MODEL") or "claude-opus-4-6"
        effort = _effort(mdl)
        if effort:
            extra = {}
            if any(m in mdl.lower() for m in _ANTHROPIC_ADAPTIVE_MARKERS):
                thinking = {"type": "adaptive"}
                extra["model_kwargs"] = {"output_config": {"effort": effort}}
            else:
                # Older models: explicit budget, which must stay below max_tokens.
                budget = 8192 if effort == "high" else 4096
                tok = max(tok, budget + 4096)
                thinking = {"type": "enabled", "budget_tokens": budget}
            # The API rejects a custom temperature when thinking is on
            # (and rejects temperature entirely on Opus 4.7+), so omit it.
            return AnthropicChatModel(model=mdl, api_key=key, max_tokens=tok, thinking=thinking,
                                      streaming=streaming,
                                      default_request_timeout=req_timeout, **extra)
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
        extra_google: Dict[str, Any] = {}
        from common import remote_state as _remote_state
        if _remote_state.is_remote():
            # Over REST, which the model gateway can carry (common/llm_gateway.py);
            # the default gRPC transport would go around it.
            extra_google["transport"] = "rest"
        return ChatGoogleGenerativeAI(model=mdl, google_api_key=key, temperature=_temp(mdl),
                                      timeout=req_timeout, **extra_google)

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
        mdl, _level(mdl), _temp(mdl),
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
