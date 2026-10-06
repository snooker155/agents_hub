"""What a model does with reasoning when nobody asks, and when it is told off.

An agent's thinking level ``off`` has to mean what it says. OpenAI's reasoning
models do not stop reasoning when the request carries no effort: each family
falls back to its own default, and gpt-5, gpt-5.5, gpt-5.6 and the o-series
reason at ``medium`` then, billed and invisible. So ``off`` is sent as the
lowest effort the model accepts, and the Models page shows both values.

The table was measured against the API on 2026-10-05 (the effort a response
reports back with no ``reasoning`` in the request, and which efforts a request
is accepted with):

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
400 "Unsupported parameter: 'temperature'". Families after 5.6 are assumed to
behave like 5.5 and 5.6 until measured.

Claude does not think unless thinking is requested, so its default is off and
off needs nothing sent. Other providers are not described (None).
"""
from __future__ import annotations

import re
from typing import Optional, TypedDict


class ReasoningProfile(TypedDict):
    default: str            # the effort the provider applies with none requested
    off: Optional[str]      # what the hub sends for thinking level off; None = nothing
    temperature: str        # "always" | "when_off" | "never"


_GPT5_RE = re.compile(r"^gpt-5(?:\.(\d+))?(?:$|-)")


def _openai_profile(model: str) -> Optional[ReasoningProfile]:
    mdl = (model or "").lower().rpartition("/")[2]
    if "-chat" in mdl:
        # gpt-5-chat-latest and the like: chat models, no reasoning parameter.
        return None
    if mdl.startswith(("o1", "o3", "o4")):
        return {"default": "medium", "off": "low", "temperature": "never"}
    match = _GPT5_RE.match(mdl)
    if not match:
        return None
    if "-pro" in mdl:
        return {"default": "high", "off": None, "temperature": "never"}
    minor = int(match.group(1) or 0)
    if minor == 0:
        return {"default": "medium", "off": "minimal", "temperature": "never"}
    if minor <= 4:
        return {"default": "none", "off": "none", "temperature": "when_off"}
    return {"default": "medium", "off": "none", "temperature": "when_off"}


def reasoning_profile(provider: Optional[str], model: Optional[str]) -> Optional[ReasoningProfile]:
    """The reasoning defaults of ``model``, or None when they are not known.

    ``provider`` is the hub's provider id. A custom backend that speaks the
    OpenAI protocol serves OpenAI's models under their own ids (often
    vendor-prefixed), so any provider other than the known non-OpenAI ones is
    matched against the OpenAI table.
    """
    prov = (provider or "openai").lower()
    if prov == "anthropic":
        return {"default": "off", "off": None, "temperature": "when_off"}
    if prov in ("google", "ollama", "lmstudio"):
        return None
    return _openai_profile(model or "")


def openai_off_effort(model: Optional[str]) -> Optional[str]:
    """The effort that stands for thinking level off on an OpenAI model, or
    None when nothing should be sent (not a reasoning model, or one that cannot
    go lower than its default)."""
    profile = _openai_profile(model or "")
    return profile["off"] if profile else None
