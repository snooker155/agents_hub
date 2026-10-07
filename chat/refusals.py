"""
Refusals a person can act on, as data.

A turn that the capability guard or a spend limit refuses used to reach the
browser as one string, which a newcomer reads as breakage. This module gives
the refusal a shape the UI can turn into a card with the reason in plain
words and a button in place: a ``code`` (``capability_guard`` or ``budget``),
the agent, and either the blocked capability combination or the limit kind and
its values. The text message stays beside it for every consumer that only
reads strings.

``refusal_of`` reads an exception; ``budget_refusal`` reads the numbers of a
turn cap. The result goes on a chat ``done`` event as ``refusal``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

CAPABILITY_GUARD = "capability_guard"
BUDGET = "budget"

#: Which limit was reached, so the UI knows which one to open.
KIND_WORKSPACE = "workspace"
KIND_PERSON = "person"
KIND_TURN = "turn"


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


def budget_refusal(kind: str, *, spent_usd: Any = None, limit_usd: Any = None, message: str = "",
                   workspace: Optional[str] = None, user_id: Optional[str] = None,
                   period: Optional[str] = None, agent_id: Optional[str] = None,
                   service_id: Optional[str] = None) -> Dict[str, Any]:
    """A budget refusal: which limit (``kind``) and its numbers."""
    out: Dict[str, Any] = {
        "code": BUDGET, "kind": kind, "message": message,
        "spent_usd": _number(spent_usd), "limit_usd": _number(limit_usd),
    }
    for key, value in (("workspace", workspace), ("user_id", user_id), ("period", period),
                       ("agent_id", agent_id), ("service_id", service_id)):
        if value:
            out[key] = value
    return out


def _number(value: Any) -> Optional[float]:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def refusal_of(exc: BaseException, *, agent_id: Optional[str] = None,
               workspace: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The refusal an exception stands for, or None for an ordinary failure.
    Never raises."""
    try:
        from agents.capability_guard import CapabilityViolation
        from common.budget import BudgetExceededError
        from common.user_budget import UserBudgetExceededError
        if isinstance(exc, CapabilityViolation):
            return capability_refusal(exc)
        if isinstance(exc, UserBudgetExceededError):
            return budget_refusal(KIND_PERSON, spent_usd=exc.spend, limit_usd=exc.limit, message=str(exc),
                                  user_id=getattr(exc, "user_id", None), period=exc.period, agent_id=agent_id)
        if isinstance(exc, BudgetExceededError):
            return budget_refusal(KIND_WORKSPACE, spent_usd=exc.spend, limit_usd=exc.limit, message=str(exc),
                                  workspace=getattr(exc, "workspace", None) or workspace,
                                  period=getattr(exc, "period", None), agent_id=agent_id)
    except Exception:  # noqa: BLE001 - a refusal that cannot be shaped is shown as plain text
        log.debug("refusal not shaped", exc_info=True)
    return None


__all__ = ["BUDGET", "CAPABILITY_GUARD", "budget_refusal", "capability_refusal", "refusal_of"]
