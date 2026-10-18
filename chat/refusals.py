"""
Refusals a person can act on, as data.

A turn that the capability guard refuses used to reach the browser as one
string, which a newcomer reads as breakage. This module gives the refusal a
shape the UI can turn into a card with the reason in plain words and a button
in place: a ``code`` (``capability_guard``), the agent and the blocked
capability combination. The text message stays beside it for every consumer
that only reads strings.

``refusal_of`` reads an exception. The result goes on a chat ``done`` event as
``refusal``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

CAPABILITY_GUARD = "capability_guard"


def capability_refusal(exc: Any) -> Dict[str, Any]:
    """The structured form of a :class:`agents.capability_guard.CapabilityViolation`."""
    from agents import capability_guard as guard
    agent_id = str(getattr(exc, "agent_id", "") or "")
    body = dict(exc.violation.to_dict())
    system_rule = guard.is_system_workspace_agent(agent_id) if agent_id else False
    out: Dict[str, Any] = {
        "code": CAPABILITY_GUARD,
        "agent_id": agent_id,
        "message": str(exc),
        "rule_id": body.get("rule_id"),
        "title": body.get("title"),
        "explanation": body.get("explanation"),
        "capabilities": body.get("capabilities") or [],
        "sources": body.get("sources") or {},
        "guard_mode": guard.guard_mode(),
        # The system workspace rule is never softened, so no button for it.
        "override_allowed": not system_rule and body.get("rule_id") != _system_rule_id(),
    }
    if out["override_allowed"] and agent_id:
        out["override_requires_container"] = guard.override_requires_container()
        try:
            out["override_honoured"] = bool(guard.override_honoured_at_build(agent_id))
        except Exception:  # noqa: BLE001 - the card still works without the hint
            log.debug("override_honoured_at_build failed for %s", agent_id, exc_info=True)
    return out


def _system_rule_id() -> str:
    from tools.capabilities import SYSTEM_WORKSPACE_RULE_ID
    return SYSTEM_WORKSPACE_RULE_ID


def refusal_of(exc: BaseException, *, agent_id: Optional[str] = None,
               workspace: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The refusal an exception stands for, or None for an ordinary failure.
    Never raises."""
    try:
        from agents.capability_guard import CapabilityViolation
        if isinstance(exc, CapabilityViolation):
            return capability_refusal(exc)
    except Exception:  # noqa: BLE001 - a refusal that cannot be shaped is shown as plain text
        log.debug("refusal not shaped", exc_info=True)
    return None


__all__ = ["CAPABILITY_GUARD", "capability_refusal", "refusal_of"]
