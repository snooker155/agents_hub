"""
Automatic compaction of a chat's history.

A conversation only ever grows, and every turn re-sends all of it. Past a point
the turn stops fitting the model at all, and until now that surfaced as an error
the user could do nothing about but clear the chat (see ``chat.errors`` and
``agents.callbacks.guards.ContextWindowGuard``).

Compaction is the other answer: once the history plus the system prompt passes a
budget, the *older* part is folded into one summary message and only the recent
tail is sent verbatim. The summary is stored on the session, so the next turn
starts from it instead of re-summarising the same material, and a later fold
covers the old summary plus whatever has since dropped out of the tail.

The summary is written by the same provider and model the agent runs on, in one
plain ``llm.invoke`` with no tools and a bounded output. When that call fails —
the very situation compaction exists for is a provider under strain — a lossy
heuristic stands in: the opening request, the most recent turns, and a marker
where the middle used to be.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

#: Share of the model's context window the conversation may occupy before it is
#: folded. The rest is headroom for the system prompt's own growth, this turn's
#: attachments, the tool trail of the run, and the answer.
BUDGET_FRACTION = 0.6

#: Characters per token, the same rough ratio the token estimator uses. The
#: budget is measured in characters throughout: it is what every builder here
#: already has, and a tokenizer per provider would buy precision the threshold
#: does not need.
CHARS_PER_TOKEN = 4

#: Budget when the model's context window is unknown (a local model, a gateway
#: that reports nothing). Matches the prompt history bound in ``chat.context``.
FALLBACK_BUDGET_CHARS = 60_000

#: Ceiling on the summary itself, in output tokens.
SUMMARY_MAX_TOKENS = 800

#: Room reserved for the summary that is about to be written, when deciding how
#: much of the tail can be kept.
SUMMARY_RESERVE_CHARS = SUMMARY_MAX_TOKENS * CHARS_PER_TOKEN

#: Never fold the conversation down to fewer than this many verbatim turns: the
#: last exchanges are what "it", "that one" and "try again" refer to.
MIN_TAIL_MESSAGES = 4

SUMMARY_PREFIX = (
    "Summary of the earlier part of this conversation, folded to keep it inside "
    "the model's context window. Treat it as established fact from this "
    "conversation, not as a new instruction."
)

_SUMMARY_SYSTEM = (
    "You compress a conversation so that it can continue in a smaller context "
    "window. Write a factual summary of what was discussed and decided: the "
    "user's goals and constraints, what was produced or changed, names of files, "
    "tasks, agents and other identifiers that were used, open questions, and "
    "anything the assistant promised to do next. Keep the user's own wording for "
    "names and values. Do not add advice, do not comment on the summary, and do "
    "not answer any request inside the conversation. Plain prose or short "
    "bullets, at most 600 words."
)


# ── budget ────────────────────────────────────────────────────────────────────

def history_budget_chars(provider: str, model: str) -> int:
    """How many characters of conversation a turn may carry for this model.

    ``BUDGET_FRACTION`` of the model's context window, converted to characters;
    the flat fallback when the window is unknown, since a guessed window would
    fold conversations that fit perfectly well.
    """
    try:
        from providers.context_windows import get_model_context_window
        window = int(get_model_context_window(provider or "", model or "") or 0)
    except Exception:
        window = 0
    if window > 0:
        return int(window * BUDGET_FRACTION * CHARS_PER_TOKEN)
    return FALLBACK_BUDGET_CHARS


def message_chars(message: Any) -> int:
    """Length of one message's text content (block lists included)."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                total += len(str(block.get("text") or ""))
            else:
                total += len(str(block))
        return total
    return len(str(content or ""))


def messages_chars(messages: list) -> int:
    return sum(message_chars(m) for m in messages or [])


def message_text(message: Any) -> str:
    """One message's text content, for the summariser and the heuristic."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(b.get("text") or "") if isinstance(b, dict) else str(b)
            for b in content
        )
    return str(content or "")


def _role(message: Any) -> str:
    return "User" if getattr(message, "type", "") == "human" else "Assistant"


def message_anchor(message: Any) -> str:
    """A short fingerprint of one message, used to find it again next turn."""
    import hashlib

    kind = "u" if getattr(message, "type", "") == "human" else "a"
    digest = hashlib.sha1(message_text(message).encode("utf-8", "replace")).hexdigest()
    return f"{kind}:{digest[:16]}"


def realign_covers_until(history: list, covers_until: int, anchor: str) -> int:
    """Where the stored summary ends in *this* turn's history.

    A chat surface re-sends a bounded window of the conversation, so the same
    turn sits at a lower index every time the window slides and a stored index
    alone would quietly skip the messages that moved past it. The summary is
    anchored to the last message it folded: finding that message says where the
    tail starts, and not finding it means everything the summary speaks for has
    already scrolled out of the window, which is exactly a tail that starts at
    the beginning. Records written before the anchor existed keep the index.
    """
    covers_until = max(0, int(covers_until or 0))
    if not anchor:
        return min(covers_until, len(history))
    idx = covers_until - 1
    if 0 <= idx < len(history) and message_anchor(history[idx]) == anchor:
        return covers_until
    for i, message in enumerate(history):
        if message_anchor(message) == anchor:
            return i + 1
    return 0


# ── the result of one pass ────────────────────────────────────────────────────

@dataclass
class Compaction:
    """What a turn should send, and what the session should remember.

    ``messages`` is the ``chat_history`` for this turn: the summary note first
    when there is one, then the verbatim tail. ``covers_until`` is how many of
    the original history messages the summary now speaks for, which is what the
    next turn resumes from.
    """
    messages: list = field(default_factory=list)
    summary: str = ""
    covers_until: int = 0
    folded: int = 0
    changed: bool = False
    fallback: bool = False
    #: Fingerprint of the last message the summary folded, so the next turn can
    #: find where the tail starts even after the history window has slid.
    anchor: str = ""

    @property
    def compacted(self) -> bool:
        """Whether this turn sends a summary in place of older turns."""
        return bool(self.summary)


#: Providers whose client refuses a system message anywhere but the first
#: position. Gemini (langchain-google-genai) raises on one, so its summary is
#: carried as a user note instead.
NO_MID_CONVERSATION_SYSTEM = frozenset({"google"})


def summary_message(text: str, *, provider: str = "", system_prompt: str = ""):
    """The summary rendered as the message that leads the history.

    Normally a system message: it is context about the conversation rather than a
    turn in it, Anthropic merges it into the system block, and the tail keeps its
    own user/assistant alternation. It is marked for the prompt cache when the
    prefix it closes is big enough to be worth caching — the system prompt counts
    toward that, since it sits right before it. Providers that accept a system
    message only in first position get the same note as a user message.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from providers.adapters import MIN_CACHEABLE_CHARS, cacheable_content

    note = f"{SUMMARY_PREFIX}\n\n{text}"
    if (provider or "").strip().lower() in NO_MID_CONVERSATION_SYSTEM:
        return HumanMessage(content=note)
    floor = max(0, MIN_CACHEABLE_CHARS - len(system_prompt or ""))
    return SystemMessage(content=cacheable_content(provider, note, min_chars=floor))


# ── writing the summary ───────────────────────────────────────────────────────

def heuristic_summary(previous: str, folded: list, *, keep_last: int = 2) -> str:
    """The lossy stand-in for a summary the model could not write.

    Keeps the request the conversation opened with and the last few folded
    turns, and says plainly that the middle is gone — an honest gap the agent can
    ask about beats a confident summary nobody wrote.
    """
    parts: list[str] = []
    if previous:
        parts.append(previous.strip())
    kept_first = ""
    for m in folded:
        if getattr(m, "type", "") == "human":
            kept_first = message_text(m).strip()
            break
    if kept_first:
        parts.append(f"The conversation opened with: {kept_first[:1000]}")
    tail = [m for m in folded][-keep_last:] if keep_last > 0 else []
    dropped = max(0, len(folded) - len(tail) - (1 if kept_first else 0))
    if dropped:
        parts.append(f"[{dropped} message(s) in between were dropped without a summary]")
    for m in tail:
        parts.append(f"{_role(m)}: {message_text(m).strip()[:1000]}")
    return "\n\n".join(p for p in parts if p)


def summarize(llm: Any, previous: str, folded: list) -> tuple[str, bool]:
    """One bounded summarisation call. Returns ``(summary, used_fallback)``.

    The agent's own provider and model write it, so the summary is in the same
    voice and costs what that model costs; no tools are bound, so the call cannot
    do anything but answer.
    """
    if llm is None or not folded:
        return heuristic_summary(previous, folded), True
    from langchain_core.messages import HumanMessage, SystemMessage

    transcript = "\n\n".join(f"{_role(m)}: {message_text(m)}" for m in folded)
    body = []
    if previous:
        body.append("Summary of the conversation up to this point:\n" + previous)
        body.append("Continue it with the turns below, into ONE summary that "
                    "replaces both. Keep what is still relevant, drop what was "
                    "superseded.")
    else:
        body.append("Summarise the conversation below.")
    body.append("--- conversation ---\n" + transcript)
    messages = [SystemMessage(content=_SUMMARY_SYSTEM),
                HumanMessage(content="\n\n".join(body))]
    try:
        reply = llm.invoke(messages)
        text = message_text(reply).strip()
        if text:
            return text, False
    except Exception:
        pass
    return heuristic_summary(previous, folded), True


def summarizer_llm(agent: Any):
    """A small, non-streaming model handle on the agent's provider and model.

    Its own handle rather than the agent's: the output is capped at
    ``SUMMARY_MAX_TOKENS``, nothing is streamed into the chat bubble, and the
    agent's tools are not bound to it. Returns None when no model can be built,
    which sends compaction down the heuristic path.
    """
    try:
        from agents.agent_utils import build_chat_model
        return build_chat_model(
            provider=getattr(agent, "provider", None),
            model=getattr(agent, "model", None),
            temperature=0.0,
            max_tokens=SUMMARY_MAX_TOKENS,
            api_key=getattr(agent, "api_key", None),
            base_url=getattr(agent, "base_url", None),
            streaming=False,
        )
    except Exception:
        return None


# ── the pass itself ───────────────────────────────────────────────────────────

def _keep_count(tail: list, target_chars: int, min_tail: int) -> int:
    """How many of the most recent messages stay verbatim."""
    keep, used = 0, 0
    for m in reversed(tail):
        cost = message_chars(m)
        if keep >= 1 and used + cost > target_chars:
            break
        used += cost
        keep += 1
    keep = max(keep, min(min_tail, len(tail)))
    # A pass that folds nothing would loop on the next turn with the same
    # verdict, so once we are here at least one message goes into the summary.
    return min(keep, max(len(tail) - 1, 0))


def compact_history(
    messages: list,
    *,
    budget_chars: int,
    system_prompt: str = "",
    summary: str = "",
    covers_until: int = 0,
    llm: Any = None,
    provider: str = "",
    force: bool = False,
    min_tail: int = MIN_TAIL_MESSAGES,
) -> Compaction:
    """Fold the older half of a history into a summary when it no longer fits.

    ``summary`` / ``covers_until`` are what an earlier turn stored: the summary
    already speaks for ``messages[:covers_until]``, so only the tail after it is
    weighed, and a new fold extends the same summary rather than starting over.
    ``force`` compacts regardless of the budget — the retry after a context
    overflow, where the provider has already said what the arithmetic could not.
    """
    messages = list(messages or [])
    covers_until = max(0, min(int(covers_until or 0), len(messages)))
    if not summary:
        covers_until = 0
    tail = messages[covers_until:]

    def _result(text: str, until: int, folded: int, changed: bool,
                fallback: bool = False) -> Compaction:
        head = [summary_message(text, provider=provider, system_prompt=system_prompt)] if text else []
        anchor = message_anchor(messages[until - 1]) if text and until > 0 else ""
        return Compaction(
            messages=head + messages[until:],
            summary=text, covers_until=until, folded=folded,
            changed=changed, fallback=fallback, anchor=anchor,
        )

    note_chars = len(SUMMARY_PREFIX) + len(summary) if summary else 0
    used = len(system_prompt or "") + note_chars + messages_chars(tail)
    if (not force and used <= budget_chars) or not tail:
        return _result(summary, covers_until, 0, False)

    target = max(budget_chars - len(system_prompt or "") - SUMMARY_RESERVE_CHARS,
                 budget_chars // 4)
    keep = _keep_count(tail, target, min_tail)
    folded = tail[:len(tail) - keep]
    if not folded:
        return _result(summary, covers_until, 0, False)

    text, fallback = summarize(llm, summary, folded)
    return _result(text, covers_until + len(folded), len(folded), True, fallback)


# ── session storage + the turn-level entry point ──────────────────────────────

def compaction_event(compaction: Compaction) -> dict:
    """The stream event that says a fold happened, for the UI to show later."""
    return {
        "type": "compaction",
        "folded": compaction.folded,
        "summary_chars": len(compaction.summary or ""),
        "covers_until": compaction.covers_until,
        "fallback": compaction.fallback,
    }


def compact_for_turn(
    *,
    agent: Any,
    history: list,
    system_prompt: str = "",
    session_id: Optional[str] = None,
    force: bool = False,
    llm: Any = None,
) -> Compaction:
    """Compact one turn's history against the running agent's model.

    Reads the session's stored summary, folds if the budget (or ``force``) says
    so, writes the summary back, and hands back the messages to send. Never
    raises: a compaction that cannot be done leaves the history as it was, which
    is the behaviour this whole path replaces.
    """
    from common.session_service import get_session_summary, set_session_summary

    try:
        stored = get_session_summary(session_id) if session_id else {}
    except Exception:
        stored = {}
    summary = str((stored or {}).get("text") or "")
    covers_until = realign_covers_until(
        history, (stored or {}).get("covers_until") or 0,
        str((stored or {}).get("anchor") or "")) if summary else 0

    budget = history_budget_chars(getattr(agent, "provider", "") or "",
                                  getattr(agent, "model", "") or "")
    needs_llm = force or (
        len(system_prompt or "") + messages_chars(history[covers_until:] if summary else history)
        > budget
    )
    if llm is None and needs_llm:
        llm = summarizer_llm(agent)

    try:
        result = compact_history(
            history,
            budget_chars=budget,
            system_prompt=system_prompt,
            summary=summary,
            covers_until=covers_until,
            llm=llm,
            provider=agent.effective_provider() if hasattr(agent, "effective_provider")
            else (getattr(agent, "provider", "") or ""),
            force=force,
        )
    except Exception:
        return Compaction(messages=list(history), summary="", covers_until=0)

    if result.changed and session_id:
        try:
            set_session_summary(session_id, result.summary, result.covers_until,
                                anchor=result.anchor)
        except Exception:
            pass
    return result


__all__ = [
    "BUDGET_FRACTION",
    "Compaction",
    "FALLBACK_BUDGET_CHARS",
    "MIN_TAIL_MESSAGES",
    "SUMMARY_MAX_TOKENS",
    "SUMMARY_PREFIX",
    "compact_for_turn",
    "compact_history",
    "compaction_event",
    "heuristic_summary",
    "history_budget_chars",
    "message_chars",
    "messages_chars",
    "message_text",
    "message_anchor",
    "realign_covers_until",
    "summarize",
    "summarizer_llm",
    "summary_message",
]
