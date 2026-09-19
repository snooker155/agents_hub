"""
Shared LLM/tool stats collection for agent-run callbacks.

Three callbacks historically duplicated the same token-usage extraction and
field bookkeeping: ``RunStatsCallback`` (subprocess runs), ``ChatStreamCallback``
(chat SSE) and ``SessionChatCallback`` (in-process session runs). This module
holds the common pieces once:

- ``to_json_safe(value)``           — best-effort JSON-safe conversion.
- ``extract_token_usage(response)`` — the 3-tier usage fallback
  (``llm_output.token_usage`` → ``response_metadata`` → ``usage_metadata``).
- ``StatsCollectorCallback``        — base that accumulates prompt/completion
  tokens, tool history, thinking history, and LLM responses, and exposes
  ``build_process(duration_ms)``. Subclasses add their distinctive behaviour
  (printing, SSE emit, asyncio queue) by overriding the small ``_on_*`` hooks.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional

from langchain_core.callbacks import BaseCallbackHandler


def content_text(content: Any) -> str:
    """Flatten a message content (string or block list) to plain text.

    Providers that stream structured content (the OpenAI Responses API,
    Anthropic) carry the answer in ``{"type": "text", "text": …}`` blocks rather
    than a bare string.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                if block.get("type") in (None, "text", "output_text"):
                    parts.append(block["text"])
        return "".join(parts)
    return ""


def token_text(token: Any, chunk: Any = None) -> str:
    """The plain text of one streamed token.

    ``BaseChatModel.stream`` hands the callback ``chunk.message.content`` as-is
    and merely casts it to ``str`` for the type checker, so a provider streaming
    block lists delivers a *list* here. Stringifying that pours the block repr
    into the answer ("[{'type': 'text', 'text': 'Hi', 'index': 1}]"), so take
    the generation chunk's own text when there is one and flatten otherwise.
    """
    if isinstance(token, str):
        return token
    if chunk is not None:
        text = getattr(chunk, "text", None)
        if isinstance(text, str):
            return text
    return content_text(token)


def to_json_safe(value: Any, *, _depth: int = 0, _max_depth: int = 5) -> Any:
    """Best-effort conversion of callback payloads to JSON-safe structures."""
    if _depth >= _max_depth:
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): to_json_safe(v, _depth=_depth + 1, _max_depth=_max_depth)
                for k, v in list(value.items())[:200]}
    if isinstance(value, (list, tuple)):
        return [to_json_safe(v, _depth=_depth + 1, _max_depth=_max_depth)
                for v in list(value)[:200]]
    for meth in ("model_dump", "dict"):
        fn = getattr(value, meth, None)
        if callable(fn):
            try:
                return to_json_safe(fn(), _depth=_depth + 1, _max_depth=_max_depth)
            except Exception:
                pass
    if hasattr(value, "__dict__"):
        try:
            return to_json_safe(vars(value), _depth=_depth + 1, _max_depth=_max_depth)
        except Exception:
            pass
    return str(value)


def extract_token_usage(response: Any) -> Dict[str, Any]:
    """Pull a token-usage dict from a LangChain LLMResult, trying three sources.

    Returns a dict that may contain prompt/completion/total under various key
    names; callers normalise via ``normalize_usage``.
    """
    # 1) llm_output.token_usage
    try:
        usage = (getattr(response, "llm_output", None) or {}).get("token_usage", {}) or {}
        if usage:
            return usage
    except Exception:
        pass
    # 2) generations[*].message.response_metadata.token_usage|usage
    try:
        for grp in (getattr(response, "generations", []) or []):
            for g in grp:
                md = getattr(getattr(g, "message", None), "response_metadata", None) or {}
                tu = md.get("token_usage") or md.get("usage") or {}
                if tu:
                    return tu
    except Exception:
        pass
    # 3) generations[*].message.usage_metadata (input/output/total_tokens)
    try:
        for grp in (getattr(response, "generations", []) or []):
            for g in grp:
                um = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                if um:
                    return {
                        "prompt_tokens": um.get("input_tokens"),
                        "completion_tokens": um.get("output_tokens"),
                        "total_tokens": um.get("total_tokens"),
                        # Carried through verbatim so cached_input_tokens() can
                        # still find the cache-read count after normalisation.
                        "input_token_details": um.get("input_token_details") or {},
                    }
    except Exception:
        pass
    return {}


def normalize_usage(usage: Dict[str, Any]) -> tuple[int, int, int]:
    """Return (prompt, completion, total) ints from a raw usage dict."""
    p = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    c = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    t = int(usage.get("total_tokens") or (p + c))
    return p, c, t


def _as_mapping(value: Any) -> Dict[str, Any]:
    """A nested usage detail as a dict, whether the SDK handed us one or a
    pydantic object (``prompt_tokens_details`` is an object on some versions)."""
    if isinstance(value, dict):
        return value
    for attr in ("model_dump", "dict"):
        fn = getattr(value, attr, None)
        if callable(fn):
            try:
                out = fn()
                if isinstance(out, dict):
                    return out
            except Exception:
                pass
    return {}


def cached_input_tokens(usage: Dict[str, Any]) -> int:
    """How many prompt tokens the provider served from its prompt cache.

    An agent loop re-sends the whole conversation every step, so most of each
    prompt after the first is a cache hit billed at a fraction of the input
    rate. Every provider names the count differently: OpenAI nests it under
    ``prompt_tokens_details`` (chat completions) or ``input_tokens_details``
    (responses), Anthropic reports ``cache_read_input_tokens`` at the top level,
    and LangChain's normalised ``usage_metadata`` uses
    ``input_token_details.cache_read``. A provider that reports none yields 0,
    which prices the run exactly the way it was priced before.
    """
    if not isinstance(usage, dict):
        return 0

    def _int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    for key in ("cache_read_input_tokens", "cached_tokens", "cache_read"):
        if usage.get(key):
            return _int(usage[key])
    for holder in ("prompt_tokens_details", "input_tokens_details", "input_token_details"):
        detail = _as_mapping(usage.get(holder))
        for key in ("cached_tokens", "cache_read"):
            if detail.get(key):
                return _int(detail[key])
    return 0


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) when the provider reports none."""
    if not text:
        return 0
    return max(1, int(math.ceil(len(text) / 4)))


def _now() -> str:
    """Wall-clock HH:MM:SS used to timestamp thinking-trace markers."""
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")


# Map LangChain message ``type`` values to the roles used in the stored context.
_MESSAGE_ROLE_BY_TYPE = {
    "system": "system",
    "human": "user",
    "ai": "assistant",
    "tool": "tool",
    "function": "tool",
}


def message_to_role_content(msg: Any) -> Dict[str, str]:
    """Normalise a LangChain ``BaseMessage`` (or dict) to ``{role, content}``."""
    msg_type = getattr(msg, "type", None)
    if msg_type is None and isinstance(msg, dict):
        msg_type = msg.get("type") or msg.get("role")
    role = _MESSAGE_ROLE_BY_TYPE.get(str(msg_type or "").lower(), str(msg_type or "unknown"))
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    if not isinstance(content, str):
        content = to_json_safe(content)
        if not isinstance(content, str):
            try:
                import json
                content = json.dumps(content, ensure_ascii=False)
            except Exception:
                content = str(content)
    return {"role": role, "content": content}


def build_prompt_struct(messages: List[Any]) -> Dict[str, Any]:
    """Split a flat chat-message list into system / history / current user.

    - ``system_prompt``: concatenation of all leading system messages.
    - ``user_message``: the content of the last human message.
    - ``history``: the conversational turns in between. Tool plumbing is
      excluded — ``tool`` observation messages and tool-call-only assistant
      turns (empty text content) belong in the separate ``tool_calls`` block,
      so ``history`` stays a clean user/assistant transcript.
    """
    items = [message_to_role_content(m) for m in (messages or [])]
    system_parts = [it["content"] for it in items if it["role"] == "system"]
    non_system = [it for it in items if it["role"] != "system"]

    last_user_idx = next(
        (i for i in range(len(non_system) - 1, -1, -1) if non_system[i]["role"] == "user"),
        None,
    )
    if last_user_idx is None:
        user_message = ""
        history = non_system
    else:
        user_message = non_system[last_user_idx]["content"]
        # Only genuine prior turns (before the current user message) are history.
        # Messages AFTER it are the agent's own intra-run loop output — the
        # intermediate assistant/tool turns ("middle response tokens"). Those
        # belong in the live chat bubble and the thinking trace, never in the
        # stored conversation history, so they are dropped here.
        history = non_system[:last_user_idx]

    # Drop tool observations and empty tool-call-only assistant turns from the
    # conversational history; those are surfaced in the tools block instead.
    history = [
        it for it in history
        if it["role"] != "tool" and (it["role"] != "assistant" or str(it["content"]).strip())
    ]

    return {
        "system_prompt": "\n\n".join(system_parts),
        "history": history,
        "user_message": user_message,
    }


class StatsCollectorCallback(BaseCallbackHandler):
    """Accumulates token/tool/thinking stats during an agent run.

    Subclasses override the ``_emit_*`` hooks (no-ops here) to add streaming,
    printing, or logging without re-implementing the stats bookkeeping.
    """

    raise_error: bool = True

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        # Subset of prompt_tokens the provider served from its cache. Priced
        # separately by common.pricing, so it is counted separately here.
        self.cached_prompt_tokens = 0
        self.tool_calls = 0
        self._last_prompt_text = ""
        # Structured snapshot of the prompt for the most recent LLM call:
        # {system_prompt, history: [{role, content}], user_message}.
        self._last_prompt_struct: Dict[str, Any] = {}
        self._output_text_parts: List[str] = []
        self.tool_history: List[Dict[str, Any]] = []
        self.thinking_history: List[str] = []
        self.llm_invoke_responses: List[Dict[str, Any]] = []
        # Per-LLM-call records, each tagged whether it followed a tool call.
        self.llm_invocations: List[Dict[str, Any]] = []
        self._pending_tool: Optional[Dict[str, Any]] = None
        # True once a tool has run, so the next LLM call is "tool-driven".
        self._tool_was_used = False
        self._tool_step = 0

    # -- hooks for subclasses (default: no-op) --
    def _emit_thinking(self, line: str) -> None: ...
    def _emit_token(self, token: str) -> None: ...
    def _emit_tool_start(self, name: str, input_full: str) -> None: ...
    def _emit_tool_end(self, entry: Dict[str, Any]) -> None: ...

    # -- LLM lifecycle --
    @staticmethod
    def _model_name(serialized) -> Optional[str]:
        return (serialized or {}).get("name") if isinstance(serialized, dict) else None

    def _start(self, model_name: Optional[str]) -> None:
        line = f"[llm_start] model={model_name or 'unknown'}"
        self.thinking_history.append(line)
        self._emit_thinking(line)

    def on_chat_model_start(self, serialized, messages, **_):
        """Chat models deliver structured ``BaseMessage`` lists — capture the
        system prompt, history and current user message as an object."""
        flat: List[Any] = []
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
                f"{it['role']}: {it['content']}" for it in
                (message_to_role_content(m) for m in flat)
            )
        except Exception:
            self._last_prompt_text = ""
        self._start(self._model_name(serialized))

    def on_llm_start(self, serialized, prompts, **_):
        # Text-completion path (no structured messages); keep the raw string and
        # represent it as a single user message in the struct.
        try:
            self._last_prompt_text = (
                "\n".join(str(p) for p in prompts if p is not None)
                if isinstance(prompts, list) else str(prompts or "")
            )
        except Exception:
            self._last_prompt_text = ""
        self._last_prompt_struct = {
            "system_prompt": "",
            "history": [],
            "user_message": self._last_prompt_text,
        }
        self._start(self._model_name(serialized))

    def on_llm_new_token(self, token, **kwargs):
        text = token_text(token, kwargs.get("chunk"))
        if text:
            self._output_text_parts.append(text)
            self._emit_token(text)

    @staticmethod
    def _response_text(response) -> str:
        """Best-effort extraction of the generated text from an LLMResult."""
        try:
            parts = []
            for grp in (getattr(response, "generations", []) or []):
                for g in grp:
                    txt = getattr(g, "text", None)
                    if not txt:
                        txt = content_text(
                            getattr(getattr(g, "message", None), "content", None))
                    if txt:
                        parts.append(txt)
            return "\n".join(parts)
        except Exception:
            return ""

    def on_llm_end(self, response, **_):
        try:
            self.llm_invoke_responses.append({
                "response_type": response.__class__.__name__,
                "llm_output": to_json_safe(getattr(response, "llm_output", None)),
                "generations": to_json_safe(getattr(response, "generations", None)),
            })
        except Exception:
            pass

        usage = extract_token_usage(response)
        p, c, t = normalize_usage(usage)
        cached = cached_input_tokens(usage)
        if p == 0 and c == 0 and t == 0:
            p = estimate_tokens(self._last_prompt_text)
            c = estimate_tokens("".join(self._output_text_parts))
            t = p + c
            cached = 0  # an estimate cannot tell a cache hit from a fresh token
        self.prompt_tokens += p
        self.completion_tokens += c
        self.total_tokens += t
        self.cached_prompt_tokens += min(cached, p)

        # Record this LLM call: tool-driven if a tool has already run in this run.
        try:
            self.llm_invocations.append({
                "kind": "tool" if self._tool_was_used else "llm",
                "system_prompt": self._last_prompt_struct.get("system_prompt", ""),
                "history": self._last_prompt_struct.get("history", []),
                "user_message": self._last_prompt_struct.get("user_message", ""),
                "response": self._response_text(response),
                "token_usage": {
                    "inbound_tokens": p,
                    "outbound_tokens": c,
                    "total_tokens": t,
                    "cached_tokens": min(cached, p),
                },
            })
        except Exception:
            pass

    # -- tool lifecycle --
    def _mark(self, line: str) -> None:
        """Append a chronological marker to the thinking trace and emit it."""
        self.thinking_history.append(line)
        self._emit_thinking(line)

    def on_tool_start(self, serialized, input_str, **_):
        self.tool_calls += 1
        self._tool_was_used = True
        self._tool_step += 1
        name = (serialized or {}).get("name") if isinstance(serialized, dict) else "tool"
        input_full = str(input_str)
        self._pending_tool = {
            "tool": name, "input": input_full, "step": self._tool_step,
            "_started": time.perf_counter(),
        }
        self._emit_tool_start(name, input_full)

    def _mark_tool(self, entry: Dict[str, Any], *, ok: bool) -> None:
        """Emit a single completed-tool marker with step, name and duration."""
        dur = int((time.perf_counter() - entry.get("_started", time.perf_counter())) * 1000)
        status = "" if ok else " status=error"
        self._mark(
            f"[tool_call] {_now()} step={entry.get('step')} "
            f"tool={entry.get('tool')} duration_ms={dur}{status}"
        )

    def on_tool_end(self, output, **_):
        output_full = str(output)
        if self._pending_tool is not None:
            entry = {**self._pending_tool, "output": output_full}
            self._mark_tool(entry, ok=True)
            self.tool_history.append({k: v for k, v in entry.items() if not k.startswith("_")})
            self._pending_tool = None
            self._emit_tool_end(entry)

    def on_tool_error(self, error, **_):
        if self._pending_tool is not None:
            entry = {**self._pending_tool, "output": f"ERROR: {error}"}
            self._mark_tool(entry, ok=False)
            self.tool_history.append({k: v for k, v in entry.items() if not k.startswith("_")})
            self._pending_tool = None
            self._emit_tool_end(entry)

    def build_process(self, duration_ms: int) -> Dict[str, Any]:
        """Assemble the canonical ``process`` payload stored on the run record.

        Shape matches ``common.run_payloads``: input context as blocks, the
        response as {text, structured}, the trace as ``reasoning`` — identical
        for every execution mode. ``response.structured`` is filled in later by
        ``run_manager.close_run_from_result`` when the agent emitted one.
        """
        # The final LLM call carries the freshest system prompt / history / user
        # message; the run output is the response of that last invocation.
        last = self._last_prompt_struct or {}
        final_response = self.llm_invocations[-1]["response"] if self.llm_invocations else ""
        return {
            "input_context": {
                "system_prompt": last.get("system_prompt", ""),
                "history": last.get("history", []),
                "user_message": last.get("user_message", ""),
            },
            "response": {"text": final_response, "structured": None},
            "tool_calls": self.tool_history,
            "reasoning": self.thinking_history,
            "llm_invocations": self.llm_invocations,
            "llm_raw_responses": self.llm_invoke_responses,
            "token_usage": {
                "inbound_tokens": self.prompt_tokens,
                "outbound_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "cached_tokens": self.cached_prompt_tokens,
            },
            "duration_ms": duration_ms,
        }


class RunStatsCallback(StatsCollectorCallback):
    """Stats collector for subprocess agent runs — prints progress to stdout
    (captured into the run log file)."""

    def _emit_thinking(self, line: str) -> None:
        print(line)

    def _emit_tool_start(self, name: str, input_full: str) -> None:
        preview = input_full[:240] + "..." if len(input_full) > 240 else input_full
        print(f"[tool_start] tool={name} input={preview}")

    def _emit_tool_end(self, entry: Dict[str, Any]) -> None:
        out = str(entry.get("output", ""))
        preview = out[:240] + "..." if len(out) > 240 else out
        print(f"[tool_end] output={preview}")

    def on_llm_end(self, response, **_):
        super().on_llm_end(response, **_)
        print(
            f"[llm_usage] prompt_tokens={self.prompt_tokens} "
            f"completion_tokens={self.completion_tokens} total_tokens={self.total_tokens}"
        )

    def on_llm_error(self, error, **_):
        print(f"[llm_error] {type(error).__name__}: {error}")

    def on_tool_error(self, error, **_):
        tool_name = (self._pending_tool or {}).get("tool", "unknown")
        print(f"[tool_error] tool={tool_name} {type(error).__name__}: {error}")
        super().on_tool_error(error, **_)

    def on_chain_error(self, error, **_):
        print(f"[chain_error] {type(error).__name__}: {error}")
