"""
Server-side reconstruction of an instance's context.

The web Chat page posts its transcript from the browser, so a conversation only
has a history for as long as a tab holds it. An instance has to work without
that: the operator opens a copy that finished hours ago, in a session no browser
ever had, and writes to it — and it must come back knowing what it did.

The material is already on disk: every run of the instance stored the exchange
under its payload's ``input_context``, and the tool calls next to it. This
module turns that journal back into the alternating transcript that
``chat.context.build_chat_context`` folds into a prompt.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from common import db
from managers.run_manager import get_run_process

log = logging.getLogger(__name__)

# Bounds mirror the chat prompt budget: the pipeline trims again, but there is
# no reason to read a thousand payload rows to throw them away.
DEFAULT_MAX_TURNS = 20
MAX_TOOL_LINE = 240


def _instance_runs(instance_id: Optional[str], limit: int,
                   conversation_id: Optional[str] = None,
                   service_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """The most recent finished runs of one conversation of the instance,
    oldest first. ``conversation_id`` None (or ``"main"``) is the main
    conversation, which is every run of an instance that never had others.

    With ``service_id`` the conversation is the service's, across every
    replica that ever answered it: a conversation of a service has one
    history whichever replica takes the next message (docs/services.md)."""
    from instances.inbox import normalize_conversation

    cid = normalize_conversation(conversation_id)
    conversation_sql = "conversation_id IS NULL" if cid is None else "conversation_id = ?"
    if service_id:
        owner_sql, owner = "service_id = ?", str(service_id)
    else:
        owner_sql, owner = "instance_id = ?", str(instance_id)
    params: List[Any] = [owner] + ([] if cid is None else [cid]) + [int(limit)]
    rows = db.get_conn().execute(
        "SELECT run_id, input, output, title, started_at, status "
        f"FROM runs WHERE {owner_sql} AND {conversation_sql} "
        "AND status IN ('completed', 'stopped') "
        "ORDER BY COALESCE(started_at, created_at) DESC LIMIT ?",
        params,
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def _exchange(run: Dict[str, Any]) -> Tuple[str, str]:
    """The (user message, agent response) pair of one run.

    Prefers the recorded prompt context — it is what the agent actually saw —
    and falls back to the run's own input/output columns for runs whose payload
    was never written (a crash, or an older record).
    """
    ctx: Dict[str, Any] = {}
    try:
        ctx = (get_run_process(run.get("run_id")) or {}).get("llm_input_context") or {}
    except Exception:  # noqa: BLE001 - payload store may fail in any way, the run columns are the fallback
        log.debug("no prompt context for run %s", run.get("run_id"), exc_info=True)
        ctx = {}
    user = str(ctx.get("user_message") or run.get("input") or "").strip()
    response = str(ctx.get("response") or run.get("output") or "").strip()
    return user, response


def _tool_summary(run_id: str) -> str:
    """``read_file ×12, write_file ×3`` — what the copy *did*, not just what it
    said. Without it a revived instance remembers its answers and forgets its
    work, which is exactly the part the operator is asking about."""
    try:
        calls = (get_run_process(run_id) or {}).get("tool_calls") or []
    except Exception:  # noqa: BLE001 - payload store may fail in any way, the summary is optional
        log.debug("no tool calls for run %s", run_id, exc_info=True)
        return ""
    counts: Dict[str, int] = {}
    for call in calls:
        name = str((call or {}).get("tool") or "").strip()
        if name:
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return ""
    parts = [f"{name} ×{n}" if n > 1 else name
             for name, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    line = ", ".join(parts)
    return line[:MAX_TOOL_LINE]


def build_instance_history(
    instance_id: str,
    *,
    max_turns: int = DEFAULT_MAX_TURNS,
    include_tools: bool = True,
    conversation_id: Optional[str] = None,
    service_id: Optional[str] = None,
) -> List[Any]:
    """The instance's prior turns as ``ChatHistoryMessage`` objects, oldest first.

    Tool activity is folded into the assistant turn it belongs to, so the model
    sees one coherent turn rather than a separate pseudo-message.
    """
    from chat.models import ChatHistoryMessage

    history: List[Any] = []
    for run in _instance_runs(instance_id, max_turns, conversation_id, service_id):
        user, response = _exchange(run)
        if user:
            history.append(ChatHistoryMessage(role="user", content=user))
        if include_tools:
            tools = _tool_summary(str(run.get("run_id")))
            if tools:
                response = (response + f"\n[tools used: {tools}]").strip()
        if response:
            history.append(ChatHistoryMessage(role="agent", content=response))
    return history


def describe_context(instance_id: Optional[str], *, max_turns: int = DEFAULT_MAX_TURNS,
                     conversation_id: Optional[str] = None,
                     service_id: Optional[str] = None) -> Dict[str, Any]:
    """What the next message to this instance will carry into the prompt.

    Backs the instance page's Context tab: an operator who is about to write to
    a copy that ran three hours ago should be able to see what it still knows
    before deciding what to say.
    """
    turns: List[Dict[str, Any]] = []
    total_chars = 0
    for run in _instance_runs(instance_id, max_turns, conversation_id, service_id):
        user, response = _exchange(run)
        tools = _tool_summary(str(run.get("run_id")))
        total_chars += len(user) + len(response)
        turns.append({
            "run_id": run.get("run_id"),
            "started_at": run.get("started_at"),
            "status": run.get("status"),
            "user_message": user,
            "response": response,
            "tools": tools,
        })
    return {
        "instance_id": instance_id,
        "service_id": service_id,
        "conversation_id": conversation_id or "main",
        "turns": turns,
        "turn_count": len(turns),
        "approx_chars": total_chars,
        "max_turns": max_turns,
    }
