"""
``handoff_to_agent``: give the conversation to another agent.

Built per agent, like the skills tools (memory/procedural.py), because what it
may do is the agent's own configuration: ``AgentSpec.handoffs`` names the
agents it may hand over to, and ``AgentSpec.handoff_history`` the default
history filter. agents/agent_factory.py adds the tool only when that list is
not empty, and its description lists the targets by name and description so
the model can pick one without another lookup.

A call is checked before anything happens: the target must be on the list,
exist, be usable in the workspace, not have held this turn already (no
passing the user back and forth) and fit in the turn's handoff limit; a
requested history filter may only narrow the agent's default (a value that is
not a filter, or a wider one, falls back to the default). A refusal is an
ordinary tool answer and the agent goes on. A handoff that passes is recorded
on the turn's sink (chat/handoff.py) and ends the agent's turn at once
(``agents.agent_loop.end_turn``): the run answers with what the agent told the
user alongside the call, or with the reason when it said nothing, and the chat
pipeline starts the receiving agent in the same turn.

A task run, the CLI's one-shot run or any other run outside a conversation
installs no sink, and the tool answers that a handoff needs a conversation and
points at ``delegate_task_tool`` instead.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from tools._json import json_err as _json_err, json_ok as _json_ok

log = logging.getLogger(__name__)

HANDOFF_TOOL_NAME = "handoff_to_agent"

#: Added to the system prompt of an agent that holds the tool (agent_factory).
HANDOFF_PROMPT = (
    "## Handing the conversation over\n"
    "`handoff_to_agent` gives this conversation to another agent: the one whose role "
    "the user's request belongs to (the tool lists who you may hand over to). A handoff "
    "ends your turn. The other agent answers the user directly, in this turn and in the "
    "turns after it, so hand over only when the rest of the conversation belongs with "
    "that agent. Hand over by calling the tool; never write the call out as text. The "
    "`reason` you give is shown to the user as the reason for the handoff, so write it "
    "for them, in their language. When the user asks to talk to one of those agents, "
    "or to be passed on to it, that is a handoff: call `handoff_to_agent`, not a "
    "delegation tool such as `run_agent_tool`, which would keep you in the conversation. "
    "When you only need a piece of work done and want to keep the conversation, "
    "delegate instead if you have a delegation tool, and answer with its result. A "
    "refused handoff is final for that target: answer yourself or pick another listed "
    "agent that clearly fits."
)


class HandoffInput(BaseModel):
    agent_id: str = Field(..., description="The agent to hand the conversation to: one of the ids listed in this tool's description.")
    reason: str = Field(..., description="Why that agent takes over, in one short sentence in the user's language. The user sees it.")
    note: Optional[str] = Field(
        None,
        description="What the receiving agent should know that the conversation does not say: what you found out, what the user wants next. Only the receiving agent sees it.",
    )
    history: Optional[str] = Field(
        None,
        description="How much of the conversation the receiving agent sees: 'full', 'summary', 'last_n:<N>' or 'none'. Leave empty for your default; you may choose less than the default, never more. Only one of those four values, never free text.",
    )


def _target_lines(targets: List[str]) -> List[str]:
    """One line per target that exists, for the tool description."""
    from agents.registry import get_agent
    lines: List[str] = []
    for aid in targets:
        spec = get_agent(aid)
        if spec is None:
            continue
        description = " ".join(str(spec.description or "").split())
        if len(description) > 200:
            description = description[:199].rstrip() + "…"
        lines.append(f"- `{spec.id}` ({spec.name})" + (f": {description}" if description else ""))
    return lines


def create_handoff_tools(spec: Any, workspace: Optional[str] = None) -> List[StructuredTool]:
    """``[handoff_to_agent]`` bound to *spec*'s targets and default filter, or
    ``[]`` when the agent may hand over to nobody (no handoffs configured, or
    none of them exists any more)."""
    if spec is None:
        return []
    agent_id = str(getattr(spec, "id", "") or "")
    agent_name = str(getattr(spec, "name", "") or agent_id)
    # A role reference (``@verifier``) names whoever holds the role in this
    # workspace (agents/roles.py); the tool lists and accepts that agent.
    from agents.roles import expand as _expand_roles
    targets = [t for t in _expand_roles(getattr(spec, "handoffs", None) or [], workspace)
               if t and t != agent_id]
    default_filter = str(getattr(spec, "handoff_history", "") or "full")
    listed = _target_lines(targets)
    if not listed:
        return []

    def _handoff(agent_id: str, reason: str, note: Optional[str] = None,
                 history: Optional[str] = None) -> str:
        try:
            return _perform(
                caller_id=spec.id, caller_name=agent_name, targets=targets,
                default_filter=default_filter, workspace=workspace,
                target=agent_id, reason=reason, note=note, history=history,
            )
        except Exception as e:  # noqa: BLE001 - a tool answers with an error envelope, never a traceback
            log.debug("handoff_to_agent failed", exc_info=True)
            return _json_err(f"Failed to hand over: {e}", code="error")

    description = (
        "Hand this conversation over to another agent. The agent you name answers the user "
        "directly and keeps the conversation after this turn; your turn ends with the call. "
        "Give a short `reason` the user will see, a `note` with what the receiving agent should "
        "know, and optionally a narrower `history` "
        f"(your default: {default_filter}). You may hand over to:\n" + "\n".join(listed)
    )
    return [StructuredTool.from_function(
        name=HANDOFF_TOOL_NAME,
        description=description,
        func=_handoff,
        args_schema=HandoffInput,
    )]


def _perform(*, caller_id: str, caller_name: str, targets: List[str], default_filter: str,
             workspace: Optional[str], target: str, reason: str, note: Optional[str],
             history: Optional[str]) -> str:
    """Check the call, record the intent on the turn's sink and end the turn."""
    from chat import handoff as handoff_mod

    target = str(target or "").strip()
    if target.startswith("@"):
        from agents.roles import resolve as _resolve_role
        target = _resolve_role(target, workspace)
    reason = " ".join(str(reason or "").split())
    sink = handoff_mod.current()
    if sink is None:
        return _json_err(
            "Handing the conversation over only works in a conversation (a chat turn). "
            "In a task run, hand part of the work to another agent with delegate_task_tool "
            "and use its result.",
            code="no_conversation")
    if sink.intent is not None:
        return _json_err(
            f"This turn is already handed over to '{sink.intent.to_agent_id}'.",
            code="already_handed_over")
    if not reason:
        return _json_err("Give a reason the user can read.", code="bad_request")
    if target not in targets:
        return _json_err(
            f"'{target}' is not an agent you may hand over to. Allowed: {', '.join(targets)}.",
            code="not_allowed", extra={"allowed": list(targets)})

    from agents.registry import get_agent
    spec = get_agent(target)
    if spec is None:
        return _json_err(f"Agent '{target}' does not exist.", code="not_found")
    # The conversation's workspace; with none recorded on the sink or the
    # build, the run's own (common/workspace_scope.py), so a target is never
    # taken unchecked while the run does work in a workspace.
    from common.workspace_scope import current_workspace
    ws = sink.workspace or workspace or current_workspace()
    if ws:
        from common.workspace_context import filter_agents_for_workspace
        if not filter_agents_for_workspace([spec], ws):
            return _json_err(f"Agent '{target}' is not available in workspace '{ws}'.",
                             code="forbidden")
    if target in sink.chain:
        return _json_err(
            f"'{target}' already answered in this turn; the conversation cannot go back to it now. "
            "Answer the user yourself or hand over to another listed agent.",
            code="already_in_turn", extra={"chain": list(sink.chain)})
    if sink.depth >= sink.max_depth:
        return _json_err(
            f"This turn was already handed over {sink.depth} time(s); the limit is "
            f"{sink.max_depth}. Answer the user yourself.",
            code="too_deep", extra={"depth": sink.depth, "max_depth": sink.max_depth})
    # A value that is not a filter, or would show more than the default, falls
    # back to the default instead of refusing: a refused handoff sends the
    # model off to delegate or to answer itself, which is worse for the user
    # than a handoff with the history the agent was configured to give.
    history_note = ""
    try:
        history_filter = handoff_mod.narrow_filter(history, default_filter)
    except ValueError as e:
        history_filter = handoff_mod.normalize_handoff_history(default_filter)
        history_note = f"{e}; the default '{history_filter}' was used instead."

    summary = handoff_mod.summarize_for_handoff(sink) if history_filter == "summary" else ""
    intent = handoff_mod.HandoffIntent(
        from_agent_id=caller_id, from_agent_name=caller_name,
        to_agent_id=spec.id, to_agent_name=spec.name or spec.id,
        reason=reason, note=str(note or "").strip(),
        history_filter=history_filter, summary=summary,
    )
    if not sink.record(intent):
        return _json_err("This turn is already handed over.", code="already_handed_over")

    from agents.agent_loop import end_turn
    end_turn(reason, tool=HANDOFF_TOOL_NAME, prefer_model_text=True)
    result = {
        "handed_over_to": spec.id,
        "agent_name": spec.name or spec.id,
        "history": history_filter,
        "message": f"{spec.name or spec.id} takes over the conversation from here.",
    }
    if history_note:
        result["history_note"] = history_note
    return _json_ok(result)


__all__ = ["HANDOFF_PROMPT", "HANDOFF_TOOL_NAME", "HandoffInput", "create_handoff_tools"]
