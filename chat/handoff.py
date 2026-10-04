"""
Handing a conversation to another agent.

Delegation brings another agent's answer back: ``run_agent_tool`` in a chat and
``delegate_task_tool`` in a task run another agent and hand its output to the
caller, who stays in charge of the turn and of the conversation. A handoff
gives the conversation away. The first agent ends its turn with a short
message, the agent it names answers the user directly in the same turn, and
the conversation's target moves to that agent for the turns that follow. It
is the shape of the OpenAI Agents SDK's handoffs with their input filters.

The tool itself lives in tools/handoff.py and the turn that carries it out in
chat/pipelines.py (streaming) and chat/send.py (blocking). This module holds
what both sides share:

* **History filters.** What of the conversation the receiving agent sees:
  ``full`` (every prior turn, plus what the agents before it said this turn),
  ``last_n:<N>`` (the last N messages), ``summary`` (one summary message,
  written by the compaction summariser on the handing agent's model and priced
  as an auxiliary call of its run) or ``none`` (only the user's latest
  message). An agent's ``handoff_history`` is the default; one call may only
  narrow it, widest first: full, summary, last_n, none.
* **The sink.** A context variable the pipeline installs for one run, the same
  way it installs ``common.entity_sink``: the tool reads what it needs to know
  about the turn (which agents already held it, how many handoffs it has had,
  the conversation to summarise) and records the intent there. Outside a chat
  turn there is no sink, and the tool says a handoff needs a conversation.
* **The receiving run's input.** The filtered history, and a handoff note in
  front of the user's message: who handed over and why, the handing agent's
  note, and what history came with it.

Limits: a turn hands off at most ``AGENTS_HUB_HANDOFF_MAX_DEPTH`` times (3 by
default), and never back to an agent that already held the turn, so two
agents cannot pass the user back and forth.
"""
from __future__ import annotations

import contextvars
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from agents.registry import HANDOFF_HISTORY_KINDS, normalize_handoff_history, parse_handoff_history

log = logging.getLogger(__name__)

#: How many handoffs one chat turn may make.
MAX_DEPTH_ENV = "AGENTS_HUB_HANDOFF_MAX_DEPTH"
DEFAULT_MAX_DEPTH = 3

#: ``purpose`` of the summary call on the handing run's ``loop.aux_calls``.
SUMMARY_PURPOSE = "handoff_summary"


def max_depth() -> int:
    """How many handoffs one turn may make (at least 1)."""
    try:
        return max(1, int(os.environ.get(MAX_DEPTH_ENV, "") or DEFAULT_MAX_DEPTH))
    except ValueError:
        return DEFAULT_MAX_DEPTH


# ── history filters ──────────────────────────────────────────────────────────

def normalize_filter(value: Any) -> Optional[str]:
    """The canonical spelling of a history filter, or None when *value* is not
    one. ``last_n`` needs its count: ``last_n:6``."""
    parsed = parse_handoff_history(value)
    if parsed is None:
        return None
    return normalize_handoff_history(value)


def _rank(value: str) -> tuple:
    """Sort key: wider filters first, a longer ``last_n`` wider than a shorter."""
    kind, n = parse_handoff_history(value) or ("none", 0)
    return HANDOFF_HISTORY_KINDS.index(kind), -n


def narrow_filter(requested: Any, default: Any) -> str:
    """The filter one handoff uses: *requested* when it is the default or
    narrower, the default when nothing was requested.

    Raises ``ValueError`` naming the allowed values when *requested* is not a
    filter or would show the receiver more than the agent's default.
    """
    base = normalize_handoff_history(default)
    if requested is None or not str(requested).strip():
        return base
    wanted = normalize_filter(requested)
    if wanted is None:
        raise ValueError(
            f"'{requested}' is not a history filter; use full, summary, last_n:<N> or none")
    if _rank(wanted) < _rank(base):
        raise ValueError(
            f"history '{wanted}' shows more than this agent's default '{base}'; "
            "a handoff may only narrow it (full, summary, last_n, none, widest first)")
    return wanted


def describe_filter(value: str) -> str:
    """One line for the receiving agent: what history came with the handoff."""
    kind, n = parse_handoff_history(value) or ("full", 0)
    if kind == "full":
        return "the whole conversation before this message"
    if kind == "summary":
        return "a summary of the conversation before this message"
    if kind == "last_n":
        return f"the last {n} message(s) of the conversation before this message"
    return "none: only the user's latest message below"


def filter_history(messages: Sequence[Any], history_filter: str, *,
                   summary: str = "", provider: str = "") -> List[Any]:
    """The prior turns the receiving agent gets, as chat messages.

    ``messages`` is the conversation before this turn, as the chat pipeline
    built it for the first agent (``chat.context.build_history_messages``).
    ``summary`` is the text written for the ``summary`` filter while the
    handing agent's run was live; it becomes one message led the way
    compaction leads a folded conversation (``chat.compaction.summary_message``),
    so a provider that refuses a system message mid-conversation gets a user
    note instead.
    """
    kind, n = parse_handoff_history(history_filter) or ("full", 0)
    items = list(messages or [])
    if kind == "full":
        return items
    if kind == "last_n":
        return items[-n:] if n > 0 else []
    if kind == "summary":
        text = (summary or "").strip()
        if not text:
            return []
        from chat.compaction import summary_message
        return [summary_message(text, provider=provider)]
    return []


# ── the intent and the sink ──────────────────────────────────────────────────

@dataclass
class HandoffIntent:
    """What the tool decided: who takes over, why, and with what history."""

    from_agent_id: str
    from_agent_name: str
    to_agent_id: str
    to_agent_name: str
    reason: str
    note: str = ""
    history_filter: str = "full"
    #: The summary for the ``summary`` filter, written during the handing run
    #: so its cost lands on that run.
    summary: str = ""


@dataclass
class HandoffSink:
    """One run's view of the turn it belongs to, and where its handoff lands.

    Installed by the chat pipeline for each run of a turn. ``chain`` holds the
    agents that held the turn so far, this run's agent last; ``depth`` how
    many handoffs the turn already made.
    """

    agent_id: str = ""
    chain: List[str] = field(default_factory=list)
    depth: int = 0
    max_depth: int = DEFAULT_MAX_DEPTH
    workspace: Optional[str] = None
    #: The conversation this run received, for the summary filter: the summary
    #: compaction already keeps for the session, and the turns after it.
    previous_summary: str = ""
    conversation: List[Any] = field(default_factory=list)
    #: The running agent (its provider and model write the summary).
    summarizer: Any = None
    intent: Optional[HandoffIntent] = None

    def set_conversation(self, compaction: Any) -> None:
        """Keep what the running agent received, from its turn's
        ``chat.compaction.Compaction``: the stored summary apart from the
        verbatim tail, so the summariser can continue the one rather than
        re-reading it as a turn."""
        summary = str(getattr(compaction, "summary", "") or "")
        messages = list(getattr(compaction, "messages", None) or [])
        self.previous_summary = summary
        self.conversation = messages[1:] if summary and messages else messages

    def record(self, intent: HandoffIntent) -> bool:
        """Keep the turn's handoff; the first one wins. Returns whether this
        one was kept."""
        if self.intent is not None:
            return False
        self.intent = intent
        return True


_SINK: contextvars.ContextVar[Optional[HandoffSink]] = contextvars.ContextVar(
    "handoff_sink", default=None)


def set_sink(sink: Optional[HandoffSink]) -> contextvars.Token:
    return _SINK.set(sink)


def reset_sink(token: contextvars.Token) -> None:
    _SINK.reset(token)


def current() -> Optional[HandoffSink]:
    return _SINK.get()


# ── the summary filter ───────────────────────────────────────────────────────

class _PricedModel:
    """A chat model whose calls are counted as the running loop's auxiliary
    calls (common/aux_usage.py): the summary appears on the handing run's
    ``loop.aux_calls``, is priced at its model on the Costs page and counts
    against the run's money cap, like a tool policy classifier's call."""

    def __init__(self, llm: Any):
        self._llm = llm

    def invoke(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        reply = self._llm.invoke(messages, *args, **kwargs)
        from common import aux_usage
        aux_usage.record(SUMMARY_PURPOSE, llm=self._llm, response=reply)
        return reply


def summarize_for_handoff(sink: HandoffSink) -> str:
    """The summary the ``summary`` filter hands over, or "" when there is no
    conversation before this turn.

    Written with the compaction summariser (``chat.compaction.summarize``) on
    the running agent's provider and model, and when that call fails, the same
    lossy heuristic compaction falls back to. Called from the tool, inside the
    handing agent's run, so the call is priced on that run.
    """
    previous = (sink.previous_summary or "").strip()
    folded = list(sink.conversation or [])
    if not folded:
        return previous
    from chat.compaction import summarize, summarizer_llm
    llm = summarizer_llm(sink.summarizer) if sink.summarizer is not None else None
    text, _fallback = summarize(_PricedModel(llm) if llm is not None else None, previous, folded)
    return (text or "").strip()


# ── the receiving run ────────────────────────────────────────────────────────

def handoff_note(intent: HandoffIntent, *, replies: Sequence[Dict[str, str]] = ()) -> str:
    """The note the receiving agent reads in front of the user's message.

    ``replies`` are what the agents before it said to the user this turn
    (``{"agent_name", "text"}``, oldest first). Only the ``full`` filter shows
    them: the other filters exist to keep the receiver's context small.
    """
    kind, _n = parse_handoff_history(intent.history_filter) or ("full", 0)
    lines = [
        "## This conversation was handed over to you",
        f"{intent.from_agent_name} (agent `{intent.from_agent_id}`) handed the conversation "
        "over to you. You answer the user directly from now on, in this turn and the next ones.",
        f"Reason: {intent.reason.strip()}",
    ]
    if (intent.note or "").strip():
        lines.append(f"Note from {intent.from_agent_name} for you: {intent.note.strip()}")
    said = [r for r in replies if str(r.get("text") or "").strip()]
    if kind == "full" and said:
        lines.append("Earlier in this turn:")
        for r in said:
            lines.append(f"- {r.get('agent_name') or 'An agent'} told the user: {str(r['text']).strip()}")
    lines.append(f"History you received: {describe_filter(intent.history_filter)}.")
    lines.append("Answer the user's message below. Do not restate the handoff unless it helps the user.")
    return "\n".join(lines)


def receiving_prompt(note: str, prompt: str) -> str:
    """The receiving run's human message: the handoff note, then the user's
    message with its attachments and references, as the first agent got it."""
    return f"{note}\n\n---\n\n{prompt}"


def handoff_event(intent: HandoffIntent, *, run_id: str, next_run_id: str,
                  from_response: str, session_id: Optional[str] = None,
                  usage: Optional[Dict[str, Any]] = None,
                  tool_calls: Optional[int] = None,
                  duration_ms: Optional[int] = None) -> Dict[str, Any]:
    """The ``handoff`` stream event (the stage 3 contract's shape), plus what
    the handing run's bubble needs to close: its usage, tool calls, duration."""
    event: Dict[str, Any] = {
        "type": "handoff",
        "from_agent_id": intent.from_agent_id,
        "from_agent_name": intent.from_agent_name,
        "to_agent_id": intent.to_agent_id,
        "to_agent_name": intent.to_agent_name,
        "reason": intent.reason,
        "history_filter": intent.history_filter,
        "run_id": run_id,
        "next_run_id": next_run_id,
        "from_response": from_response,
    }
    if session_id:
        event["session_id"] = session_id
    if usage is not None:
        event["usage"] = usage
    if tool_calls is not None:
        event["tool_calls"] = tool_calls
    if duration_ms is not None:
        event["duration_ms"] = duration_ms
    return event


def done_fields(event: Dict[str, Any]) -> Dict[str, Any]:
    """The handoff as the final ``done`` carries it: the event's fields."""
    return {k: v for k, v in event.items() if k != "type"}


def handing_record(intent: HandoffIntent, next_run_id: str) -> Dict[str, Any]:
    """Stored on the handing run as ``handoff``: where the conversation went."""
    return {
        "to_agent_id": intent.to_agent_id,
        "to_agent_name": intent.to_agent_name,
        "reason": intent.reason,
        "note": intent.note,
        "history_filter": intent.history_filter,
        "next_run_id": next_run_id,
    }


def received_record(intent: HandoffIntent, run_id: str) -> Dict[str, Any]:
    """Stored on the receiving run as ``handoff_from``: where it came from."""
    return {
        "run_id": run_id,
        "from_agent_id": intent.from_agent_id,
        "from_agent_name": intent.from_agent_name,
        "reason": intent.reason,
        "history_filter": intent.history_filter,
    }


def history_for(agent: Any, history: Sequence[Any], intent: Optional[HandoffIntent]) -> List[Any]:
    """The prior turns a run's agent gets: all of them for the turn's first
    agent (no *intent*), the handoff's filter applied for a receiving one. The
    built agent's provider decides how a summary message is led."""
    if intent is None:
        return list(history or [])
    provider = (agent.effective_provider() if hasattr(agent, "effective_provider")
                else (getattr(agent, "provider", "") or ""))
    return filter_history(history, intent.history_filter, summary=intent.summary,
                          provider=provider or "")


def refusal_by_turn(chain: Sequence[str], intent: HandoffIntent) -> Optional[str]:
    """Why the turn will not carry out a recorded handoff, or None.

    The tool refuses these already; this is the turn's own guard, so a
    handoff recorded by anything else still cannot go back to an agent that
    held the turn or run past the turn's limit. ``chain`` is the agents that
    held the turn, the handing one last.
    """
    if intent.to_agent_id in chain:
        return f"'{intent.to_agent_id}' already held this turn"
    if len(chain) - 1 >= max_depth():
        return f"the turn reached its handoff limit ({max_depth()})"
    return None


def open_receiving_run(request: Any, intent: HandoffIntent, *, handing_run_id: str) -> tuple:
    """Open the receiving agent's run for a handoff.

    Returns ``(request, prompt, run)``: the turn's request addressed to the
    receiving agent (same message, attachments, references, workspace and
    conversation; not bound to the handing agent's instance), the user's
    message with its context blocks as the receiving agent reads them, and
    ``create_chat_run``'s tuple for a run whose ``parent_run_id``
    is the handing run and whose ``handoff_from`` says where it came from.
    The handoff note and the Studio's scene note are the caller's to add
    (:func:`receiving_prompt`).
    """
    from chat.context import build_chat_context
    from chat.runs import create_chat_run

    # The version pin and the per-run overrides were asked for the handing
    # agent (its versions, its tools); the receiving agent runs as it is.
    receiving = request.model_copy(update={"agent_id": intent.to_agent_id, "instance_id": None,
                                           "agent_version": None, "overrides": None})
    prompt, _workspace_abs = build_chat_context(receiving)
    run = create_chat_run(
        receiving,
        parent_run_id=handing_run_id,
        handoff_from=received_record(intent, handing_run_id),
    )
    return receiving, prompt, run


def uses_session_summary(history_filter: str) -> bool:
    """Whether the receiving run should read the conversation's stored
    compaction summary. Only ``full`` does: the narrower filters exist to show
    less, and the stored summary would put the whole conversation back."""
    kind, _n = parse_handoff_history(history_filter) or ("full", 0)
    return kind == "full"


__all__ = [
    "DEFAULT_MAX_DEPTH", "HandoffIntent", "HandoffSink", "MAX_DEPTH_ENV", "SUMMARY_PURPOSE",
    "current", "describe_filter", "done_fields", "filter_history", "handing_record",
    "handoff_event", "handoff_note", "history_for", "max_depth", "narrow_filter",
    "normalize_filter", "open_receiving_run", "received_record", "receiving_prompt",
    "refusal_by_turn", "reset_sink", "set_sink", "summarize_for_handoff", "uses_session_summary",
]
