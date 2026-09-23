"""
Model context-window resolution.

The catalog (``providers.catalog``, database-backed, formerly ``models.json``)
stores an optional ``context_window`` (max input
tokens) per model. It is populated three ways, in order of trust:

1. **User override** in the Models page catalog — always wins.
2. **Backend-reported** values captured during model discovery. Gemini reports
   ``inputTokenLimit``; OpenAI-compatible gateways report ``context_length``
   (OpenRouter) or ``max_model_len`` (vLLM); Ollama reports the architecture's
   ``context_length`` via ``/api/show``; LM Studio reports
   ``max_context_length`` via ``/api/v0/models``. What the backend reports is
   preferred over model-card numbers, since gateways/local servers often serve
   a smaller window than the model was trained for.
3. **Static fallback** for providers whose APIs expose nothing (OpenAI,
   Anthropic): the optional ``litellm`` package's maintained metadata when
   installed, else the small built-in table below.

``get_model_context_window(provider, model)`` is the runtime entry point; it
reads the catalog first and falls back to (3). Returns 0 when unknown.
"""
from __future__ import annotations

from providers.catalog import load_catalog_raw

# Built-in fallback (max input tokens), matched by substring against the model
# id, most-specific first — same convention as the pricing defaults. Indicative
# values; users can override any of them in the Models catalog. Anthropic and
# Gemini values are normally discovered live (their model APIs report the
# window); this table is the last resort.
#   (provider, substring) -> context_window
FALLBACK_CONTEXT_WINDOWS: list[tuple[str, str, int]] = [
    # ── OpenAI ──
    ("openai", "gpt-5", 400_000),
    ("openai", "gpt-4.1", 1_047_576),
    ("openai", "gpt-4o", 128_000),
    ("openai", "gpt-4-turbo", 128_000),
    ("openai", "gpt-4", 8_192),
    ("openai", "gpt-3.5-turbo-16k", 16_385),
    ("openai", "gpt-3.5", 16_385),
    ("openai", "o4-mini", 200_000),
    ("openai", "o3", 200_000),
    ("openai", "o1", 200_000),
    # ── Anthropic ── (1M: Fable/Mythos 5, Opus 4.5+, Sonnet 4.6+; 200K: Haiku 4.5 and older)
    ("anthropic", "fable", 1_000_000),
    ("anthropic", "mythos", 1_000_000),
    ("anthropic", "opus-4-8", 1_000_000),
    ("anthropic", "opus-4-7", 1_000_000),
    ("anthropic", "opus-4-6", 1_000_000),
    ("anthropic", "opus-4-5", 1_000_000),
    ("anthropic", "sonnet-5", 1_000_000),
    ("anthropic", "sonnet-4-6", 1_000_000),
    ("anthropic", "claude", 200_000),
    # ── Google ──
    ("google", "gemini-1.5-pro", 2_097_152),
    ("google", "gemini", 1_048_576),
]


def _litellm_context_window(model: str) -> int:
    """Max input tokens from litellm's model metadata, 0 if unavailable.

    litellm is optional — it ships a maintained per-model map covering the
    cloud providers whose APIs do not expose context windows.
    """
    try:
        import litellm
        litellm.suppress_debug_info = True  # no console banner on unknown models
        info = litellm.get_model_info(model)
        return int(info.get("max_input_tokens") or info.get("max_tokens") or 0)
    except Exception:
        return 0


def fallback_context_window(provider: str, model: str) -> int:
    """Best-effort context window when the backend reports nothing."""
    n = _litellm_context_window(model)
    if n:
        return n
    mid = (model or "").lower()
    for prov, sub, window in FALLBACK_CONTEXT_WINDOWS:
        if prov == provider and sub in mid:
            return window
    return 0


def get_model_context_window(provider: str, model: str) -> int:
    """Context window (max input tokens) for a model, 0 when unknown.

    Catalog value (user-set or discovered) first, then the static fallback.
    Safe to call from the agent runtime — reads the catalog directly and
    never raises.
    """
    try:
        catalog = load_catalog_raw()
        if isinstance(catalog, dict):
            entry = catalog.get(provider) or {}
            for m in entry.get("models", []):
                if m.get("id") == model:
                    n = int(m.get("context_window") or 0)
                    if n:
                        return n
                    break
    except Exception:
        pass
    return fallback_context_window(provider, model)


# ── overflow detection ────────────────────────────────────────────────────────
# Substrings the providers use when a request no longer fits the window. The
# guard in ``agents.callbacks.guards`` catches the case the backend accepted
# (prompt over the window, silently truncated); this covers the other half —
# the request the backend rejected outright with a 400. Matched lowercase
# against the error text, so one classifier serves every chat surface.
_OVERFLOW_MARKERS: tuple[str, ...] = (
    "context window exceeded",      # our own ContextWindowGuard
    "context_length_exceeded",      # OpenAI
    "maximum context length",       # OpenAI
    "context length",               # OpenAI-compatible gateways
    "prompt is too long",           # Anthropic
    "input is too long",            # Anthropic (older wording)
    "exceeds the maximum number of tokens",  # Gemini
    "too many tokens",
    "reduce the length of the messages",
    "requested tokens exceed",      # vLLM / OpenRouter
    "exceeds context window",       # Ollama / llama.cpp
    "available kv cache",           # llama.cpp: "exceeds the available KV cache size"
)


def is_context_overflow(error: str) -> bool:
    """True when an agent error means "the conversation no longer fits".

    Used by the chat surfaces to turn an opaque provider error into an explicit
    "context is full" state with a Clear-chat way out, instead of a red string
    the user cannot act on.
    """
    text = (error or "").lower()
    return any(marker in text for marker in _OVERFLOW_MARKERS)
