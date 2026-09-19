"""
Streaming callbacks that forward agent events to subscribers.

- ``SessionPublishCallback`` — POSTs token/tool events to the local dashboard's
  session SSE endpoint. Used by subprocess runs that are session continuations.

The chat/session in-process streaming callbacks (``ChatStreamCallback`` in
routes/chat.py and the session-chat callback in routes/sessions.py) remain in
their route modules because they are tightly coupled to per-request asyncio
queues and artifact diffing; both now subclass
``agents.callbacks.run_statistics.StatsCollectorCallback`` to share the token/tool
bookkeeping.
"""
from __future__ import annotations

import threading
import time

from langchain_core.callbacks import BaseCallbackHandler

from agents.callbacks.run_statistics import token_text

# Token batching. A streaming completion fires on_llm_new_token hundreds of times;
# one HTTP POST per token would put a synchronous request between every token of
# every agent run. Tokens are instead accumulated and shipped as one event when
# either bound is hit — small enough that the UI still reads as live typing,
# large enough that the post rate stays in single digits per second.
_TOKEN_FLUSH_CHARS = 160
_TOKEN_FLUSH_SECONDS = 0.2


class SessionPublishCallback(BaseCallbackHandler):
    """Posts streaming events to the backend SSE broker when this subprocess
    is a session continuation (AGENT_SESSION_ID env var is set).

    Called from a background thread by LangChain, so HTTP POSTs are synchronous
    (requests library). The local dashboard server is expected to be running.

    Tokens are batched (see ``_TOKEN_FLUSH_CHARS`` / ``_TOKEN_FLUSH_SECONDS``);
    every non-token event flushes whatever is buffered first, so the browser
    receives text and tool events in the order they actually happened. The final
    flush rides on the terminal ``done`` event the caller posts.
    """

    raise_error: bool = False  # never crash the agent run because of publish failures

    def __init__(self, session_id: str, run_id: str, agent_id: str, port: int = 8000):
        self.session_id = session_id
        self.run_id = run_id
        self.agent_id = agent_id
        self._url = f"http://localhost:{port}/api/sessions/{session_id}/events"
        self._step = 0
        # Token batching state. LangChain dispatches callbacks from the agent's
        # execution thread, but executor- and model-level dispatch can overlap,
        # so the buffer is guarded.
        self._buf: list[str] = []
        self._buf_len = 0
        self._last_flush = 0.0
        self._buf_lock = threading.Lock()
        # on_llm_end fires once per dispatch level for the same response; keep
        # the last reasoning text so thinking is published once per LLM call.
        self._last_reasoning = ""

    # ── transport ────────────────────────────────────────────────────────────

    def _send(self, event: dict) -> None:
        """Raw POST of one event. Never flushes — used by the flush path itself."""
        try:
            import requests as _req
            _req.post(self._url, json=event, timeout=2)
        except Exception:
            pass

    def _post(self, event: dict) -> None:
        """Publish one non-token event, after any buffered tokens.

        Public by convention: the run entrypoints (``runtime/agent_run.py``,
        ``flow/task_driver.py``) call it directly to emit their ``meta`` and
        ``done`` markers, and rely on it to drain the token buffer first.
        """
        self.flush()
        self._send(event)

    def flush(self) -> None:
        """Ship whatever tokens are buffered as a single ``token`` event."""
        with self._buf_lock:
            if not self._buf:
                return
            text = "".join(self._buf)
            self._buf.clear()
            self._buf_len = 0
            self._last_flush = time.monotonic()
        self._send({"type": "token", "token": text, "run_id": self.run_id})

    # ── callbacks ────────────────────────────────────────────────────────────

    def on_llm_new_token(self, token, **kwargs):
        text = token_text(token, kwargs.get("chunk"))
        if not text:
            return
        with self._buf_lock:
            self._buf.append(text)
            self._buf_len += len(text)
            now = time.monotonic()
            if self._last_flush == 0.0:
                self._last_flush = now
            due = (
                self._buf_len >= _TOKEN_FLUSH_CHARS
                or (now - self._last_flush) >= _TOKEN_FLUSH_SECONDS
            )
        if due:
            self.flush()

    def on_llm_end(self, response, **_):
        """Flush trailing tokens and publish this call's native reasoning.

        Reasoning is not part of the token stream (providers deliver it out of
        band), so without this the thinking a run produced would only ever reach
        the run log — never the live view.
        """
        self.flush()
        try:
            from reasoning.native_reasoning import extract_reasoning_from_llm_result
            reasoning = extract_reasoning_from_llm_result(response)
        except Exception:
            reasoning = ""
        if reasoning and reasoning != self._last_reasoning:
            self._last_reasoning = reasoning
            self._send({"type": "thinking", "message": reasoning, "run_id": self.run_id})

    def publish_done(self, result, invocation, *, stopped: bool = False) -> None:
        """Publish the terminal ``done`` event for a finished run.

        Same payload runtime/agent_run.py assembles by hand for subprocess runs,
        so a live view closes its block identically whichever runner produced it.
        Flushes any trailing tokens first (via ``_post``).
        """
        stats = getattr(invocation, "stats", None)
        ok = bool(getattr(result, "ok", False)) and not stopped
        if stopped:
            response = "[stopped by user]"
        elif ok:
            response = str(getattr(result, "agent_output", "") or "")
        else:
            response = f"Error: {getattr(result, 'error', None) or 'unknown'}"
        self._post({
            "type": "done",
            "ok": ok,
            "response": response,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "usage": {
                "inbound_tokens": getattr(stats, "prompt_tokens", 0),
                "outbound_tokens": getattr(stats, "completion_tokens", 0),
                "total_tokens": getattr(stats, "total_tokens", 0),
                "cached_tokens": getattr(stats, "cached_prompt_tokens", 0),
            },
            "tool_calls": getattr(stats, "tool_calls", 0),
            "duration_ms": getattr(invocation, "duration_ms", 0),
            "continuation": True,
        })

    def on_tool_start(self, serialized, input_str, **_):
        self._step += 1
        name = (serialized or {}).get("name", "tool") if isinstance(serialized, dict) else "tool"
        inp = str(input_str or "")
        self._post({
            "type": "tool_start",
            "step": self._step,
            "tool": name,
            "input": inp[:240],
            "run_id": self.run_id,
        })

    def on_tool_end(self, output, **_):
        self._post({
            "type": "tool_end",
            "output": str(output or "")[:240],
            "run_id": self.run_id,
        })

    def on_tool_error(self, error, **_):
        self._post({
            "type": "tool_error",
            "error": str(error),
            "run_id": self.run_id,
        })

    def on_llm_error(self, error, **_):
        self._post({
            "type": "error",
            "source": "llm",
            "error": str(error),
            "run_id": self.run_id,
        })
