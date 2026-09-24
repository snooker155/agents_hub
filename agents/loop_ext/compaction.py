"""
Loop extension: compaction (see agents/loop_ext/__init__.py).

A long run fills its model's context with its own tool trail: every file it
read, every page it fetched, every listing it asked for is sent again on every
later step. Before the run reaches the model's limit (and the guard in
agents/callbacks/guards.py stops it, or a backend silently cuts the head off),
this extension makes room, cheapest pass first:

1. **Clear old tool results.** Every tool result but the newest few is replaced
   by a short note naming the tool and the size of what it returned, and
   telling the model to call the tool again if it still needs the data. The
   assistant turn that asked for the result stays, so every tool call keeps its
   answer and the ``tool_call_id`` pairing the providers check is intact. The
   cleared set only grows, in one batch per pass, so the prefix a provider
   caches stays the same between passes.
2. **Shorten the conversation history** (``chat_history``) with the chat's own
   compaction (chat/compaction.py) when the history alone takes more than its
   share of the budget.
3. **Fold the oldest steps into a summary**, written by the agent's own model
   in one bounded call (a heuristic stands in when that call fails), placed
   where those steps were. The summary is kept on the run's state, so later
   model calls reuse it, and a later fold extends it instead of starting over.

The budget is ``compaction_fraction`` of the model's context window (from the
catalog, providers/context_windows.py), measured in characters the way the
chat's compaction measures them. A model whose window is unknown is left
alone: a guessed window would fold runs that fit.

On Anthropic models that support it, step 1 happens on the server instead:
the request carries the ``clear_tool_uses_20250919`` context edit (see
agents/loop_ext/anthropic_native.py), recorded once per run as a ``server``
compaction. The server's clearing does not count as an edited history, which
the client's would on models that bind their thinking to the conversation.
The summary fold stays as the last resort, triggered by the prompt size the
provider reported for the previous call (what was left after the server
cleared), not by the local estimate.

The context-window guard (agents/callbacks/guards.py ContextWindowGuard) knows
about this extension: a prompt that overflowed the window despite the estimate
sets ``force`` on the run's compaction record, and the next call runs every
pass regardless of the estimate, before the guard would stop the run.

Every pass is recorded in ``LoopState.compactions`` as
``{kind, at_step, cleared, chars_before, chars_after}`` and shown on the run.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from agents.agent_loop import LoopExtension, LoopState

log = logging.getLogger(__name__)

#: Share of the context window a run may fill before it is compacted.
DEFAULT_FRACTION = 0.7
#: Newest tool results that are never cleared.
DEFAULT_KEEP = 3
#: Newest assistant steps (a tool call and its results) a fold always leaves
#: verbatim: the model is in the middle of them.
MIN_KEEP_STEPS = 2
#: After a fold the run should sit at this share of the budget, so the next
#: step does not fold again at once.
FOLD_TARGET = 0.5
#: The history is "the problem" when it alone takes more than this share of
#: the budget; it is then folded down to that share.
HISTORY_SHARE = 0.5
#: A tool result this short is not worth clearing: the note would be as long.
MIN_CLEAR_CHARS = 400
#: Characters of one tool result or argument set that reach the summariser.
TRANSCRIPT_ITEM_CHARS = 2000
#: Ceiling on the whole transcript sent to the summariser.
TRANSCRIPT_MAX_CHARS = 120_000
#: Tool results that are never cleared: reasoning notes and plans are the
#: run's own working memory, and search_tools results are what makes the
#: loaded tools visible.
NEVER_CLEARED = frozenset({"ask_user", "search_tools"})

STEPS_SUMMARY_PREFIX = (
    "Summary of your earlier steps in this run, folded to keep the run inside "
    "the model's context window. The tool calls it describes already happened; "
    "do not repeat them unless you need fresh data."
)

_STEPS_SUMMARY_SYSTEM = (
    "You compress the working record of an agent that is in the middle of a "
    "task, so that it can continue in a smaller context window. From the record "
    "below, write what the agent has done and learned: which tools it called and "
    "what they returned that still matters (names of files, ids, values, "
    "findings, errors), what it has changed, what it concluded, and what it "
    "still has to do. Keep names and values exactly as written. Do not add "
    "advice, do not continue the task and do not call tools. Plain prose or "
    "short bullets, at most 600 words."
)


def _note(tool: str, chars: int) -> str:
    return (f"[Tool result cleared to save context: {tool} returned {chars} "
            f"characters here. Call {tool} again if you need this data.]")


# ── measuring ─────────────────────────────────────────────────────────────────

def _block_chars(block: Any) -> int:
    if isinstance(block, dict):
        total = 0
        for key in ("text", "thinking", "content"):
            value = block.get(key)
            if isinstance(value, str):
                total += len(value)
            elif value is not None and key == "content":
                total += len(json.dumps(value, default=str))
        if block.get("type") == "tool_use" and block.get("input") is not None:
            total += len(json.dumps(block.get("input"), default=str))
        return total
    return len(str(block))


def message_size(message: Any) -> int:
    """Characters one message puts into the prompt: its text and, for an
    assistant turn, the tool calls it made (an argument set can be a whole
    file)."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        total = len(content)
    elif isinstance(content, list):
        total = sum(_block_chars(b) for b in content)
    else:
        total = len(str(content or ""))
    has_blocks = isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
    if not has_blocks:
        for call in getattr(message, "tool_calls", None) or []:
            total += len(str(call.get("name") or ""))
            total += len(json.dumps(call.get("args") or {}, default=str))
    return total


def messages_size(messages: List[Any]) -> int:
    return sum(message_size(m) for m in messages or [])


def _tool_name(message: Any) -> str:
    extra = getattr(message, "additional_kwargs", None) or {}
    return str(extra.get("name") or getattr(message, "name", None) or "tool")


def _is_tool(message: Any) -> bool:
    return getattr(message, "type", "") == "tool"


def _is_ai(message: Any) -> bool:
    return getattr(message, "type", "") in ("ai", "AIMessageChunk")


def _steps(messages: List[Any]) -> List[List[Any]]:
    """The scratchpad cut into steps: an assistant turn and everything after it
    up to the next one (its tool results, a steering message placed after
    them). Messages before the first assistant turn are a step of their own."""
    steps: List[List[Any]] = []
    for m in messages:
        if _is_ai(m) or not steps:
            steps.append([m])
        else:
            steps[-1].append(m)
    return steps


def _step_anchor(step: List[Any]) -> str:
    """A fingerprint of one step, to find the fold boundary again next call."""
    head = step[0] if step else None
    ids = [str(c.get("id") or "") for c in (getattr(head, "tool_calls", None) or [])]
    if any(ids):
        return "tc:" + ",".join(ids)
    from chat.compaction import message_anchor
    return message_anchor(head) if head is not None else ""


def _latest_messages(steps: List[Any]):
    """The model's messages of the run, newest first, from the executor's
    ``(action, observation)`` steps."""
    for step in reversed(list(steps or [])):
        action = step[0] if isinstance(step, (tuple, list)) and step else None
        yield from reversed(list(getattr(action, "message_log", None) or []))


def _last_input_tokens(steps: List[Any]) -> int:
    """Prompt tokens the provider reported for the latest model call of the
    run, 0 when unknown (no step yet, or a backend that reports no usage)."""
    for msg in _latest_messages(steps):
        usage = getattr(msg, "usage_metadata", None) or {}
        tokens = int(usage.get("input_tokens") or 0) if isinstance(usage, dict) else 0
        if tokens:
            return tokens
    return 0


def _applied_edits(steps: List[Any]) -> Optional[Dict[str, int]]:
    """What the server's context editing cleared on the latest call that
    reported it (non-streamed responses carry it in ``response_metadata``)."""
    for msg in _latest_messages(steps):
        meta = getattr(msg, "response_metadata", None) or {}
        cm = meta.get("context_management") if isinstance(meta, dict) else None
        edits = cm.get("applied_edits") if isinstance(cm, dict) else None
        if not edits:
            continue
        edits = [e for e in edits if isinstance(e, dict)]
        usage = getattr(msg, "usage_metadata", None) or {}
        return {"cleared": sum(int(e.get("cleared_tool_uses") or 0) for e in edits),
                "cleared_input_tokens": sum(int(e.get("cleared_input_tokens") or 0) for e in edits),
                "input_tokens": int(usage.get("input_tokens") or 0) if isinstance(usage, dict) else 0}
    return None


# ── the summariser ────────────────────────────────────────────────────────────

def _accounting_callbacks() -> List[Any]:
    """The run's cost and usage callbacks, for the summariser's own call.

    The summary is written inside the loop's model step, so a plain
    ``invoke`` would inherit every callback of the run, including the one that
    streams tokens into a chat bubble. It gets only the money guard and the
    token statistics: the call is paid for like any other.
    """
    try:
        from langchain_core.runnables.config import var_child_runnable_config
        from agents.callbacks.guards import RunBudgetGuard
        from agents.callbacks.run_statistics import StatsCollectorCallback
        config = var_child_runnable_config.get() or {}
        callbacks = config.get("callbacks")
        handlers = callbacks if isinstance(callbacks, list) else getattr(callbacks, "handlers", None)
        return [h for h in handlers or [] if isinstance(h, (RunBudgetGuard, StatsCollectorCallback))]
    except Exception:  # noqa: BLE001 - no accounting is better than no summary
        return []


class _IsolatedModel:
    """A chat model invoked with only the accounting callbacks (see above);
    quacks like the model for chat.compaction.summarize."""

    def __init__(self, llm: Any, callbacks: List[Any]) -> None:
        self._llm = llm
        self._callbacks = callbacks

    def invoke(self, messages: Any, **_kwargs: Any) -> Any:
        return self._llm.invoke(messages, config={"callbacks": list(self._callbacks),
                                                  "run_name": "loop_compaction"})


def _clip(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    half = max(limit // 2, 1)
    return f"{text[:half]}\n[... {len(text) - 2 * half} characters left out ...]\n{text[-half:]}"


def steps_transcript(messages: List[Any]) -> str:
    """The folded steps as text for the summariser: calls, results, notes."""
    from chat.compaction import message_text
    lines: List[str] = []
    for m in messages:
        kind = getattr(m, "type", "")
        if _is_ai(m):
            text = message_text(m).strip()
            if text:
                lines.append(f"Assistant: {_clip(text, TRANSCRIPT_ITEM_CHARS)}")
            for call in getattr(m, "tool_calls", None) or []:
                args = json.dumps(call.get("args") or {}, ensure_ascii=False, default=str)
                lines.append(f"Assistant called {call.get('name')}({_clip(args, TRANSCRIPT_ITEM_CHARS)})")
        elif _is_tool(m):
            lines.append(f"{_tool_name(m)} returned: {_clip(message_text(m), TRANSCRIPT_ITEM_CHARS)}")
        elif kind == "human":
            lines.append(f"User: {_clip(message_text(m), TRANSCRIPT_ITEM_CHARS)}")
        else:
            lines.append(f"Note: {_clip(message_text(m), TRANSCRIPT_ITEM_CHARS)}")
    return _clip("\n\n".join(lines), TRANSCRIPT_MAX_CHARS)


def heuristic_steps_summary(previous: str, messages: List[Any], *, keep_last: int = 12) -> str:
    """The lossy stand-in for a summary the model could not write: the calls
    made, each with the start of its result, and an honest marker for what
    was dropped."""
    from chat.compaction import message_text
    parts: List[str] = [previous.strip()] if previous else []
    entries: List[str] = []
    results = {getattr(m, "tool_call_id", None): m for m in messages if _is_tool(m)}
    for m in messages:
        for call in getattr(m, "tool_calls", None) or []:
            args = json.dumps(call.get("args") or {}, ensure_ascii=False, default=str)
            result = results.get(call.get("id"))
            head = message_text(result).strip().replace("\n", " ")[:200] if result is not None else ""
            entries.append(f"- {call.get('name')}({args[:200]}) -> {head}")
    dropped = max(0, len(entries) - keep_last)
    if dropped:
        parts.append(f"[{dropped} earlier tool call(s) were dropped without a summary]")
    parts.extend(entries[-keep_last:])
    return "\n".join(p for p in parts if p) or "[earlier steps were dropped without a summary]"


def summarize_steps(llm: Any, previous: str, messages: List[Any]) -> Tuple[str, bool]:
    """One bounded summarisation call over folded steps. ``(text, fallback)``."""
    if llm is None or not messages:
        return heuristic_steps_summary(previous, messages), True
    from langchain_core.messages import HumanMessage, SystemMessage
    from chat.compaction import message_text
    body: List[str] = []
    if previous:
        body.append("Summary of the earlier record:\n" + previous)
        body.append("Continue it with the steps below into ONE summary that replaces both.")
    else:
        body.append("Summarise the record below.")
    body.append("--- record ---\n" + steps_transcript(messages))
    try:
        reply = llm.invoke([SystemMessage(content=_STEPS_SUMMARY_SYSTEM),
                            HumanMessage(content="\n\n".join(body))])
        text = message_text(reply).strip()
        if text:
            return text, False
    except Exception:  # noqa: BLE001 - a provider under strain is why the heuristic exists
        log.warning("compaction: summary call failed, using the heuristic", exc_info=True)
    return heuristic_steps_summary(previous, messages), True


# ── the extension ─────────────────────────────────────────────────────────────

class CompactionExtension(LoopExtension):
    """Keeps a run inside its model's context window (module docstring)."""

    name = "compaction"

    def __init__(self, agent: Any, *, window: int, fraction: float, keep: int,
                 provider: str, native_llm: Any = None) -> None:
        self._agent = agent
        self.window = int(window)
        self.fraction = float(fraction)
        self.keep = max(0, int(keep))
        self.provider = provider
        #: The Anthropic model the server-side clearing is sent to, when it applies.
        self.native_llm = native_llm
        self._tool_chars: Optional[int] = None
        # Tests swap the summariser; a real run builds one on the agent's model.
        self.make_summarizer = self._default_summarizer

    # -- configuration --

    @property
    def native(self) -> bool:
        return self.native_llm is not None

    @property
    def budget_chars(self) -> int:
        from chat.compaction import CHARS_PER_TOKEN
        return int(self.window * self.fraction * CHARS_PER_TOKEN)

    def _default_summarizer(self) -> Any:
        from chat.compaction import summarizer_llm
        llm = summarizer_llm(self._agent)
        return _IsolatedModel(llm, _accounting_callbacks()) if llm is not None else None

    def _all_tool_chars(self) -> int:
        """Characters of every bound tool's schema, measured once per build."""
        if self._tool_chars is None:
            total = 0
            try:
                from langchain_core.utils.function_calling import convert_to_openai_tool
                for t in getattr(self._agent, "_tools", None) or []:
                    try:
                        total += len(json.dumps(convert_to_openai_tool(t), default=str))
                    except Exception:  # noqa: BLE001 - an odd tool only makes the estimate rougher
                        total += len(str(getattr(t, "description", "")))
            except ImportError:
                total = 0
            self._tool_chars = total
        return self._tool_chars

    # -- per-run state --

    @staticmethod
    def scratch(state: LoopState) -> Dict[str, Any]:
        cs = state.scratch.get("compaction")
        if not isinstance(cs, dict):
            cs = {"active": True, "force": False, "overflows": 0, "cleared": {},
                  "truncated": {}, "fold": None, "history": None, "server": None}
            state.scratch["compaction"] = cs
        return cs

    # -- the hooks --

    def model_kwargs(self, state: LoopState, llm: Any) -> Dict[str, Any]:
        """The server-side clearing, on the agent's own Anthropic model only
        (a fallback model on another provider gets nothing)."""
        if self.native_llm is None or llm is not self.native_llm:
            return {}
        from agents.loop_ext import anthropic_native as an
        cs = self.scratch(state)
        edit = self.server_edit()
        if cs.get("server") is None:
            entry = {"kind": "server", "at_step": 0, "cleared": 0, "chars_before": 0,
                     "chars_after": 0, "strategy": an.CLEAR_TOOL_USES,
                     "trigger_tokens": edit["trigger"]["value"], "keep": edit["keep"]["value"]}
            state.compactions.append(entry)
            cs["server"] = entry
        return {"betas": an.betas_with(llm, an.CONTEXT_MANAGEMENT_BETA),
                "context_management": {"edits": [edit]}}

    def server_edit(self) -> Dict[str, Any]:
        """The ``clear_tool_uses`` edit: trigger at the same share of the
        window as the client's pass, keep the same newest results, and clear
        at least a twentieth of the window so a clearing that invalidates the
        provider's prompt cache is worth the cache write."""
        from agents.loop_ext import anthropic_native as an
        trigger = max(int(self.window * self.fraction), 1)
        edit: Dict[str, Any] = {
            "type": an.CLEAR_TOOL_USES,
            "trigger": {"type": "input_tokens", "value": trigger},
            "keep": {"type": "tool_uses", "value": max(self.keep, 1)},
            "clear_at_least": {"type": "input_tokens", "value": max(5000, self.window // 20)},
        }
        # Only names the request declares: the list is checked against it.
        names = {str(getattr(t, "name", "") or "") for t in getattr(self._agent, "_tools", None) or []}
        exclude = sorted(self._never_cleared() & names)
        if exclude:
            edit["exclude_tools"] = exclude
        return edit

    @staticmethod
    def _never_cleared() -> frozenset:
        try:
            from tools.approval import REASONING_TOOL_NAMES as reasoning
        except ImportError:
            reasoning = frozenset()
        return NEVER_CLEARED | reasoning

    def shape_messages(self, state: LoopState, inputs: Dict[str, Any],
                       scratchpad: List[Any]) -> List[Any]:
        cs = self.scratch(state)
        steps = inputs.get("intermediate_steps") or []
        at_step = len(steps)
        if self.native and cs.get("server") is not None:
            self._note_server_edits(cs, steps)

        # What earlier passes of this run decided, applied again: the trail is
        # rebuilt from the steps on every call.
        history = list(inputs.get("chat_history") or [])
        shaped_history = self._apply_history(cs, history)
        cleared = self._apply_cleared(cs, scratchpad)
        view = self._view(cs, cleared)

        size = self._size(state, inputs, shaped_history, view)
        forced = bool(cs.get("force"))
        if not forced and not self._over(steps, size):
            return self._finish(inputs, history, shaped_history, view)
        cs["force"] = False
        budget = self.budget_chars

        # 1. Old tool results (the server does this on a native model).
        if not self.native:
            newly = self._clear(cs, view)
            if newly:
                cleared = self._apply_cleared(cs, scratchpad)
                view = self._view(cs, cleared)
                after = self._size(state, inputs, shaped_history, view)
                self._record(state, "clear_tool_results", at_step, newly, size, after)
                size = after
                if not forced and size <= budget:
                    return self._finish(inputs, history, shaped_history, view)

        # 2. The conversation history, when it alone is the problem.
        if history and messages_size(shaped_history) > budget * HISTORY_SHARE:
            new_history, folded = self._compact_history(cs, history)
            if folded:
                shaped_history = new_history
                after = self._size(state, inputs, shaped_history, view)
                self._record(state, "summary", at_step, folded, size, after, scope="history")
                size = after
                if not forced and size <= budget:
                    return self._finish(inputs, history, shaped_history, view)

        # 3. Fold the oldest steps into the run's summary.
        fixed = size - messages_size(view)
        folded = self._fold(cs, cleared, fixed)
        if folded:
            view = self._view(cs, cleared)
            after = self._size(state, inputs, shaped_history, view)
            self._record(state, "summary", at_step, folded, size, after, scope="steps",
                         fallback=bool((cs.get("fold") or {}).get("fallback")))
            size = after

        # 4. Still past the whole window: a result too big to keep whole.
        from chat.compaction import CHARS_PER_TOKEN
        if size > self.window * CHARS_PER_TOKEN:
            trimmed = self._truncate(cs, view)
            if trimmed:
                view = self._view(cs, cleared)
                after = self._size(state, inputs, shaped_history, view)
                self._record(state, "clear_tool_results", at_step, trimmed, size, after,
                             truncated=True)
        return self._finish(inputs, history, shaped_history, view)

    def _view(self, cs: Dict[str, Any], cleared: List[Any]) -> List[Any]:
        """The trail as the model sees it: cleared results, the fold, cut results."""
        return self._apply_truncated(cs, self._apply_fold(cs, cleared))

    # -- helpers of shape_messages --

    @staticmethod
    def _finish(inputs: Dict[str, Any], history: List[Any], shaped: List[Any],
                view: List[Any]) -> List[Any]:
        if shaped is not history and inputs.get("chat_history") is not None:
            inputs["chat_history"] = shaped
        return view

    def _size(self, state: LoopState, inputs: Dict[str, Any], history: List[Any],
              view: List[Any]) -> int:
        """Estimated characters of the whole prompt: system prompt, bound tool
        schemas, history, this turn's request and the tool trail."""
        tools = (state.scratch.get("tool_search") or {}).get("bound_chars")
        tool_chars = int(tools) if isinstance(tools, int) else self._all_tool_chars()
        return (len(getattr(self._agent, "system_prompt", "") or "") + tool_chars
                + messages_size(history) + len(str(inputs.get("input") or ""))
                + messages_size(view))

    def _over(self, steps: List[Any], size: int) -> bool:
        if self.native:
            # The server clears before the model reads; what it reported for
            # the previous call is what is left. Only when that is still past
            # the line is the client's fold needed.
            measured = _last_input_tokens(steps)
            if measured:
                return measured > self.window * self.fraction
        return size > self.budget_chars

    def _note_server_edits(self, cs: Dict[str, Any], steps: List[Any]) -> None:
        """Copy what the server reported clearing onto the run's ``server``
        entry. Only a call that was not streamed carries the report (the
        installed client drops it from a stream), so on most runs the entry
        says that clearing was on, not how much it cleared."""
        applied = _applied_edits(steps)
        entry = cs.get("server")
        if not applied or not isinstance(entry, dict):
            return
        from chat.compaction import CHARS_PER_TOKEN
        if applied["cleared"] and not entry.get("cleared"):
            entry["at_step"] = len(steps)  # the step the server first cleared at
        entry["cleared"] = applied["cleared"]
        entry["cleared_input_tokens"] = applied["cleared_input_tokens"]
        if applied["input_tokens"]:
            entry["chars_after"] = applied["input_tokens"] * CHARS_PER_TOKEN
            entry["chars_before"] = (applied["input_tokens"]
                                     + applied["cleared_input_tokens"]) * CHARS_PER_TOKEN

    @staticmethod
    def _record(state: LoopState, kind: str, at_step: int, cleared: int,
                before: int, after: int, **extra: Any) -> None:
        entry = {"kind": kind, "at_step": at_step, "cleared": int(cleared),
                 "chars_before": int(before), "chars_after": int(after)}
        entry.update({k: v for k, v in extra.items() if v not in (None, False)})
        state.compactions.append(entry)

    # clearing

    def _clear(self, cs: Dict[str, Any], view: List[Any]) -> int:
        """Add every tool result the model still sees but the newest ``keep``
        to the cleared set (results already folded into the summary are not
        worth a pass)."""
        results = [m for m in view if _is_tool(m)]
        older = results[:-self.keep] if self.keep else results
        never = self._never_cleared()
        cleared: Dict[str, str] = cs.setdefault("cleared", {})
        newly = 0
        for m in older:
            call_id = str(getattr(m, "tool_call_id", "") or "")
            name = _tool_name(m)
            if not call_id or call_id in cleared or name in never:
                continue
            chars = message_size(m)
            if chars < MIN_CLEAR_CHARS:
                continue
            cleared[call_id] = _note(name, chars)
            newly += 1
        return newly

    @staticmethod
    def _apply_cleared(cs: Dict[str, Any], scratchpad: List[Any]) -> List[Any]:
        cleared = cs.get("cleared") or {}
        if not cleared:
            return list(scratchpad)
        out = []
        for m in scratchpad:
            call_id = str(getattr(m, "tool_call_id", "") or "") if _is_tool(m) else ""
            if call_id and call_id in cleared:
                m = m.model_copy(update={"content": cleared[call_id]})
            out.append(m)
        return out

    def _truncate(self, cs: Dict[str, Any], view: List[Any]) -> int:
        """Cut kept tool results that are each larger than a quarter of the
        budget down to that size, head and tail kept."""
        limit = max(self.budget_chars // 4, MIN_CLEAR_CHARS)
        truncated: Dict[str, int] = cs.setdefault("truncated", {})
        newly = 0
        for m in view:
            call_id = str(getattr(m, "tool_call_id", "") or "") if _is_tool(m) else ""
            if call_id and call_id not in truncated and message_size(m) > limit:
                truncated[call_id] = limit
                newly += 1
        return newly

    @staticmethod
    def _apply_truncated(cs: Dict[str, Any], view: List[Any]) -> List[Any]:
        truncated = cs.get("truncated") or {}
        if not truncated:
            return view
        from chat.compaction import message_text
        out = []
        for m in view:
            call_id = str(getattr(m, "tool_call_id", "") or "") if _is_tool(m) else ""
            if call_id in truncated:
                text = _clip(message_text(m), truncated[call_id])
                m = m.model_copy(update={"content": text + (
                    f"\n[Result of {_tool_name(m)} cut to fit the context window. "
                    "Call it again with a narrower request if you need the rest.]")})
            out.append(m)
        return out

    # history

    def _apply_history(self, cs: Dict[str, Any], history: List[Any]) -> List[Any]:
        stored = cs.get("history")
        if not stored or not history:
            return history
        from chat.compaction import message_anchor
        if stored.get("len") == len(history) and stored.get("anchor") == message_anchor(history[-1]):
            return list(stored.get("messages") or history)
        return history

    def _compact_history(self, cs: Dict[str, Any], history: List[Any]) -> Tuple[List[Any], int]:
        from chat.compaction import compact_history, message_anchor
        try:
            result = compact_history(
                history,
                budget_chars=int(self.budget_chars * HISTORY_SHARE),
                llm=self.make_summarizer(),
                provider=self.provider,
            )
        except Exception:  # noqa: BLE001 - an unfoldable history is sent as it is
            log.warning("compaction: history fold failed", exc_info=True)
            return history, 0
        if not result.changed or not result.folded:
            return history, 0
        cs["history"] = {"len": len(history), "anchor": message_anchor(history[-1]),
                         "messages": list(result.messages)}
        return list(result.messages), int(result.folded)

    # folding

    def _fold_state(self, cs: Dict[str, Any], steps: List[List[Any]]) -> Tuple[str, int]:
        """``(summary, steps it covers)`` of this run's fold, when its boundary
        is still where it was."""
        fold = cs.get("fold") or {}
        covered = int(fold.get("steps") or 0)
        if not fold.get("summary") or covered <= 0 or covered > len(steps):
            return "", 0
        if _step_anchor(steps[covered - 1]) != fold.get("anchor"):
            cs["fold"] = None
            return "", 0
        return str(fold["summary"]), covered

    def _summary_message(self, text: str) -> Any:
        from langchain_core.messages import HumanMessage
        return HumanMessage(content=f"{STEPS_SUMMARY_PREFIX}\n\n{text}")

    def _apply_fold(self, cs: Dict[str, Any], scratchpad: List[Any]) -> List[Any]:
        steps = _steps(scratchpad)
        summary, covered = self._fold_state(cs, steps)
        if not covered:
            return list(scratchpad)
        tail = [m for step in steps[covered:] for m in step]
        return [self._summary_message(summary), *tail]

    def _fold(self, cs: Dict[str, Any], scratchpad: List[Any], fixed: int) -> int:
        """Fold more of the oldest steps into the summary. Returns how many
        steps this pass folded (0 when nothing can be folded)."""
        from chat.compaction import SUMMARY_RESERVE_CHARS
        steps = _steps(scratchpad)
        previous, covered = self._fold_state(cs, steps)
        tail = steps[covered:]
        foldable = len(tail) - MIN_KEEP_STEPS
        if foldable <= 0:
            return 0
        target = int(self.budget_chars * FOLD_TARGET)
        remaining = sum(messages_size(s) for s in tail)
        count = 0
        while count < foldable and (count == 0 or fixed + SUMMARY_RESERVE_CHARS + remaining > target):
            remaining -= messages_size(tail[count])
            count += 1
        folded_messages = [m for step in tail[:count] for m in step]
        text, fallback = summarize_steps(self.make_summarizer(), previous, folded_messages)
        cs["fold"] = {"summary": text, "steps": covered + count,
                      "anchor": _step_anchor(steps[covered + count - 1]), "fallback": fallback}
        return count


# ── building ──────────────────────────────────────────────────────────────────

def _enabled(agent: Any) -> bool:
    spec = getattr(agent, "spec", None)
    if getattr(spec, "compaction", None) is False:
        return False
    from agents.loop_ext.settings import loop_setting
    return bool(loop_setting(agent, "compaction", True))


def _window(agent: Any) -> Tuple[str, str, int]:
    from agents.loop_ext.anthropic_native import model_id
    llm = getattr(agent, "_llm", None)
    try:
        provider = agent.effective_provider() if hasattr(agent, "effective_provider") else ""
    except Exception:  # noqa: BLE001 - a provider that cannot be read is an unknown window
        provider = ""
    provider = provider or str(getattr(agent, "provider", "") or "")
    model = str(getattr(agent, "model", "") or "") or model_id(llm)
    try:
        from providers.context_windows import get_model_context_window
        window = int(get_model_context_window(provider, model) or 0)
    except Exception:  # noqa: BLE001 - an unreadable catalog is an unknown window
        window = 0
    return provider, model, window


def _native_llm(agent: Any, model: str) -> Any:
    from agents.loop_ext import anthropic_native as an
    from agents.loop_ext.settings import loop_setting
    llm = getattr(agent, "_llm", None)
    if llm is None or not an.is_anthropic_client(llm):
        return None
    if not an.supports_context_editing(an.model_id(llm) or model):
        return None
    if not loop_setting(agent, "native", True):
        return None
    return llm


def extension_for(agent: Any) -> Optional[CompactionExtension]:
    """The compaction policy for *agent*, or None when it is switched off or
    the model's context window is unknown."""
    if not _enabled(agent):
        return None
    provider, model, window = _window(agent)
    if window <= 0:
        return None
    from agents.loop_ext.settings import loop_setting
    fraction = float(loop_setting(agent, "compaction_fraction", DEFAULT_FRACTION))
    if not 0.05 <= fraction <= 0.95:
        fraction = DEFAULT_FRACTION
    keep = int(loop_setting(agent, "compaction_keep", DEFAULT_KEEP))
    return CompactionExtension(agent, window=window, fraction=fraction, keep=max(keep, 0),
                               provider=provider, native_llm=_native_llm(agent, model))


__all__ = [
    "CompactionExtension",
    "extension_for",
    "heuristic_steps_summary",
    "message_size",
    "steps_transcript",
    "summarize_steps",
]
