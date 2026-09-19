"""
Pluggable adapters for custom model backends.

An *adapter* maps a backend's wire protocol onto a concrete LangChain chat model.
The built-in ``openai`` adapter speaks the OpenAI Chat Completions API (``/v1``)
— which covers vLLM, TGI, OpenRouter, Together, Groq, Azure-style gateways,
LiteLLM, and most self-hosted servers — and is what makes these tool-calling
agents work, since they depend on OpenAI-style function calling.

To support a backend with a different shape, register another adapter::

    from providers.adapters import register_adapter, Adapter

    def _build_myproto(backend, *, model, temperature, max_tokens, streaming,
                       thinking_level):
        return MyChatModel(...)

    register_adapter(Adapter(
        kind="myproto", label="My Protocol",
        builder=_build_myproto, openai_compatible=False,
    ))

``openai_compatible`` tells the Settings probe whether the backend can be
auto-discovered via ``GET {base_url}/models``; non-compatible backends just have
their models entered by hand in the Models catalog.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

# thinking_level → OpenAI-style reasoning_effort. Kept local (not imported from
# agent_utils) so this module has no import-time dependency on the agent stack.
_LEVEL_EFFORT: Dict[str, str] = {"low": "low", "medium": "medium", "high": "high"}


@dataclass
class Adapter:
    """One way to turn a custom backend into a LangChain chat model.

    ``builder(backend, *, model, temperature, max_tokens, streaming,
    thinking_level)`` returns the chat model. ``openai_compatible`` marks
    backends whose model list can be probed at ``GET {base_url}/models``.
    """
    kind: str
    label: str
    builder: Callable[..., Any]
    openai_compatible: bool = True


_ADAPTERS: Dict[str, Adapter] = {}


def register_adapter(adapter: Adapter) -> Adapter:
    """Register (or replace) an adapter by its ``kind``."""
    _ADAPTERS[adapter.kind] = adapter
    return adapter


def get_adapter(kind: str) -> Optional[Adapter]:
    return _ADAPTERS.get((kind or "").strip())


def list_adapters() -> List[Dict[str, Any]]:
    """Adapters as ``{kind, label, openai_compatible}`` dicts for the UI."""
    return [
        {"kind": a.kind, "label": a.label, "openai_compatible": a.openai_compatible}
        for a in _ADAPTERS.values()
    ]


def build_custom_model(
    backend: Dict[str, Any],
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    streaming: bool = False,
    thinking_level: Optional[str] = None,
):
    """Build the LangChain chat model for ``backend`` via its adapter.

    ``model`` falls back to the backend's ``default_model``. Raises ValueError for
    an unknown adapter or when no model can be resolved.
    """
    adapter = get_adapter(str(backend.get("adapter") or "openai"))
    if adapter is None:
        raise ValueError(f"Unknown adapter '{backend.get('adapter')}' for backend '{backend.get('id')}'.")
    mdl = model or backend.get("default_model") or ""
    if not mdl:
        raise ValueError(
            f"No model specified for custom backend '{backend.get('id')}'. "
            "Pick a model or set the backend's default model."
        )
    return adapter.builder(
        backend,
        model=mdl,
        temperature=temperature,
        max_tokens=max_tokens,
        streaming=streaming,
        thinking_level=thinking_level,
    )


# ── Built-in: OpenAI-compatible adapter ───────────────────────────────────────

def _build_openai_compatible(
    backend: Dict[str, Any],
    *,
    model: str,
    temperature: Optional[float],
    max_tokens: Optional[int],
    streaming: bool,
    thinking_level: Optional[str],
):
    # ReasoningChatOpenAI preserves servers' separate ``reasoning_content`` field
    # (gpt-oss et al.). Imported lazily so this module stays import-cheap and free
    # of an agent-stack dependency at load time.
    from agents.agent_utils import ReasoningChatOpenAI, needs_responses_api
    from common.config import settings

    base_url = (backend.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise ValueError(f"Custom backend '{backend.get('id')}' has no base URL.")
    temp = temperature if temperature is not None else settings.temperature
    tok = max_tokens if max_tokens is not None else settings.max_tokens
    effort = _LEVEL_EFFORT.get((thinking_level or "").lower())
    headers = backend.get("headers") or {}

    kwargs: Dict[str, Any] = dict(
        model=model,
        base_url=base_url,
        # Many keyless gateways still require a non-empty key; send a placeholder.
        api_key=(backend.get("api_key") or "").strip() or "not-needed",
        max_tokens=tok,
        streaming=streaming,
    )
    if headers:
        kwargs["default_headers"] = {str(k): str(v) for k, v in headers.items()}
    if effort:
        # Reasoning models reject a custom temperature; OpenAI-compatible servers
        # ignore reasoning_effort when unsupported.
        kwargs["reasoning_effort"] = effort
    else:
        kwargs["temperature"] = temp
    if needs_responses_api(model):
        # Models that only do tool calls on /v1/responses; a gateway serving one
        # proxies that endpoint too.
        kwargs["use_responses_api"] = True
    return ReasoningChatOpenAI(**kwargs)


register_adapter(Adapter(
    kind="openai",
    label="OpenAI-compatible (/v1)",
    builder=_build_openai_compatible,
    openai_compatible=True,
))
