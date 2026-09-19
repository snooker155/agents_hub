"""
Failure classification shared by every chat surface.

An agent error reaches the browser as a string the provider wrote, and most of
them are only ever read ("tool failed", "connection reset"). One kind is
different: the conversation no longer fits the model's context window. That one
the user can act on — clear the chat, or move to a model with a bigger window —
but only if the UI can tell it apart from everything else, which is what the
``code`` this module attaches is for.

Both halves of the overflow case end up here: the guard that fires when the
backend accepted an over-sized prompt (``agents.callbacks.guards``) and the 400
the backend returns when it refuses one outright.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

#: Machine-readable code for "the accumulated history no longer fits".
CONTEXT_OVERFLOW = "context_overflow"


def error_code(error: str) -> Optional[str]:
    """The code for an agent error, or None when it is an ordinary failure."""
    from providers.context_windows import is_context_overflow
    return CONTEXT_OVERFLOW if is_context_overflow(error or "") else None


def error_event(source: str, error: str) -> Dict[str, Any]:
    """An ``error`` stream event, tagged with its code when it has one."""
    ev: Dict[str, Any] = {"type": "error", "source": source, "error": error}
    code = error_code(error)
    if code:
        ev["code"] = code
    return ev
