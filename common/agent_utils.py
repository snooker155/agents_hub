from __future__ import annotations

from typing import Optional, List, Any, Dict
from datetime import datetime, timezone
from pathlib import Path
import json

import os

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.callbacks import BaseCallbackHandler

from common.config import settings


def build_chat_model(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    streaming: bool = False,
):
    """Build a LangChain chat model for the given provider.

    provider: 'openai' | 'anthropic' | 'google' | 'ollama' | 'lmstudio' | None/'inherit'
    Falls back to the global DEFAULT_PROVIDER env setting when not supplied.
    """
    # Resolve provider: None / 'inherit' → use global DEFAULT_PROVIDER.
    # Read .env directly so already-running subprocesses pick up UI changes
    # (os.environ is frozen at subprocess-start time; .env is always current).
    if not provider or provider == "inherit":
        _dot_env = Path(__file__).resolve().parents[1] / ".env"
        _file_provider: str = ""
        if _dot_env.exists():
            try:
                for _ln in _dot_env.read_text(encoding="utf-8").splitlines():
                    _ln = _ln.strip()
                    if _ln.startswith("DEFAULT_PROVIDER") and "=" in _ln:
                        _file_provider = _ln.partition("=")[2].strip().strip('"\'')
                        break
            except Exception:
                pass
        provider = os.environ.get("DEFAULT_PROVIDER") or _file_provider or settings.default_provider

    temp = temperature if temperature is not None else settings.temperature
    tok = max_tokens if max_tokens is not None else settings.max_tokens

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        url = base_url or os.environ.get("OLLAMA_BASE_URL") or settings.ollama_base_url
        mdl = model or os.environ.get("OLLAMA_MODEL") or settings.ollama_model
        if not mdl:
            raise ValueError("Ollama model is not configured. Set OLLAMA_MODEL in Settings.")
        return ChatOllama(model=mdl, base_url=url, temperature=temp)

    if provider == "lmstudio":
        url = (base_url or os.environ.get("LMSTUDIO_BASE_URL") or settings.lmstudio_base_url).rstrip("/")
        mdl = model or os.environ.get("LMSTUDIO_MODEL") or settings.lmstudio_model
        if not mdl:
            raise ValueError("LM Studio model is not configured. Set LMSTUDIO_MODEL in Settings.")
        return ChatOpenAI(
            model=mdl,
            base_url=f"{url}/v1",
            api_key="lm-studio",  # LM Studio ignores the key value
            temperature=temp,
            max_tokens=tok,
            streaming=streaming,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        key = api_key or os.environ.get("ANTHROPIC_API_KEY") or settings.anthropic_api_key
        mdl = model or os.environ.get("ANTHROPIC_MODEL") or "claude-opus-4-6"
        return ChatAnthropic(model=mdl, api_key=key, temperature=temp, max_tokens=tok)

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        key = api_key or os.environ.get("GOOGLE_API_KEY") or settings.google_api_key
        mdl = model or os.environ.get("GOOGLE_MODEL") or "gemini-2.0-flash"
        return ChatGoogleGenerativeAI(model=mdl, google_api_key=key, temperature=temp)

    # Default: OpenAI (or inherit global settings)
    return ChatOpenAI(
        model=model or os.environ.get("OPENAI_MODEL") or settings.model,
        temperature=temp,
        max_tokens=tok,
        api_key=api_key or os.environ.get("OPENAI_API_KEY") or settings.openai_api_key,
        base_url=base_url or os.environ.get("OPENAI_BASE_URL") or None,
        streaming=streaming,
    )

class SharedProgressCallback(BaseCallbackHandler):
    """
    Console logger and file-based progress tracker.
    """

    def __init__(self, workspace: Optional[Path] = None, model_name: str = "unknown") -> None:
        self._step = 0
        self.workspace = workspace
        self.model_name = model_name

    def on_llm_start(self, serialized, prompts, **kwargs):
        model_name = serialized.get("name") if isinstance(serialized, dict) else "unknown"
        print(f"[llm_start] model={model_name or 'unknown'}", flush=True)

    def on_llm_end(self, response, **kwargs):
        """Log token usage so the dashboard can display stats for worker runs."""
        usage: Dict[str, Any] = {}
        try:
            usage = (getattr(response, "llm_output", None) or {}).get("token_usage", {}) or {}
        except Exception:
            pass
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
                pass
        if not usage:
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
                            }
                            break
                    if usage:
                        break
            except Exception:
                pass
        p = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        c = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        t = int(usage.get("total_tokens") or (p + c))
        print(f"[llm_usage] prompt_tokens={p} completion_tokens={c} total_tokens={t}", flush=True)

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


class ToolRepetitionError(RuntimeError):
    """Raised when the same tool is called too many times consecutively."""


class ToolRepetitionGuard(BaseCallbackHandler):
    """Stop agent execution when the same tool is called N times in a row.

    Accumulates every (tool, output) pair during the run so that the full
    work done by the agent can be returned when stopping early.

    ``raise_error = True`` is required so LangChain does not swallow the
    exception in its callback dispatch loop.
    """

    def __init__(self, max_repeats: int = 3) -> None:
        super().__init__()
        self.raise_error = True  # tell LangChain to propagate our exceptions
        self.max_repeats = max_repeats
        self._last_tool: Optional[str] = None
        self._consecutive: int = 0
        self._should_stop: bool = False
        self._pending_tool: str = ""
        self.last_llm_text: str = ""  # last coherent text the LLM produced

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        """Capture the last text the LLM generated (its reasoning / partial answer)."""
        try:
            for grp in (getattr(response, "generations", []) or []):
                for g in grp:
                    msg = getattr(g, "message", None)
                    content = getattr(msg, "content", None) if msg else None
                    if isinstance(content, str) and content.strip():
                        self.last_llm_text = content.strip()
                    elif isinstance(content, list):
                        # Anthropic-style structured content blocks
                        texts = [
                            c.get("text", "")
                            for c in content
                            if isinstance(c, dict) and c.get("type") == "text"
                        ]
                        text = " ".join(t for t in texts if t).strip()
                        if text:
                            self.last_llm_text = text
        except Exception:
            pass

    def on_tool_start(self, serialized: Any, input_str: Any, **kwargs: Any) -> None:
        name: str = (serialized.get("name", "") if isinstance(serialized, dict) else "") or ""
        self._pending_tool = name
        if name and name == self._last_tool:
            self._consecutive += 1
        else:
            self._last_tool = name
            self._consecutive = 1
        self._should_stop = self._consecutive > self.max_repeats

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        if self._should_stop:
            raise ToolRepetitionError(
                f"Tool '{self._last_tool}' was called {self._consecutive} times in a row "
                f"(limit={self.max_repeats}). Stopping agent to prevent infinite loop."
            )


class RunStopCallback(BaseCallbackHandler):
    """Interrupt agent execution when the associated run is stopped via the run manager."""

    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id
        self.cancelled = False

    def _check_stop(self) -> None:
        if self.cancelled:
            raise InterruptedError(f"Run {self.run_id} was stopped by user")
        try:
            from agents.run_manager import get_run_by_id
            run = get_run_by_id(self.run_id)
            if run and run.get("status") in ("stop", "stopped"):
                self.cancelled = True
                raise InterruptedError(f"Run {self.run_id} was stopped by user")
        except InterruptedError:
            raise
        except Exception:
            pass

    def on_llm_new_token(self, token, **kwargs) -> None:  # noqa: ARG002
        self._check_stop()

    def on_llm_end(self, response, **kwargs) -> None:
        self._check_stop()

    def on_tool_end(self, output, **kwargs) -> None:
        self._check_stop()
