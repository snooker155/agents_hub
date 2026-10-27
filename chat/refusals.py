"""
Refusals a person can act on, as data.

A turn that the capability guard refuses used to reach the browser as one
string, which a newcomer reads as breakage. This module gives the refusal a
shape the UI can turn into a card with the reason in plain words and a button
in place: a ``code`` (``capability_guard``), the agent and the blocked
capability combination. The text message stays beside it for every consumer
that only reads strings.

``refusal_of`` reads an exception, ``guardrail_refusal`` a run a guardrail
stopped (``AgentResult.guardrail``); ``guardrail_warnings`` the warns of a
turn that went on, which travel on the ``done`` event beside the reply. The result goes on a chat ``done`` event as
``refusal``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

CAPABILITY_GUARD = "capability_guard"
GUARDRAIL = "guardrail"


def capability_refusal(exc: Any) -> Dict[str, Any]:
    """The structured form of a :class:`agents.capability_guard.CapabilityViolation`."""
    from agents import capability_guard as guard
    agent_id = str(getattr(exc, "agent_id", "") or "")
    body = dict(exc.violation.to_dict())
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
        "override_allowed": True,
    }
    if out["override_allowed"] and agent_id:
        out["override_requires_container"] = guard.override_requires_container()
        try:
            out["override_honoured"] = bool(guard.override_honoured_at_build(agent_id))
        except Exception:  # noqa: BLE001 - the card still works without the hint
            log.debug("override_honoured_at_build failed for %s", agent_id, exc_info=True)
    return out


def guardrail_refusal(trip: Dict[str, Any], *, agent_id: Optional[str] = None) -> Dict[str, Any]:
    """The structured form of a guardrail trip (guardrails/runtime.py): which
    guardrail stopped the turn, on its input or its output, and what it found
    as data (``detail``), so the card says it in the person's language. A
    judge's reason is the judge model's own words and travels as they are."""
    return {
        "code": GUARDRAIL,
        "agent_id": agent_id or "",
        "message": str(trip.get("message") or ""),
        "guardrail_id": str(trip.get("guardrail_id") or ""),
        "name": str(trip.get("name") or ""),
        "stage": str(trip.get("stage") or ""),
        "kind": str(trip.get("kind") or ""),
        "detail": dict(trip.get("detail") or {}),
        "reason": str(trip.get("reason") or ""),
    }


def guardrail_warnings(loop: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The warn guardrails that found something in a turn that went on
    (``AgentResult.loop["guardrails"]``, guardrails/runtime.py), shaped like a
    guardrail refusal so the chat says them the same way, under the reply."""
    out: List[Dict[str, Any]] = []
    for check in (loop or {}).get("guardrails") or []:
        if not isinstance(check, dict) or check.get("passed") or check.get("action") != "warn":
            continue
        warning = guardrail_refusal(check)
        warning.pop("code", None)
        warning.pop("agent_id", None)
        warning.pop("message", None)
        out.append(warning)
    return out


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


__all__ = ["CAPABILITY_GUARD", "GUARDRAIL", "capability_refusal", "guardrail_refusal", "guardrail_warnings",
           "refusal_of"]
