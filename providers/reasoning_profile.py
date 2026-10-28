"""What a model does with reasoning when nobody asks, and which efforts it takes.

Effort is a model setting: the agent's Model tab, the header's model picker
(for agents that inherit the model) and the Models page (the model's own
default) each set it, in that order of priority. What a value may be depends on
the model, so every known model has a profile: the efforts it accepts, the one
its provider applies when the request names none, and what thinking level
``off`` sends.

``off`` has to mean what it says. OpenAI's reasoning models do not stop
reasoning when the request carries no effort: each family falls back to its own
default, and gpt-5, gpt-5.5, gpt-5.6 and the o-series reason at ``medium`` then,
billed and invisible. So ``off`` is sent as the lowest effort the model accepts.

The OpenAI table was measured against the API on 2026-10-05 (the effort a
response reports back with no ``reasoning`` in the request, and which efforts a
request is accepted with):

=====================  ==========  ===========================
family                 default     lowest accepted
=====================  ==========  ===========================
gpt-5, -mini, -nano    medium      minimal
gpt-5.1 .. gpt-5.4     none        none
gpt-5.5, gpt-5.6       medium      none
o1, o3, o4             medium      low
*-pro                  high        high (nothing lower)
=====================  ==========  ===========================

Temperature is accepted only with effort ``none``; any other effort answers
400 "Unsupported parameter: 'temperature'". ``xhigh`` arrived with gpt-5.2.
Families after 5.6 (gpt-6 and on) are assumed to behave like 5.5 and 5.6 until
measured.

Claude takes ``output_config.effort`` with adaptive thinking (Opus 4.6 and
later, Sonnet 4.6 and later, Fable). On Opus 4.6 to 4.8 and Sonnet 4.6 no
thinking is requested unless asked for, so ``off`` is a real choice there. Opus
5, Opus 5.5, Sonnet 5, Sonnet 5.5 and Fable think by default (on Opus 5.5,
Sonnet 5.5 and Fable it cannot be turned off at all), so their lowest choice is
``low``. Older Claude models think on a token budget sized from the level.

Local servers (Ollama's reasoning families, LM Studio) and custom backends
whose model is not an OpenAI one get the generic off/low/medium/high the hub
always sent them. Google is not described (None): its models get no effort.
"""
from __future__ import annotations

import re
from typing import List, Optional, TypedDict


class ReasoningProfile(TypedDict):
    default: str            # the effort the provider applies with none requested
    off: Optional[str]      # what the hub sends for thinking level off; None = nothing
    temperature: str        # "always" | "when_off" | "never"
    efforts: List[str]      # the values the model accepts, lowest first


#: Every effort any model takes, lowest first. Clamping walks this order.
EFFORT_ORDER = ("off", "none", "minimal", "low", "medium", "high", "xhigh", "max")

_GENERIC = ["off", "low", "medium", "high"]


def _profile(default: str, off: Optional[str], temperature: str, efforts: List[str]) -> ReasoningProfile:
    return {"default": default, "off": off, "temperature": temperature, "efforts": list(efforts)}


_GPT_RE = re.compile(r"^gpt-(\d+)(?:\.(\d+))?(?:$|-)")


def openai_gpt_version(model: Optional[str]) -> Optional[tuple[int, int]]:
    """(major, minor) of a GPT model id ("gpt-5.6-sol" → (5, 6), "gpt-6-luna"
    → (6, 0)), matched after a vendor segment as well; None for anything else."""
    match = _GPT_RE.match((model or "").lower().rpartition("/")[2])
    if not match:
        return None
    return int(match.group(1)), int(match.group(2) or 0)


def _openai_profile(model: str) -> Optional[ReasoningProfile]:
    mdl = (model or "").lower().rpartition("/")[2]
    if "-chat" in mdl:
        # gpt-5-chat-latest and the like: chat models, no reasoning parameter.
        return None
    if mdl.startswith(("o1", "o3", "o4")):
        return _profile("medium", "low", "never", ["low", "medium", "high"])
    version = openai_gpt_version(mdl)
    if not version or version < (5, 0):
        return None
    if "-pro" in mdl:
        return _profile("high", None, "never", ["high"])
    if version == (5, 0):
        return _profile("medium", "minimal", "never", ["minimal", "low", "medium", "high"])
    if version == (5, 1):
        return _profile("none", "none", "when_off", ["none", "low", "medium", "high"])
    top = ["none", "low", "medium", "high", "xhigh"]
    if version <= (5, 4):
        return _profile("none", "none", "when_off", top)
    return _profile("medium", "none", "when_off", top)


def _claude_profile(model: str) -> ReasoningProfile:
    mdl = (model or "").lower()
    five = ["low", "medium", "high", "xhigh", "max"]
    # Thinking always on (Opus 5.5, Sonnet 5.5, Fable, Mythos) or on by
    # default (Opus 5, Sonnet 5): low is as far down as it goes.
    if "opus-5-5" in mdl:
        return _profile("medium", "low", "never", five)
    if any(m in mdl for m in ("sonnet-5-5", "fable", "mythos")):
        return _profile("high", "low", "never", five)
    if any(m in mdl for m in ("opus-5", "sonnet-5")):
        return _profile("high", "low", "never", five)
    # Adaptive thinking on request only.
    if any(m in mdl for m in ("opus-4-7", "opus-4-8")):
        return _profile("off", None, "never", ["off", *five])
    if any(m in mdl for m in ("opus-4-6", "sonnet-4-6")):
        return _profile("off", None, "when_off", ["off", "low", "medium", "high", "max"])
    # Haiku 4.5 and older: a token budget sized from the level.
    return _profile("off", None, "when_off", _GENERIC)


# Ollama models known to support think mode; Ollama errors on `think=true`
# for models without it, so anything unrecognised gets no effort.
OLLAMA_REASONING_MARKERS = ("qwen3", "deepseek-r1", "qwq", "gpt-oss", "magistral")


def reasoning_profile(provider: Optional[str], model: Optional[str]) -> Optional[ReasoningProfile]:
    """The reasoning profile of ``model``, or None when it takes no effort.

    ``provider`` is the hub's provider id. A custom backend that speaks the
    OpenAI protocol serves OpenAI's models under their own ids (often
    vendor-prefixed), so any provider other than the known ones is matched
    against the OpenAI table first, and gets the generic levels otherwise.
    """
    prov = (provider or "openai").lower()
    if prov == "anthropic":
        return _claude_profile(model or "")
    if prov == "google":
        return None
    if prov == "ollama":
        mdl = (model or "").lower()
        return _profile("off", None, "always", _GENERIC) if any(m in mdl for m in OLLAMA_REASONING_MARKERS) else None
    if prov == "lmstudio":
        return _profile("off", None, "always", _GENERIC)
    own = _openai_profile(model or "")
    if own or prov == "openai":
        return own
    return _profile("off", None, "always", _GENERIC)


def clamp_effort(profile: Optional[ReasoningProfile], value: Optional[str]) -> Optional[str]:
    """``value`` as the model can take it: unchanged when it accepts it, else
    the nearest accepted effort below, else its lowest. ``off`` is what the
    profile sends for off (None = nothing). None when there is nothing to send:
    no value, no profile, or an unknown word."""
    level = (value or "").strip().lower()
    if not profile or level not in EFFORT_ORDER:
        return None
    if level == "off":
        return profile["off"]
    efforts = profile["efforts"]
    if level in efforts:
        return level
    rank = EFFORT_ORDER.index(level)
    below = [e for e in efforts if EFFORT_ORDER.index(e) < rank]
    return below[-1] if below else efforts[0]


def openai_off_effort(model: Optional[str]) -> Optional[str]:
    """The effort that stands for thinking level off on an OpenAI model, or
    None when nothing should be sent (not a reasoning model, or one that cannot
    go lower than its default)."""
    profile = _openai_profile(model or "")
    return profile["off"] if profile else None


def effective_effort(provider: Optional[str], model: Optional[str], *,
                     agent: Optional[str] = None, workspace: Optional[str] = None,
                     model_inherited: bool = True) -> dict:
    """Which effort a model is run at and where it comes from, for the pages
    that show it (the agent's Model tab, the header's picker).

    The chain the runtime walks (agents/agent_factory.py, build_chat_model):
    the agent's own; the header's choice for the workspace when the agent
    inherits the model too; the model's own on the Models page; the provider's
    default. ``value`` is the requested one clamped to what the model accepts.
    """
    from providers.catalog import model_effort
    profile = reasoning_profile(provider, model)
    requested, source = None, None
    for value, where in ((agent, "agent"), (workspace if model_inherited else None, "workspace"),
                         (model_effort(provider, model), "model")):
        if value and value in EFFORT_ORDER:
            requested, source = value, where
            break
    if requested is None and profile:
        requested, source = profile["default"], "provider"
    value = clamp_effort(profile, requested) if profile else None
    if requested == "off" and value is None and profile:
        value = "off"
    return {"provider": provider, "model": model, "requested": requested, "value": value,
            "source": source, "profile": profile}
