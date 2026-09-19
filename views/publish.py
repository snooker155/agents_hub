"""
Bridge from a parsed structured response to its transport payload.

A chat run ends with an optional structured response (an ``AgentResponse``).
For most kinds the transport payload is just ``response_obj.to_payload()``. For
a :class:`~agents.agent_response.ViewResponse` the inline spec is persisted
through the view store and replaced with a lightweight ``view_ref`` — so the
client and the run record carry a reference, never the full spec.

Kept out of ``agents.agent_response`` (a pure parsing module) and out of the
chat pipeline (called from several surfaces: web chat, flow nodes, Telegram).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

log = logging.getLogger("views.publish")


def publish_structured_response(
    response_obj: Any,
    *,
    workspace: Optional[str] = None,
    run_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return the transport payload for a parsed structured response.

    - ``ViewResponse`` → persist the view and return its ``view_ref``. If the
      view fails validation, log a warning and return ``None`` so the reply
      degrades to its plain text instead of leaking a broken view.
    - any other ``AgentResponse`` → ``response_obj.to_payload()``.
    - ``None`` → ``None``.
    """
    if response_obj is None:
        return None

    # Imported lazily: this module is imported from the chat pipeline, and the
    # agents package pulls in heavier deps we don't want on that import path.
    from agents.agent_response import ViewResponse

    if isinstance(response_obj, ViewResponse):
        from views.models import ViewValidationError
        from views.store import create_view, view_ref
        try:
            env = create_view(
                **_split_envelope(response_obj.to_envelope()),
                workspace=workspace,
                run_id=run_id,
                task_id=task_id,
            )
        except ViewValidationError as exc:
            log.warning("inline view rejected (%s); degrading to plain text", exc)
            return None
        except Exception:
            log.exception("failed to persist inline view; degrading to plain text")
            return None
        return view_ref(env)

    to_payload = getattr(response_obj, "to_payload", None)
    if callable(to_payload):
        try:
            return to_payload()
        except Exception:
            log.exception("to_payload() failed for %r", type(response_obj).__name__)
            return None
    return None


def _split_envelope(env: Dict[str, Any]) -> Dict[str, Any]:
    """Map a raw view envelope dict onto ``create_view``'s keyword arguments."""
    return {
        "kind": env.get("kind", ""),
        "title": env.get("title", ""),
        "spec": env.get("spec") or {},
        "summary": env.get("summary", ""),
        "data": env.get("data"),
        "controls": env.get("controls") or [],
        "actions": env.get("actions") or [],
        "complexity": env.get("complexity", "inline"),
        "fallback": env.get("fallback") or {},
    }


__all__ = ["publish_structured_response"]
