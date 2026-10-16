"""
Auto mode: a chat whose person said yes in advance to every confirmation
(docs/hooks.md, "Auto mode").

A person switches it on in a conversation (the Chat page's Auto toggle); every
turn of that conversation then carries ``auto_approve`` on its request. While
it is on, two kinds of confirmation stop waiting for the person:

* A call the tool guard holds for a person in the chat (agents/hooks.py,
  common/tool_approvals.py): the approval row is opened and settled as
  approved straight away, by "auto mode", and the chat shows the card
  already resolved. The policy trail and the audit log record it like any
  other approved call, with ``by: auto_mode``.
* A tool whose own ``user_approved`` argument gates it (run_flow_tool,
  run_team_tool, stop_run, run_eval_tool, ...): :func:`approved` lets the call
  through and records the same settled row, so the person sees what ran.

What auto mode does not touch: a call a rule denies (a deny policy, a hook
that blocks, a guardrail, the capability guard) is refused as before, since a
refusal is not a confirmation; and a card that needs the person to type
something (``propose_connection``: the secrets are theirs to enter) still
waits for them.

Where the flag comes from: the turn's request, checked by
:func:`allowed_for` (an interactive chat surface, a workspace that allows it),
held for the turn in a context variable (:func:`set_turn`) and written on the
turn's run record as ``auto_approve`` so a process that only has the run id
(a run container, a node of a flow chat) reads the same answer.
"""
from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

#: Who settled a call, on the approval row and in the audit trail.
DECIDED_BY = "auto mode"
#: The guard's word for what decided (``by`` on the trail and the event).
BY = "auto_mode"

#: Calls auto mode never answers: the person has to type into the card.
NEVER_AUTO = frozenset({"propose_connection"})

#: Message origins whose person can switch auto mode on: the Chat page and the
#: assistant's thread (common/tool_approvals.INTERACTIVE_ORIGINS).
INTERACTIVE_SOURCES = frozenset({"chat", "assistant-chat"})

_turn: ContextVar[Optional[bool]] = ContextVar("auto_mode_turn", default=None)


def set_turn(on: bool):
    """Mark the current turn as running in auto mode (or not). Returns the
    token for :func:`reset_turn`."""
    return _turn.set(bool(on))


def reset_turn(token) -> None:
    try:
        _turn.reset(token)
    except ValueError:
        # Reset from another context (an async generator finished by a
        # different task): the variable dies with that context anyway.
        log.debug("auto mode: turn flag reset from another context")


def allowed_for(workspace: Optional[str], source: Optional[str]) -> bool:
    """Whether a turn from ``source`` in ``workspace`` may run in auto mode:
    only from a surface with a person in front of it, and only where the
    workspace's admin has not turned it off (``settings.allow_auto_approve``)."""
    if str(source or "chat") not in INTERACTIVE_SOURCES:
        return False
    try:
        from common.workspace_context import normalize_workspace_name, resolve_active_workspace
        ws = normalize_workspace_name(workspace) or resolve_active_workspace()
        if not ws:
            return True
        from workspace import get_workspace_metadata
        settings = get_workspace_metadata(ws).get("settings") or {}
        return settings.get("allow_auto_approve") is not False
    except Exception:  # noqa: BLE001 - an unreadable workspace refuses auto mode, it never grants it
        log.debug("auto mode: workspace settings unreadable", exc_info=True)
        return False


def current_run_id() -> str:
    """The run the calling tool belongs to, found the way agents/hooks.py
    finds it: the loop's own state first, then the run's environment."""
    try:
        from agents.agent_loop import current_state
        state = current_state()
        if state is not None and state.run_id:
            return str(state.run_id)
    except Exception:  # noqa: BLE001 - no loop state, the environment may still name the run
        log.debug("auto mode: no loop state", exc_info=True)
    return str(os.environ.get("AGENT_RUN_ID") or "")


def _run_record(run_id: str) -> Dict[str, Any]:
    try:
        from common.tool_approvals import transport_for
        return transport_for(run_id).get_run(run_id) or {}
    except Exception:  # noqa: BLE001 - an unreadable run is not in auto mode
        log.debug("auto mode: run %s unreadable", run_id, exc_info=True)
        return {}


def is_on(run_id: Optional[str] = None) -> bool:
    """True when the current turn, or the run ``run_id`` belongs to, runs in
    auto mode."""
    flag = _turn.get()
    if flag is not None:
        return flag
    rid = str(run_id or current_run_id() or "")
    if not rid:
        return False
    return bool(_run_record(rid).get("auto_approve"))


def settle(*, run_id: str, tool: str, tool_input: Any = None, reason: str = "",
           fingerprint: str = "", agent_id: str = "", workspace: Optional[str] = None,
           conversation_id: Optional[str] = None, owner: Optional[str] = None,
           emitter: Any = None) -> Optional[Dict[str, Any]]:
    """Record one call auto mode approved: an approval row opened and settled
    as approved by :data:`DECIDED_BY`, and, when the turn streams, the chat's
    card shown already resolved. Returns the settled row, or None when there
    was nowhere to record it (a process without the database): the call still
    goes ahead, the person approved it in advance."""
    from common import tool_approvals
    try:
        row = tool_approvals.open_approval(
            run_id=run_id, tool=tool, tool_input=tool_input, reason=reason,
            fingerprint=fingerprint, by=BY, agent_id=agent_id, workspace=workspace,
            conversation_id=conversation_id, owner=owner,
            timeout_s=tool_approvals.MIN_TIMEOUT_SECONDS)
        settled = tool_approvals.decide(
            str(row["approval_id"]), "approve",
            note="Approved automatically: auto mode is on in this chat.", author=DECIDED_BY)
    except Exception:  # noqa: BLE001 - the record is a trail, not a gate
        log.debug("auto mode: could not record %s", tool, exc_info=True)
        return None
    settled = {**row, **(settled or {}), "status": tool_approvals.STATUS_APPROVED,
               "decided_by_name": DECIDED_BY}
    if emitter is not None:
        for kind in ("tool_approval", "tool_approval_resolved"):
            event = tool_approvals._event(settled, kind)  # noqa: SLF001 - one event shape for every card
            event["auto"] = True
            try:
                emitter(event)
            except Exception:  # noqa: BLE001
                log.debug("auto mode: could not emit %s", kind, exc_info=True)
    return settled


def approved(tool: str, user_approved: bool, tool_input: Any = None, *, reason: str = "") -> bool:
    """The check a ``user_approved`` gate makes: True when the caller says the
    person approved, or when auto mode is on (then the call is recorded as
    approved by auto mode, see :func:`settle`)."""
    if user_approved:
        return True
    if tool in NEVER_AUTO:
        return False
    run_id = current_run_id()
    if not is_on(run_id):
        return False
    if run_id:
        emitter = None
        try:
            from common import stream_sink
            emitter = stream_sink.get_emitter()
        except Exception:  # noqa: BLE001 - no stream: the row is still recorded
            emitter = None
        from common.agent_context import current_agent_id
        settle(run_id=run_id, tool=tool, tool_input=tool_input,
               reason=reason or "The tool asks for a person's yes before it runs.",
               agent_id=current_agent_id.get() or "", emitter=emitter)
    return True


def prompt_note() -> str:
    """What the agent is told when its turn runs in auto mode."""
    return (
        "Auto mode is on in this chat: the person has approved every confirmation in advance. "
        "Do not stop to ask whether to go ahead with a step you were asked to do: call the tool "
        "directly (a tool that takes user_approved will run as approved). Still say what you did, "
        "and still stop to ask when the request itself is unclear."
    )


__all__ = [
    "BY", "DECIDED_BY", "INTERACTIVE_SOURCES", "NEVER_AUTO",
    "allowed_for", "approved", "current_run_id", "is_on", "prompt_note", "reset_turn", "set_turn",
    "settle",
]
