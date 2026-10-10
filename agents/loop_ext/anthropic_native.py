"""
What the loop extensions may ask of Anthropic's API directly.

Two of the loop's policies have a server-side counterpart on Anthropic models:

- compaction: the context editing strategy ``clear_tool_uses_20250919`` (beta
  ``context-management-2025-06-27``) clears old tool results on the server,
  and a cleared request does not count as an edited history, which matters on
  models that bind their thinking blocks to the conversation (a client that
  rewrites an old tool result there invalidates every later thinking block);
- tool search: tools declared with ``defer_loading: true`` stay out of the
  model's context until a ``tool_reference`` names them, so the ``tools``
  array can be the same on every request of a run (cache friendly, and again
  no edited history) while the model still sees a short list.

Both reach the API through the hub's own driver
(``providers.anthropic_driver``): ``bind(betas=[...], context_management={...})``
sends the request to the beta endpoint with the header and the body field; a
dict tool in Anthropic's own shape keeps ``defer_loading``; a ``tool_reference``
block inside a tool result's content is sent unchanged; the
``context_management.applied_edits`` report arrives in ``response_metadata``
on streamed and unstreamed calls alike. The hub's ``search_tools`` answers
with ``tool_reference`` blocks (the API's documented custom tool search)
rather than Anthropic's own tool search tool, so the catalogue search stays
the same on every provider.
"""
from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple

#: Beta header of Anthropic's context editing.
CONTEXT_MANAGEMENT_BETA = "context-management-2025-06-27"
#: The edit that clears old tool results.
CLEAR_TOOL_USES = "clear_tool_uses_20250919"

_FAMILY = re.compile(r"(opus|sonnet|haiku)-(\d{1,2})(?!\d)(?:-(\d{1,2})(?!\d))?")


def is_anthropic_client(llm: Any) -> bool:
    """True for the built-in Anthropic chat model (not a gateway that speaks
    another protocol under an Anthropic model name)."""
    from providers.anthropic_driver import AnthropicChatModel
    return isinstance(llm, AnthropicChatModel)


def model_id(llm: Any) -> str:
    return str(getattr(llm, "model", None) or getattr(llm, "model_name", None) or "").lower()


def _generation(model: str) -> Optional[Tuple[str, int, int]]:
    """``(family, major, minor)`` of a Claude model id, None when unknown.

    ``claude-sonnet-4-20250514`` reads as ``(sonnet, 4, 0)``: a date suffix is
    not a minor version. The Claude 3 names (``claude-3-5-sonnet-20241022``)
    do not match at all.
    """
    m = _FAMILY.search(model or "")
    if not m:
        return None
    return m.group(1), int(m.group(2)), int(m.group(3) or 0)


def supports_context_editing(model: str) -> bool:
    """Context editing: the Claude 4 generation on (Haiku from 4.5), and
    Fable and Mythos; not Claude 3."""
    model = (model or "").lower()
    if "fable" in model or "mythos" in model:
        return True
    gen = _generation(model)
    if gen is None:
        return False
    family, major, minor = gen
    if family == "haiku":
        return (major, minor) >= (4, 5)
    return major >= 4


def supports_tool_reference(model: str) -> bool:
    """Deferred tools and ``tool_reference``: Opus, Sonnet and Haiku from 4.5,
    Fable and Mythos."""
    model = (model or "").lower()
    if "fable" in model or "mythos" in model:
        return True
    gen = _generation(model)
    return gen is not None and (gen[1], gen[2]) >= (4, 5)


def betas_with(llm: Any, beta: str) -> List[str]:
    """The model's own beta flags plus *beta*: binding ``betas`` replaces the
    model's list, so what it already sends has to travel along."""
    betas = [str(b) for b in (getattr(llm, "betas", None) or [])]
    if beta not in betas:
        betas.append(beta)
    return betas


__all__ = [
    "CLEAR_TOOL_USES",
    "CONTEXT_MANAGEMENT_BETA",
    "betas_with",
    "is_anthropic_client",
    "model_id",
    "supports_context_editing",
    "supports_tool_reference",
]
