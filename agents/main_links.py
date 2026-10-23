"""Link a newly created agent to the main agent.

A new agent becomes one the main agent can delegate to and hand the chat to
(its ``delegates`` and ``handoffs``), so a person can reach it from the main
chat straight away. Security comes first: when linking would let the main
agent close a blocked capability combination (the lethal trifecta: private
data, untrusted content and a way out) through the new agent, and the guard is
strict (``CAPABILITY_GUARD=block``), the agent is not linked. The refusal and
its reason are kept on the main agent (``AgentSpec.link_refusals``), so when a
person asks for that agent the main agent says it knows it but may not pass
the chat or a task to it. In ``warn`` mode the link is made and logged.

The main agent itself holds ``run_shell`` under ``capability_override``, so
its full tool list already forms the trifecta and would hide every new one.
The check therefore leaves out the tools a person approves call by call
(``tool_policy: always_ask`` or ``approval_tools``): what remains is what the
main agent can do on its own, plus everything it can already reach.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

MAIN_AGENT_ID = "main-agent"


def _unattended_tools(spec: Any) -> List[str]:
    """The agent's tools minus the ones a person approves on every call."""
    policy = dict(getattr(spec, "tool_policy", None) or {})
    gated = set(getattr(spec, "approval_tools", None) or [])
    return [t for t in (spec.tools or []) if policy.get(t) != "always_ask" and t not in gated]


def link_violation(main: Any, agent_id: str) -> Optional[Any]:
    """The blocking combination linking ``agent_id`` to ``main`` would close,
    or None. Judged against what the main agent reaches now, and against the
    new agent alone, so one agent cannot slip in behind an earlier link."""
    from agents.capability_guard import _effective_violation
    tools = _unattended_tools(main)
    current = list(main.delegates or [])
    after = _effective_violation(main.id, tools, delegates=current + [agent_id])
    if after is None or not after.blocking:
        return None
    before = _effective_violation(main.id, tools, delegates=current)
    if before is None or not before.blocking:
        return after
    alone = _effective_violation(main.id, tools, delegates=[agent_id])
    return alone if alone is not None and alone.blocking else None


def refusal_reason(violation: Any) -> str:
    """One readable line for the refusal: the rule and the capabilities."""
    message = getattr(violation, "message", "") or getattr(violation, "title", "")
    return " ".join(str(message).split())


def link_new_agent(agent_id: str) -> Dict[str, Any]:
    """Add ``agent_id`` to the main agent's delegates and handoffs, or record
    why it was not. Never raises: creating the agent must not fail over this.

    Returns ``{"linked": bool, "reason": str}`` (``skipped`` when there was
    nothing to do: the main agent is missing, the agent is the main agent
    itself, a system agent, or already linked)."""
    try:
        return _link(agent_id)
    except Exception:  # noqa: BLE001 - the agent exists either way; the link is a convenience
        log.warning("main agent link: could not link %s", agent_id, exc_info=True)
        return {"linked": False, "skipped": True, "reason": ""}


def _link(agent_id: str) -> Dict[str, Any]:
    from agents import registry
    from agents.capability_guard import guard_mode

    agent_id = str(agent_id or "").strip()
    raw = registry.get_agent_raw(MAIN_AGENT_ID)
    new = registry.get_agent(agent_id) if agent_id else None
    if raw is None or new is None or agent_id == MAIN_AGENT_ID or new.system:
        return {"linked": False, "skipped": True, "reason": ""}
    main = registry.get_agent(MAIN_AGENT_ID) or raw

    delegates = list(raw.delegates or [])
    handoffs = list(raw.handoffs or [])
    refusals = dict(raw.link_refusals or {})

    violation = link_violation(main, agent_id)
    mode = guard_mode()
    if violation is not None and mode == "block":
        reason = refusal_reason(violation)
        refusals[agent_id] = reason
        registry.save_raw(replace(raw, link_refusals=refusals), user_edit=False,
                          note=f"not linked to {agent_id}")
        log.info("main agent link: %s not linked, %s", agent_id, reason)
        return {"linked": False, "reason": reason}
    if violation is not None:
        log.warning("main agent link: %s linked although %s (guard mode %s)",
                    agent_id, refusal_reason(violation), mode)

    # An empty delegates list already means "every agent"; adding one id
    # would narrow it to that agent.
    if delegates and agent_id not in delegates:
        delegates.append(agent_id)
    if agent_id not in handoffs:
        handoffs.append(agent_id)
    refusals.pop(agent_id, None)
    if (delegates, handoffs, refusals) == (list(raw.delegates or []), list(raw.handoffs or []),
                                            dict(raw.link_refusals or {})):
        return {"linked": True, "skipped": True, "reason": ""}
    registry.save_raw(replace(raw, delegates=delegates, handoffs=handoffs, link_refusals=refusals),
                      user_edit=False, note=f"linked {agent_id}")
    return {"linked": True, "reason": ""}


def unlink_agent(agent_id: str) -> None:
    """Drop a deleted agent from the main agent's delegates, handoffs and
    refusals, so its lists never name an agent that is gone. Never raises."""
    try:
        from agents import registry
        raw = registry.get_agent_raw(MAIN_AGENT_ID)
        if raw is None:
            return
        delegates = [d for d in (raw.delegates or []) if d != agent_id]
        handoffs = [h for h in (raw.handoffs or []) if h != agent_id]
        refusals = {k: v for k, v in (raw.link_refusals or {}).items() if k != agent_id}
        if (len(delegates), len(handoffs), len(refusals)) == (
                len(raw.delegates or []), len(raw.handoffs or []), len(raw.link_refusals or {})):
            return
        registry.save_raw(replace(raw, delegates=delegates, handoffs=handoffs, link_refusals=refusals),
                          user_edit=False, note=f"unlinked {agent_id}")
    except Exception:  # noqa: BLE001 - a stale id is skipped at build time anyway
        log.warning("main agent link: could not unlink %s", agent_id, exc_info=True)


def refusals_for(spec: Any, workspace: Optional[str] = None) -> Dict[str, str]:
    """The refusals still worth telling the model about: the agent exists, is
    not linked after all (a person may have added it by hand) and, with a
    workspace, is available there."""
    from agents import registry
    out: Dict[str, str] = {}
    linked = set(spec.delegates or []) | set(spec.handoffs or [])
    for aid, reason in (getattr(spec, "link_refusals", None) or {}).items():
        if aid in linked:
            continue
        target = registry.get_agent(aid)
        if target is None:
            continue
        if workspace:
            from common.workspace_context import filter_agents_for_workspace
            if not filter_agents_for_workspace([target], workspace):
                continue
        out[aid] = reason
    return out


def refusal_message(caller: Any, target_id: str, action: str) -> Optional[str]:
    """The answer a delegation or handoff to a refused agent gets, or None
    when ``target_id`` is not among ``caller``'s refusals. ``action`` is
    ``"hand the conversation to"`` or ``"delegate to"``."""
    reason = (getattr(caller, "link_refusals", None) or {}).get(target_id)
    if not reason:
        return None
    from agents import registry
    target = registry.get_agent(target_id)
    name = (target.name if target else "") or target_id
    return (
        f"You know the agent '{name}' ({target_id}), but you may not {action} it: linking "
        f"to it would break the security policy ({reason}). Do not retry and do not route "
        f"around it through another agent. Tell the user plainly that you know this agent "
        f"but cannot pass the chat or the task to it because that conflicts with the "
        f"security policy, and that they can open a chat with that agent directly."
    )


def refusal_prompt(spec: Any, workspace: Optional[str] = None) -> str:
    """A prompt section naming the agents ``spec`` was not linked to, or ''."""
    refused = refusals_for(spec, workspace)
    if not refused:
        return ""
    from agents import registry
    lines = []
    for aid, reason in sorted(refused.items()):
        target = registry.get_agent(aid)
        name = (target.name if target else "") or aid
        lines.append(f"- `{aid}` ({name}): {reason}")
    return (
        "## Agents you know but may not reach\n\n"
        "These agents exist, but you may not delegate to them or hand the conversation to "
        "them: the link would break the security policy. When the user asks for one of "
        "them, do not try. Say that you know the agent but cannot pass the chat or the task "
        "to it because that conflicts with the security policy, and that they can open a "
        "chat with that agent directly.\n\n" + "\n".join(lines)
    )


__all__ = ["MAIN_AGENT_ID", "link_new_agent", "unlink_agent", "link_violation", "refusals_for",
           "refusal_message", "refusal_prompt"]
