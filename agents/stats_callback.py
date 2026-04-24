"""Shared LangChain callback for collecting token/tool stats from agent runs."""
from __future__ import annotations

import math
from typing import Optional

from langchain_core.callbacks import BaseCallbackHandler


def _to_json_safe(value, *, _depth: int = 0):
    """Best-effort conversion of callback payloads to JSON-safe structures."""
    if _depth >= 5:
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _to_json_safe(v, _depth=_depth + 1) for k, v in list(value.items())[:200]}
    if isinstance(value, (list, tuple)):
        return [_to_json_safe(v, _depth=_depth + 1) for v in list(value)[:200]]
    for meth in ("model_dump", "dict"):
        fn = getattr(value, meth, None)
        if callable(fn):
            try:
                return _to_json_safe(fn(), _depth=_depth + 1)
            except Exception:
                pass
    if hasattr(value, "__dict__"):
        try:
            return _to_json_safe(vars(value), _depth=_depth + 1)
        except Exception:
            pass
    return str(value)


class RunStatsCallback(BaseCallbackHandler):
    """Collects LLM/tool stats for agent runs.

    Mirrors ChatStreamCallback (minus SSE streaming) so the same
    ``process`` dict can be stored in the run record.
    """

    raise_error: bool = True

    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.tool_calls = 0
        self._last_prompt_text = ""
        self._output_text_parts: list = []
        self.tool_history: list = []
        self.thinking_history: list = []
        self.llm_invoke_responses: list = []
        self._pending_tool: Optional[dict] = None

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, int(math.ceil(len(text) / 4)))

    def on_llm_start(self, serialized, prompts, **_):
        model_name = (serialized or {}).get("name") if isinstance(serialized, dict) else None
        try:
            self._last_prompt_text = (
                "\n".join(str(p) for p in prompts) if isinstance(prompts, list) else str(prompts or "")
            )
        except Exception:
            self._last_prompt_text = ""
        line = f"[llm_start] model={model_name or 'unknown'}"
        print(line)
        self.thinking_history.append(line)

    def on_llm_new_token(self, token, **_):
        if token:
            self._output_text_parts.append(str(token))

    def on_llm_end(self, response, **_):
        try:
            self.llm_invoke_responses.append({
                "response_type": response.__class__.__name__,
                "llm_output": _to_json_safe(getattr(response, "llm_output", None)),
                "generations": _to_json_safe(getattr(response, "generations", None)),
            })
        except Exception:
            pass

        usage: dict = {}
        try:
            usage = (getattr(response, "llm_output", None) or {}).get("token_usage", {}) or {}
        except Exception:
            pass
        if not usage:
            try:
                for grp in (getattr(response, "generations", []) or []):
                    for g in grp:
                        md = getattr(getattr(g, "message", None), "response_metadata", None) or {}
                        tu = md.get("token_usage") or md.get("usage") or {}
                        if tu:
                            usage = tu
                            break
                    if usage:
                        break
            except Exception:
                pass
        if not usage:
            try:
                for grp in (getattr(response, "generations", []) or []):
                    for g in grp:
                        um = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                        if um:
                            usage = {
                                "prompt_tokens": um.get("input_tokens"),
                                "completion_tokens": um.get("output_tokens"),
                                "total_tokens": um.get("total_tokens"),
                            }
                            break
                    if usage:
                        break
            except Exception:
                pass

        p = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        c = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        t = int(usage.get("total_tokens") or (p + c))

        estimated = False
        if p == 0 and c == 0 and t == 0:
            p = self._estimate_tokens(self._last_prompt_text)
            c = self._estimate_tokens("".join(self._output_text_parts))
            t = p + c
            estimated = True

        self.prompt_tokens += p
        self.completion_tokens += c
        self.total_tokens += t
        print(
            f"[llm_usage] prompt_tokens={p} completion_tokens={c} total_tokens={t}"
            + (" estimated=true" if estimated else "")
        )

    def on_tool_start(self, serialized, input_str, **_):
        self.tool_calls += 1
        name = (serialized or {}).get("name") if isinstance(serialized, dict) else "tool"
        input_full = str(input_str)
        preview = input_full[:240] + "..." if len(input_full) > 240 else input_full
        print(f"[tool_start] tool={name} input={preview}")
        self._pending_tool = {"tool": name, "input": input_full}

    def on_tool_end(self, output, **_):
        output_full = str(output)
        preview = output_full[:240] + "..." if len(output_full) > 240 else output_full
        print(f"[tool_end] output={preview}")
        if self._pending_tool is not None:
            self.tool_history.append({**self._pending_tool, "output": output_full})
            self._pending_tool = None

    def on_llm_error(self, error, **_):
        print(f"[llm_error] {type(error).__name__}: {error}")

    def on_tool_error(self, error, **_):
        tool_name = (self._pending_tool or {}).get("tool", "unknown")
        print(f"[tool_error] tool={tool_name} {type(error).__name__}: {error}")
        if self._pending_tool is not None:
            self.tool_history.append({**self._pending_tool, "output": f"ERROR: {error}"})
            self._pending_tool = None

    def on_chain_error(self, error, **_):
        print(f"[chain_error] {type(error).__name__}: {error}")

    def build_process(self, duration_ms: int) -> dict:
        return {
            "llm_input_context": self._last_prompt_text,
            "tool_calls": self.tool_history,
            "thinking": self.thinking_history,
            "llm_invoke_responses": self.llm_invoke_responses,
            "token_usage": {
                "inbound_tokens": self.prompt_tokens,
                "outbound_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
            },
            "duration_ms": duration_ms,
        }
