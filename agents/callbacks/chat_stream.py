"""
Streaming callback for the interactive chat route.

``ChatStreamCallback`` forwards LLM/tool execution events onto an asyncio queue
(for the active SSE response) and the session broker (for continuation
subscribers), while accumulating token usage and a structured process graph
(tool/thinking/artifact history) for the run record.

The small log/diff/format helpers it depends on live here too, since the chat
route and this callback are their only consumers; the route imports them back
from this module. This module must not import the chat route (one-way dependency
keeps the import graph acyclic).
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler

from agents.callbacks.run_statistics import (
    StatsCollectorCallback,
    cached_input_tokens,
    content_text,
    token_text,
    to_json_safe,
    build_prompt_struct,
    message_to_role_content,
)


# ── log / formatting helpers (shared with the chat route) ──────────────────────

def _now() -> str:
    """Wall-clock HH:MM:SS used to timestamp thinking-trace tool markers."""
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")


def write_log(log_file: Path, lines: list[str]) -> None:
    try:
        log_file.write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass


def append_log(log_lines: list[str], line: str, log_file: Path) -> None:
    log_lines.append(line)
    write_log(log_file, log_lines)


def format_tool_payload(value) -> str:
    """Render a tool input/output as a compact, single-line readable string.

    Tool args arrive as dicts (or their str() repr) and outputs as JSON-ish
    strings. Normalize structured payloads into compact valid JSON so the line
    is readable, but keep it on ONE line — these log lines are later re-parsed
    line-by-line (=== Message blocks, tool_start/tool_end regexes), so newlines
    would leak the payload into the response field and break tool extraction.
    """
    import ast

    def _compact(obj) -> str:
        return json.dumps(to_json_safe(obj), ensure_ascii=False, separators=(", ", ": "))

    # Already structured (e.g. LangChain passes a dict of tool args).
    if isinstance(value, (dict, list, tuple)):
        try:
            return _compact(value)
        except Exception:
            return str(value)
    text = str(value)
    stripped = text.strip()
    if (stripped.startswith("{") and stripped.endswith("}")) or (
        stripped.startswith("[") and stripped.endswith("]")
    ):
        # Try real JSON first, then a Python literal (single-quoted dicts).
        for parser in (json.loads, ast.literal_eval):
            try:
                return _compact(parser(stripped))
            except Exception:
                continue
    return text


def extract_reasoning_text(value) -> str:
    """Pull the human-readable text out of a think/plan tool input.

    LangChain delivers the tool argument as a dict (or its ``str()`` repr, which
    is a *Python* literal with single quotes — not JSON). Either way the useful
    content is the ``thought`` / ``plan`` field, so unwrap it here at the source
    instead of leaking ``{'thought': '...'}`` brackets to the UI. Falls back to
    the raw text when it isn't a recognizable single-field payload.
    """
    import ast

    obj = value
    if isinstance(obj, str):
        stripped = obj.strip()
        if (stripped.startswith("{") and stripped.endswith("}")):
            for parser in (json.loads, ast.literal_eval):
                try:
                    obj = parser(stripped)
                    break
                except Exception:
                    continue
    if isinstance(obj, dict):
        for key in ("thought", "plan", "content", "text", "input"):
            if isinstance(obj.get(key), str):
                return obj[key]
        first_str = next((v for v in obj.values() if isinstance(v, str)), None)
        if first_str is not None:
            return first_str
    return value if isinstance(value, str) else str(value)


def _chunk_reasoning_blocks(content) -> str:
    """Reasoning text carried by a streamed chunk's structured content blocks.

    Providers that stream extended thinking as content blocks (Anthropic-style)
    deliver reasoning with an empty ``token``, so it never reaches the
    ``<think>``-tag filter. Returns "" for the ordinary string-content case.
    """
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in ("thinking", "reasoning_content"):
            continue
        text = block.get("thinking") or block.get("reasoning_content") or block.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    return "".join(parts)


_MAX_DIFF_BYTES = 200_000  # guard against diffing huge files into the SSE stream


def build_artifact(op: str, path: str, before: str | None, after: str | None) -> dict:
    """Build an artifact record with a unified diff and add/delete line counts.

    ``before``/``after`` are full file text, or None when the file did not exist
    (add: before=None; delete: after=None). Non-text or oversized changes are
    recorded with the op/path but an empty diff and a ``binary`` flag so the UI
    can still list the file.
    """
    import difflib

    b = before if before is not None else ""
    a = after if after is not None else ""

    too_big = len(b) > _MAX_DIFF_BYTES or len(a) > _MAX_DIFF_BYTES
    if too_big:
        return {
            "op": op,
            "path": path,
            "diff": "",
            "additions": 0,
            "deletions": 0,
            "binary": True,
            "truncated": True,
        }

    b_lines = b.splitlines(keepends=True)
    a_lines = a.splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(
            b_lines,
            a_lines,
            fromfile=("/dev/null" if before is None else f"a/{path}"),
            tofile=("/dev/null" if after is None else f"b/{path}"),
            n=3,
        )
    )
    diff_text = "".join(diff_lines)
    additions = sum(
        1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++")
    )
    deletions = sum(
        1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---")
    )
    return {
        "op": op,
        "path": path,
        "diff": diff_text,
        "additions": additions,
        "deletions": deletions,
        "binary": False,
    }


# ── the callback ───────────────────────────────────────────────────────────────

class ChatStreamCallback(BaseCallbackHandler):
    """Callback handler that forwards LLM/tool execution events to an asyncio queue."""

    # Tell LangChain to propagate exceptions raised in callbacks instead of swallowing them.
    raise_error: bool = True

    def __init__(
        self,
        loop,
        queue,
        log_lines: list[str],
        log_file: Path,
        session_id: str | None = None,
        run_id: str | None = None,
    ):
        self.loop = loop
        self.queue = queue
        self.log_lines = log_lines
        self.log_file = log_file
        self.session_id = session_id
        # Stamped onto every event. The session channel carries the events of
        # every run on that session, so without it a viewer cannot tell which
        # run a token belongs to — which is exactly what a run's own page needs
        # in order to show only its own generation.
        self.run_id = run_id
        self._step = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        # Subset of prompt_tokens the provider served from its prompt cache.
        # Tracked apart because common.pricing bills it at its own rate.
        self.cached_prompt_tokens = 0
        self.total_tokens = 0
        self.tool_calls = 0
        # Context fill, reported with every usage event so a chat surface can
        # show how close the conversation is to the model's ceiling *before* it
        # hits it. ``context_window`` is 0 until bind_model() resolves it (and
        # stays 0 for models whose window is unknown — the UI then shows no
        # meter rather than a made-up one). The peak per-call prompt is what
        # matters, not the sum: each call re-sends the whole conversation.
        self.context_window = 0
        self.max_prompt_tokens = 0
        self._last_prompt_text = ""
        # Structured snapshot of the most recent LLM prompt and a per-call log.
        self._last_prompt_struct: dict = {}
        self.llm_invocations: list[dict] = []
        self._tool_was_used = False
        # Which tools ran between the previous LLM call and the next one. An
        # agent loop is call → tool(s) → call, so these are the tools whose
        # output drove the call that follows — the only honest answer to "what
        # made the model run again", and far more use than the bare fact that
        # *some* tool had been used by then.
        self._tools_pending_llm: list[str] = []
        self._tools_for_this_call: list[str] = []
        self._output_text_parts: list[str] = []
        # Full process graph data collected during the run
        self.tool_history: list[dict] = []
        self.thinking_history: list[str] = []
        self.llm_invoke_responses: list[dict] = []
        self.artifact_history: list[dict] = []  # file changes (diffs) made this run
        self._pending_tool: dict | None = None
        self.cancelled: bool = False  # set True to interrupt LLM streaming mid-generation
        # Streamed <think> tags are diverted out of the token stream into think
        # events at their true position; remember when one was emitted so
        # on_llm_end doesn't surface the same reasoning a second time.
        from reasoning.native_reasoning import ThinkTagStreamFilter
        self._think_filter = ThinkTagStreamFilter()
        self._native_reasoning_emitted = False
        # Reasoning streamed as a separate field (gpt-oss style: the chunks
        # carry reasoning_content deltas with no content text) accumulates here
        # until the answer starts, then is emitted as one think event.
        self._native_reasoning_buf = ""
        # Reasoning models (qwen3, deepseek-r1, …) emit `<think>…</think>\n\n`
        # before the answer; the filter strips the tags but the trailing newlines
        # become the answer's leading whitespace, rendering as an empty block
        # above the reply. Skip whitespace-only visible output until the first
        # real answer character. Per turn (one callback instance per run).
        self._answer_started = False
        # Trailing newline count in the emitted stream, so runs of blank lines
        # (between segments, before tool calls) are collapsed across token
        # boundaries — see _collapse_newlines.
        self._nl_run = 0

    def _abort_if_cancelled(self) -> None:
        """Abort the agent from inside its own execution thread once stopped.

        ``task.cancel()`` only interrupts the coroutine at an await point — a
        sync tool or LLM call running in a worker thread is unreachable from
        there. These callbacks run *in* that thread, so raising here (LangChain
        propagates it via ``raise_error = True``) aborts the executor loop at
        the next LLM start, streamed token, or tool boundary.
        """
        if self.cancelled:
            raise InterruptedError("Run stopped by user")

    def _emit(self, payload: dict):
        if self.run_id:
            payload.setdefault("run_id", self.run_id)
        # Forward to the local SSE queue for the active HTTP response.
        self.loop.call_soon_threadsafe(self.queue.put_nowait, payload)
        # Also publish to the session broker so /api/sessions/{id}/stream
        # subscribers (e.g. continuation SSE connections) receive the same events.
        if self.session_id:
            try:
                from common.session_broker import broker
                broker.publish_threadsafe(self.session_id, payload)
            except Exception:
                pass

    def emit_external(self, payload: dict) -> None:
        """Thread-safe entry point for forwarding an externally-produced event.

        Used by delegated worker runs (run_agent_tool) to push their nested
        tool/thought events onto this run's SSE stream. ``_emit`` already marshals
        onto the event loop via ``call_soon_threadsafe``, so this is safe to call
        from the worker's execution thread.
        """
        self._emit(payload)

    def _llm_started(self, model_name):
        self._abort_if_cancelled()
        # Claim the tools that have run since the last call; on_llm_end records
        # them with the invocation this start belongs to.
        self._tools_for_this_call = self._tools_pending_llm
        self._tools_pending_llm = []
        line = f"[llm_start] model={model_name or 'unknown'}"
        append_log(self.log_lines, line, self.log_file)
        self.thinking_history.append(line)
        self._emit({"type": "thinking", "message": line})
        # Fresh LLM call — reset the per-call native-reasoning stream state.
        self._think_filter.reset()
        self._native_reasoning_emitted = False
        self._native_reasoning_buf = ""

    def _emit_think_delta(self, delta: str):
        """Stream a slice of the model's in-progress reasoning.

        Native reasoning is only surfaced as a ``think`` step once it is complete
        (the ``</think>`` tag closes, or the answer starts) — which for a long
        thought leaves the chat showing nothing but a "working" indicator for
        many seconds. These deltas let the UI run the thought as a live ticker
        underneath it; the completed ``think`` event replaces the ticker with the
        collapsible thought block. Live-only: not logged, not part of the run
        record, since ``think`` carries the same text in full.
        """
        if not delta:
            return
        self._emit({"type": "think_delta", "delta": delta})

    def _emit_native_reasoning(self, content: str):
        """Surface native model reasoning as a `think` step in the trail."""
        if not content:
            return
        self._step += 1
        # One line, JSON-escaped content: the chat log is re-parsed line-by-line
        # (=== Message blocks, [marker] regexes), so the thought must not span
        # lines. Parsers decode it back for the insights thinking trace.
        line = f"[reasoning] step={self._step} content={json.dumps(content, ensure_ascii=False)}"
        append_log(self.log_lines, line, self.log_file)
        self.thinking_history.append(line)
        self._native_reasoning_emitted = True
        self._emit({
            "type": "think",
            "step": self._step,
            "content": content,
            "native": True,
        })

    def on_chat_model_start(self, serialized, messages, **kwargs):
        model_name = serialized.get("name") if isinstance(serialized, dict) else None
        flat: list = []
        try:
            for batch in (messages or []):
                flat.extend(batch or [])
        except Exception:
            flat = []
        try:
            self._last_prompt_struct = build_prompt_struct(flat)
        except Exception:
            self._last_prompt_struct = {}
        try:
            self._last_prompt_text = "\n".join(
                f"{it['role']}: {it['content']}"
                for it in (message_to_role_content(m) for m in flat)
            )
        except Exception:
            self._last_prompt_text = ""
        self._llm_started(model_name)

    def on_llm_start(self, serialized, prompts, **kwargs):
        model_name = None
        if isinstance(serialized, dict):
            model_name = serialized.get("name")
        try:
            if isinstance(prompts, list):
                self._last_prompt_text = "\n".join(str(p) for p in prompts if p is not None)
            else:
                self._last_prompt_text = str(prompts or "")
        except Exception:
            self._last_prompt_text = ""
        self._last_prompt_struct = {
            "system_prompt": "",
            "history": [],
            "user_message": self._last_prompt_text,
        }
        self._llm_started(model_name)

    def on_llm_new_token(self, token, **kwargs):
        # Raising (not returning) aborts the provider's streaming loop mid-
        # generation, so a stop takes effect immediately instead of after the
        # model finishes its answer.
        self._abort_if_cancelled()
        # Reasoning streamed as a separate field (gpt-oss via LM Studio): the
        # chunk carries a reasoning_content delta and usually no content text.
        # Accumulate it; it is emitted as one think event when the answer starts.
        chunk = kwargs.get("chunk")
        if chunk is not None:
            message = getattr(chunk, "message", None)
            ak = getattr(message, "additional_kwargs", None) or {}
            delta = ak.get("reasoning_content") or ak.get("reasoning")
            if not isinstance(delta, str) or not delta:
                # Anthropic-style: the reasoning arrives as content blocks on the
                # chunk rather than a scalar field, with no visible token text.
                delta = _chunk_reasoning_blocks(getattr(message, "content", None))
            if isinstance(delta, str) and delta:
                self._native_reasoning_buf += delta
                self._emit_think_delta(delta)
        text = token_text(token, chunk)
        if not text:
            return
        self._output_text_parts.append(text)
        # Divert inline <think> reasoning out of the answer stream: visible
        # text streams as tokens; the reasoning is emitted as ONE think event
        # at the moment its tag closes, so the trail keeps execution order.
        visible, reasoning = self._think_filter.feed(text)
        # Text consumed inside a still-open <think> block: stream it live so the
        # thought is visible while it is being written, not only once it closes.
        self._emit_think_delta(self._think_filter.reasoning_delta)
        if reasoning:
            self._emit_native_reasoning(reasoning)
        if visible:
            # Field-streamed reasoning precedes the answer: flush it first so
            # the trail keeps execution order (thought, then answer).
            if self._native_reasoning_buf.strip():
                self._emit_native_reasoning(self._native_reasoning_buf.strip())
                self._native_reasoning_buf = ""
            # Drop the leading whitespace left behind after a `</think>` close so
            # the answer doesn't render under an empty block (see __init__).
            if not self._answer_started:
                visible = visible.lstrip()
                if not visible:
                    return
                self._answer_started = True
            visible = self._collapse_newlines(visible)
            if visible:
                self._emit({"type": "token", "token": visible})

    # Max consecutive newlines kept in the streamed answer. 2 keeps a single
    # blank line between paragraphs while dropping larger gaps; 1 removes blank
    # lines entirely. The final bubble is restored from the agent's clean output
    # on `done`, so this only affects the live stream.
    _MAX_STREAM_NEWLINES = 2

    def _collapse_newlines(self, text: str) -> str:
        """Cap runs of newlines in the streamed answer (stateful across tokens).

        Models emit stray blank lines between segments and before tool calls,
        which pile up as empty lines in the chat bubble while streaming. Extra
        newlines beyond ``_MAX_STREAM_NEWLINES`` are dropped; ``self._nl_run``
        carries the trailing count between tokens so a run split across token
        boundaries is still collapsed.
        """
        if not text:
            return text
        out: list[str] = []
        for ch in text:
            if ch == "\n":
                if self._nl_run >= self._MAX_STREAM_NEWLINES:
                    continue
                self._nl_run += 1
            else:
                self._nl_run = 0
            out.append(ch)
        return "".join(out)

    def bind_model(self, provider: str, model: str) -> None:
        """Resolve the context window of the model this run actually uses.

        Called by every chat surface right after ``create_agent``, because the
        model is chosen there (workspace override → settings → env) and the
        callback has no other way to learn it. Failure is not worth reporting:
        an unknown window simply means no context meter this run.
        """
        try:
            from providers.context_windows import get_model_context_window
            self.context_window = int(get_model_context_window(provider or "", model or ""))
        except Exception:
            self.context_window = 0

    def context_usage(self) -> dict:
        """Context fill for this run: the largest prompt sent, against the window."""
        return {
            "context_window": self.context_window,
            "context_used": self.max_prompt_tokens,
        }

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        # Heuristic fallback when provider usage metadata is unavailable.
        if not text:
            return 0
        return max(1, int(math.ceil(len(text) / 4)))

    def on_llm_end(self, response, **kwargs):
        try:
            raw_payload = {
                "response_type": response.__class__.__name__,
                "llm_output": to_json_safe(getattr(response, "llm_output", None)),
                "generations": to_json_safe(getattr(response, "generations", None)),
            }
            self.llm_invoke_responses.append(raw_payload)
        except Exception:
            pass

        # Flush the think-tag stream filter: an unclosed <think> becomes
        # reasoning; a leftover partial tag that never completed is plain text.
        try:
            leftover_visible, leftover_reasoning = self._think_filter.flush()
        except Exception:
            leftover_visible, leftover_reasoning = "", ""
        if leftover_visible and not self.cancelled:
            if not self._answer_started:
                leftover_visible = leftover_visible.lstrip()
            if leftover_visible:
                self._answer_started = True
                leftover_visible = self._collapse_newlines(leftover_visible)
                if leftover_visible:
                    self._emit({"type": "token", "token": leftover_visible})
        if leftover_reasoning:
            self._emit_native_reasoning(leftover_reasoning)
        # Field-streamed reasoning with no answer text after it (e.g. a
        # tool-call-only turn): flush it now so the thought still shows.
        if self._native_reasoning_buf.strip():
            self._emit_native_reasoning(self._native_reasoning_buf.strip())
            self._native_reasoning_buf = ""

        # Native model reasoning (LM Studio/Ollama reasoning models): surface it
        # like a `think` step so the UI renders it in the reasoning trail. The
        # think-tool path is untouched — this only adds what the model itself
        # emitted. Skipped when the token stream already surfaced it (streaming
        # runs), so each thought appears exactly once and in execution order;
        # non-streaming runs and the separate reasoning_content field land here.
        if not self._native_reasoning_emitted:
            try:
                from reasoning.native_reasoning import extract_reasoning_from_llm_result
                native = extract_reasoning_from_llm_result(response)
            except Exception:
                native = ""
            if native:
                self._emit_native_reasoning(native)

        usage = {}
        try:
            usage = (getattr(response, "llm_output", None) or {}).get("token_usage", {}) or {}
        except Exception:
            usage = {}

        if not usage:
            try:
                gens = getattr(response, "generations", []) or []
                for grp in gens:
                    for g in grp:
                        md = getattr(getattr(g, "message", None), "response_metadata", None) or {}
                        tu = md.get("token_usage") or md.get("usage") or {}
                        if tu:
                            usage = tu
                            break
                    if usage:
                        break
            except Exception:
                usage = {}

        if not usage:
            # More providers expose usage on AIMessage.usage_metadata.
            try:
                gens = getattr(response, "generations", []) or []
                for grp in gens:
                    for g in grp:
                        um = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                        if um:
                            usage = {
                                "prompt_tokens": um.get("input_tokens"),
                                "completion_tokens": um.get("output_tokens"),
                                "total_tokens": um.get("total_tokens"),
                                # Kept so the cache-read count survives here too.
                                "input_token_details": um.get("input_token_details") or {},
                            }
                            break
                    if usage:
                        break
            except Exception:
                usage = {}

        p = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        c = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        t = int(usage.get("total_tokens") or (p + c))
        cached = cached_input_tokens(usage)

        estimated = False
        if p == 0 and c == 0 and t == 0:
            p = self._estimate_tokens(self._last_prompt_text)
            c = self._estimate_tokens("".join(self._output_text_parts))
            t = p + c
            estimated = True
            cached = 0  # an estimate cannot tell a cache hit from a fresh token

        cached = min(cached, p)
        self.prompt_tokens += p
        self.completion_tokens += c
        self.total_tokens += t
        self.cached_prompt_tokens += cached
        # The prompt of a single call *is* the context in use; the running sum
        # above is what the turn cost, which is a different question.
        self.max_prompt_tokens = max(self.max_prompt_tokens, p)

        line = (
            f"[llm_usage] prompt_tokens={p} completion_tokens={c} total_tokens={t}"
            + (f" cached_tokens={cached}" if cached else "")
            + (" estimated=true" if estimated else "")
        )
        append_log(self.log_lines, line, self.log_file)
        self._emit({
            "type": "usage",
            "prompt_tokens": p,
            "completion_tokens": c,
            "total_tokens": t,
            "cached_tokens": cached,
            "estimated": estimated,
            **self.context_usage(),
        })

        # Record this LLM call (tool-driven once any tool has run this turn).
        try:
            resp_text = ""
            for grp in (getattr(response, "generations", []) or []):
                for g in grp:
                    txt = getattr(g, "text", None) or content_text(
                        getattr(getattr(g, "message", None), "content", None))
                    if txt:
                        resp_text += txt
            self.llm_invocations.append({
                "kind": "tool" if self._tool_was_used else "llm",
                # The tools whose results this call was given, in call order.
                "tools": list(self._tools_for_this_call),
                "system_prompt": self._last_prompt_struct.get("system_prompt", ""),
                "history": self._last_prompt_struct.get("history", []),
                "user_message": self._last_prompt_struct.get("user_message", ""),
                "response": resp_text,
                "token_usage": {
                    "inbound_tokens": p,
                    "outbound_tokens": c,
                    "total_tokens": t,
                },
            })
        except Exception:
            pass

    def on_tool_start(self, serialized, input_str, **kwargs):
        self._abort_if_cancelled()
        self._step += 1
        self.tool_calls += 1
        self._tool_was_used = True
        name = serialized.get("name") if isinstance(serialized, dict) else "tool"
        self._tools_pending_llm.append(str(name))
        input_full = str(input_str)
        formatted = format_tool_payload(input_str)
        if len(formatted) > 2000:
            formatted = formatted[:2000] + "... (truncated)"
        line = f"[tool_start] step={self._step} tool={name} input={formatted}"
        append_log(self.log_lines, line, self.log_file)
        self._pending_tool = {
            "step": self._step, "tool": name, "input": input_full,
            "_started": time.perf_counter(),
        }
        # The reasoning tools (think/plan) are pass-through scratchpads: their
        # input *is* the content. Surface them as dedicated events so the UI can
        # render a reasoning/plan panel instead of a generic tool call, and skip
        # the generic tool_start/tool_end (the echoed output adds nothing).
        if name in ("think", "plan"):
            self._emit({
                "type": name,
                "step": self._step,
                "content": extract_reasoning_text(input_str),
            })
        else:
            self._emit({"type": "tool_start", "step": self._step, "tool": name, "input": input_full})

    def on_tool_end(self, output, **kwargs):
        # Stop pressed while the tool ran: abort now, before the next LLM call.
        self._abort_if_cancelled()
        output_full = str(output)
        formatted = format_tool_payload(output)
        if len(formatted) > 2000:
            formatted = formatted[:2000] + "... (truncated)"
        line = f"[tool_end] output={formatted}"
        append_log(self.log_lines, line, self.log_file)
        pending = self._pending_tool
        if pending is not None:
            entry = dict(pending)
            entry["output"] = output_full
            dur = int((time.perf_counter() - entry.pop("_started", time.perf_counter())) * 1000)
            # One consolidated marker per tool call: step, name, duration only.
            mark = f"[tool_call] {_now()} step={entry.get('step')} tool={entry.get('tool')} duration_ms={dur}"
            append_log(self.log_lines, mark, self.log_file)
            self.thinking_history.append(mark)
            self.tool_history.append(entry)
            self._pending_tool = None
        # think/plan already surfaced their content on tool_start; no generic end.
        if pending is not None and pending.get("tool") in ("think", "plan"):
            return
        self._emit({"type": "tool_end", "output": output_full})

    def on_llm_error(self, error, **kwargs):
        line = f"[llm_error] {type(error).__name__}: {error}"
        append_log(self.log_lines, line, self.log_file)
        self._emit({"type": "error", "source": "llm", "error": str(error)})

    def on_tool_error(self, error, **kwargs):
        tool_name = (self._pending_tool or {}).get("tool", "unknown")
        line = f"[tool_error] tool={tool_name} {type(error).__name__}: {error}"
        append_log(self.log_lines, line, self.log_file)
        if self._pending_tool is not None:
            entry = dict(self._pending_tool)
            entry["output"] = f"ERROR: {error}"
            dur = int((time.perf_counter() - entry.pop("_started", time.perf_counter())) * 1000)
            mark = (
                f"[tool_call] {_now()} step={entry.get('step')} "
                f"tool={entry.get('tool')} duration_ms={dur} status=error"
            )
            append_log(self.log_lines, mark, self.log_file)
            self.thinking_history.append(mark)
            self.tool_history.append(entry)
            self._pending_tool = None
        self._emit({"type": "tool_error", "tool": tool_name, "error": str(error)})

    def on_chain_error(self, error, **kwargs):
        line = f"[chain_error] {type(error).__name__}: {error}"
        append_log(self.log_lines, line, self.log_file)
        self._emit({"type": "error", "source": "chain", "error": str(error)})

    def record_artifact(self, op: str, path: str, before: str | None, after: str | None) -> None:
        """Receive a file change from the filesystem tools (via the artifact sink).

        Builds a diff record, stores it for the run's process payload, and emits
        an ``artifact`` SSE event so the UI can render the diff live. Called from
        the agent's execution thread, so it must be thread-safe — _emit already
        marshals onto the event loop.
        """
        try:
            artifact = build_artifact(op, path, before, after)
        except Exception:
            return
        self.artifact_history.append(artifact)
        line = f"[artifact] op={op} path={path} +{artifact.get('additions', 0)} -{artifact.get('deletions', 0)}"
        append_log(self.log_lines, line, self.log_file)
        self._emit({"type": "artifact", **artifact})


class FileStatsCallback(StatsCollectorCallback):
    """Stats collector that appends chat-log-format markers to a log file.

    In-process worker runs (taskless delegation via ``run_agent_tool``) have no
    subprocess stdout tee and no SSE stream, so without this they leave no
    per-run log. This writes the same [llm_start]/[tool_start]/[tool_end]/
    [tool_call]/[llm_usage] lines ChatStreamCallback produces, so the chat-log
    parser (``_extract_chat_message_runs``) reads the run like any other chat
    message. The caller owns the surrounding log scaffold (=== Message block
    header, response text, [message_summary], Finished/Status footer) and must
    call ``close()`` when the run ends.
    """

    def __init__(self, log_path: Path) -> None:
        super().__init__()
        self._f = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115

    def write(self, line: str) -> None:
        try:
            self._f.write(line + "\n")
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass

    # -- emit hooks: route the base class's markers into the file --
    def _emit_thinking(self, line: str) -> None:
        self.write(line)

    def _emit_tool_start(self, name: str, input_full: str) -> None:
        formatted = format_tool_payload(input_full)
        if len(formatted) > 2000:
            formatted = formatted[:2000] + "... (truncated)"
        self.write(f"[tool_start] step={self._tool_step} tool={name} input={formatted}")

    def _emit_tool_end(self, entry: dict) -> None:
        formatted = format_tool_payload(entry.get("output", ""))
        if len(formatted) > 2000:
            formatted = formatted[:2000] + "... (truncated)"
        self.write(f"[tool_end] output={formatted}")

    def on_llm_end(self, response, **kwargs):
        # The base accumulates totals; the chat log wants per-call usage lines.
        before = (self.prompt_tokens, self.completion_tokens, self.total_tokens)
        super().on_llm_end(response, **kwargs)
        self.write(
            f"[llm_usage] prompt_tokens={self.prompt_tokens - before[0]} "
            f"completion_tokens={self.completion_tokens - before[1]} "
            f"total_tokens={self.total_tokens - before[2]}"
        )

    def on_llm_error(self, error, **kwargs):
        self.write(f"[llm_error] {type(error).__name__}: {error}")

    def on_chain_error(self, error, **kwargs):
        self.write(f"[chain_error] {type(error).__name__}: {error}")


class DelegationStreamCallback(BaseCallbackHandler):
    """Forward a delegated child agent's tool/thought events to the parent stream.

    Attached (alongside the child's ``FileStatsCallback``) by ``run_agent_tool``
    only when a parent stream emitter is present. It emits the same event
    vocabulary the browser already understands (``think`` / ``plan`` /
    ``tool_start`` / ``tool_end`` / ``tool_error``) but tags each payload with
    ``delegation: true``, the child ``run_id``, the child ``agent_id``, and the
    nesting ``depth``, so the UI renders them as a live nested block under the
    parent's ``run_agent_tool`` call instead of at the top level.

    Streaming is best-effort decoration of the child run: ``raise_error`` stays
    False so a forwarding hiccup never aborts the delegated work (the child's own
    FileStatsCallback and RunStopCallback own logging and cancellation).
    """

    raise_error: bool = False

    def __init__(self, emit, *, run_id: str, agent_id: str, depth: int):
        self._emit = emit
        self.run_id = run_id
        self.agent_id = agent_id
        self.depth = depth
        self._step = 0
        self._pending_tool: dict | None = None

    def _send(self, payload: dict) -> None:
        event = {
            "delegation": True,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "depth": self.depth,
        }
        event.update(payload)
        try:
            self._emit(event)
        except Exception:
            # Best-effort: a broken stream must not break the delegated run.
            pass

    def on_tool_start(self, serialized, input_str, **kwargs):
        self._step += 1
        name = serialized.get("name") if isinstance(serialized, dict) else "tool"
        # think/plan are reasoning scratchpads: their input IS the thought. Surface
        # them as think/plan events (matching ChatStreamCallback) and remember not
        # to emit a redundant generic tool_end for them.
        if name in ("think", "plan"):
            self._pending_tool = {"tool": name}
            self._send({"type": name, "step": self._step, "content": extract_reasoning_text(input_str)})
        else:
            self._pending_tool = {"tool": name, "step": self._step}
            self._send({"type": "tool_start", "step": self._step, "tool": name, "input": str(input_str)})

    def on_tool_end(self, output, **kwargs):
        pending = self._pending_tool
        self._pending_tool = None
        if pending is not None and pending.get("tool") in ("think", "plan"):
            return
        self._send({"type": "tool_end", "output": str(output)})

    def on_tool_error(self, error, **kwargs):
        tool = (self._pending_tool or {}).get("tool", "unknown")
        self._pending_tool = None
        self._send({"type": "tool_error", "tool": tool, "error": str(error)})
