"""
Control & logging callbacks for agent runs.

Single-purpose callbacks that are not stats collectors:

- ``SharedProgressCallback`` — console/file progress logger for worker runs.
- ``RunStopCallback`` — interrupts a run when it is stopped via the run manager.
- ``NodeFileCallback`` — writes a flow node's LLM/tool events to its own file.

Run guards (ToolRepetitionGuard, AskUserGuard, ContextWindowGuard and their
signals) live in ``agents.callbacks.guards``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from langchain_core.callbacks import BaseCallbackHandler

from agents.callbacks.run_statistics import extract_token_usage, normalize_usage


def _clip(text: str, limit: int) -> str:
    """Single-string preview for log lines: cut at *limit* with a size note."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… (+{len(text) - limit} more chars)"


class SharedProgressCallback(BaseCallbackHandler):
    """Console logger and file-based progress tracker."""

    def __init__(self, workspace: Optional[Path] = None, model_name: str = "unknown") -> None:
        self._step = 0
        self.workspace = workspace
        self.model_name = model_name
        # Executor- and model-level callback dispatch both fire on_llm_end for
        # the same response; remember what was already written so reasoning and
        # answer text appear once per LLM call.
        self._last_reasoning = ""
        self._last_assistant = ""

    def on_llm_start(self, serialized, prompts, **kwargs):
        model_name = serialized.get("name") if isinstance(serialized, dict) else "unknown"
        print(f"[llm_start] model={model_name or 'unknown'}", flush=True)

    def on_llm_end(self, response, **kwargs):
        """Log token usage so the dashboard can display stats for worker runs."""
        p, c, t = normalize_usage(extract_token_usage(response))
        print(f"[llm_usage] prompt_tokens={p} completion_tokens={c} total_tokens={t}", flush=True)
        self._log_model_output(response)

    def _log_model_output(self, response) -> None:
        """Write the model's reasoning and visible text to the run log.

        This is what makes a dead run diagnosable after the fact: the log shows
        what the model was thinking before each action, what it said when it
        produced no tool call, and whether the response was cut off by the
        max_tokens completion cap (a truncated response looks like a silent,
        empty step without this marker).

        The reasoning line uses the same ``[reasoning] step=N content=<json>``
        format ChatStreamCallback writes for chat messages, so the message
        insights endpoint and the frontend parse worker-run thinking with the
        exact same code path as chat thinking.
        """
        try:
            from reasoning.native_reasoning import (
                extract_reasoning_from_llm_result,
                strip_think_tags,
            )
            reasoning = extract_reasoning_from_llm_result(response)
            if reasoning and reasoning != self._last_reasoning:
                self._last_reasoning = reasoning
                content = json.dumps(_clip(reasoning, 3000), ensure_ascii=False)
                print(f"[reasoning] step={self._step + 1} content={content}", flush=True)

            texts = []
            truncated = False
            for grp in (getattr(response, "generations", []) or []):
                for g in grp:
                    info = getattr(g, "generation_info", None) or {}
                    if str(info.get("finish_reason") or "") == "length":
                        truncated = True
                    msg = getattr(g, "message", None)
                    content = getattr(msg, "content", None) if msg is not None else getattr(g, "text", None)
                    if isinstance(content, list):
                        content = "".join(
                            b.get("text", "") if isinstance(b, dict) else str(b)
                            for b in content
                        )
                    if isinstance(content, str) and content.strip():
                        texts.append(strip_think_tags(content).strip())
            text = "\n".join(x for x in texts if x)
            if text and text != self._last_assistant:
                self._last_assistant = text
                content = json.dumps(_clip(text, 2000), ensure_ascii=False)
                print(f"[assistant] step={self._step + 1} content={content}", flush=True)
            if truncated:
                print(
                    "[truncated] finish_reason=length — the completion hit the max_tokens "
                    "cap (LLM_MAX_TOKENS); the response was cut off mid-reasoning/answer.",
                    flush=True,
                )
        except Exception:
            pass

    def on_tool_start(self, serialized, input_str, **kwargs):
        try:
            self._step += 1
            name = serialized.get("name") if isinstance(serialized, dict) else "tool"

            preview = str(input_str)
            # Simplify preview for file ops to avoid spam
            if name in {"write_file", "create_file", "apply_unified_diff"}:
                try:
                    if preview.startswith("{"):
                        data = json.loads(preview.replace("'", "\""))
                        if "path" in data:
                            preview = data["path"]
                except Exception:
                    pass

            if len(preview) > 200:
                preview = preview[:200] + "..."
            print(f"[{self.model_name}] Step {self._step}: {name} ← {preview}")
        except Exception:
            pass

    def on_tool_end(self, output, **kwargs):
        try:
            text = str(output)
            if len(text) > 300:
                text = text[:300] + "..."
            print(f"[{self.model_name}] Result: {text}")
        except Exception:
            pass


# How often the stop status may be re-read from the run store. Under streaming,
# on_llm_new_token fires hundreds of times per completion; polling the store on
# each one would put a database round-trip between every token. A quarter second
# keeps the abort effectively instant while capping the polling cost.
_STOP_POLL_INTERVAL = 0.25


class RunStopCallback(BaseCallbackHandler):
    """Interrupt agent execution when the associated run is stopped via the run manager.

    ``raise_error = True`` is required so LangChain does not swallow the
    InterruptedError in its callback dispatch loop.

    With streaming on, ``on_llm_new_token`` makes every token an abort point, so
    a stop lands mid-completion instead of after it. The store lookup behind that
    check is throttled (see ``_STOP_POLL_INTERVAL``); the ``cancelled`` flag
    itself is always honoured immediately, so an out-of-band stop (a caller
    setting the flag directly) still aborts on the very next callback.
    """

    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.raise_error = True  # tell LangChain to propagate our exceptions
        self.run_id = run_id
        self.cancelled = False
        self._last_poll = 0.0

    def _check_stop(self, *, throttle: bool = False) -> None:
        if self.cancelled:
            raise InterruptedError(f"Run {self.run_id} was stopped by user")
        if throttle:
            now = time.monotonic()
            if (now - self._last_poll) < _STOP_POLL_INTERVAL:
                return
            self._last_poll = now
        try:
            from managers.run_manager import get_run_by_id
            run = get_run_by_id(self.run_id)
            if run and run.get("status") in ("stop", "stopped"):
                self.cancelled = True
                raise InterruptedError(f"Run {self.run_id} was stopped by user")
        except InterruptedError:
            raise
        except Exception:
            pass

    # Start hooks abort *before* the next model/tool execution begins; the
    # token/end hooks catch a stop that arrived mid-step.
    def on_chat_model_start(self, serialized, messages, **kwargs) -> None:  # noqa: ARG002
        self._check_stop()

    def on_llm_start(self, serialized, prompts, **kwargs) -> None:  # noqa: ARG002
        self._check_stop()

    def on_tool_start(self, serialized, input_str, **kwargs) -> None:  # noqa: ARG002
        self._check_stop()

    def on_llm_new_token(self, token, **kwargs) -> None:  # noqa: ARG002
        # Fires per token when streaming is on — the throttled path.
        self._check_stop(throttle=True)

    def on_llm_end(self, response, **kwargs) -> None:
        self._check_stop()

    def on_tool_end(self, output, **kwargs) -> None:
        self._check_stop()


class NodeFileCallback(BaseCallbackHandler):
    """Writes LLM/tool events for a single flow node to its own log file."""

    raise_error = False

    def __init__(self, log_path: Path) -> None:
        self._f = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115

    def _w(self, line: str) -> None:
        try:
            self._f.write(line + "\n")
        except Exception:
            pass

    def on_llm_start(self, serialized, prompts, **_):
        model = (serialized or {}).get("name", "unknown") if isinstance(serialized, dict) else "unknown"
        self._w(f"[llm_start] model={model}")

    def on_llm_end(self, response, **_):
        self._w("[llm_end]")

    def on_llm_error(self, error, **_):
        self._w(f"[llm_error] {type(error).__name__}: {error}")

    def on_tool_start(self, serialized, input_str, **_):
        name = (serialized or {}).get("name", "tool") if isinstance(serialized, dict) else "tool"
        preview = str(input_str or "")[:500]
        self._w(f"[tool_start] tool={name} input={preview}")

    def on_tool_end(self, output, **_):
        self._w(f"[tool_end] output={str(output or '')[:500]}")

    def on_tool_error(self, error, **_):
        self._w(f"[tool_error] {type(error).__name__}: {error}")

    def on_chain_error(self, error, **_):
        self._w(f"[chain_error] {type(error).__name__}: {error}")

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass
