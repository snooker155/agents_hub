from __future__ import annotations

from typing import Optional, List, Any, Dict
from datetime import datetime, timezone
from pathlib import Path
import json

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
    Falls back to the global settings when parameters are not supplied.
    """
    temp = temperature if temperature is not None else settings.temperature
    tok = max_tokens if max_tokens is not None else settings.max_tokens

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        url = base_url or settings.ollama_base_url
        mdl = model or settings.ollama_model or "llama3"
        return ChatOllama(model=mdl, base_url=url, temperature=temp)

    if provider == "lmstudio":
        url = (base_url or settings.lmstudio_base_url).rstrip("/")
        mdl = model or settings.lmstudio_model or "local-model"
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
        key = api_key or settings.anthropic_api_key
        mdl = model or "claude-opus-4-6"
        return ChatAnthropic(model=mdl, api_key=key, temperature=temp, max_tokens=tok)

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        key = api_key or settings.google_api_key
        mdl = model or "gemini-2.0-flash"
        return ChatGoogleGenerativeAI(model=mdl, google_api_key=key, temperature=temp)

    # Default: OpenAI (or inherit global settings)
    return ChatOpenAI(
        model=model or settings.model,
        temperature=temp,
        max_tokens=tok,
        api_key=api_key or settings.openai_api_key,
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
        self.steps_data: List[Dict[str, Any]] = []

    def _save_progress(self):
        if not self.workspace:
            return
        try:
            prog_file = self.workspace / ".progress.json"
            with prog_file.open("w", encoding="utf-8") as f:
                json.dump({"steps": self.steps_data}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def on_llm_start(self, serialized, prompts, **kwargs):
        pass

    def on_llm_end(self, response, **kwargs):
        pass

    def on_tool_start(self, serialized, input_str, **kwargs):
        try:
            self._step += 1
            name = serialized.get("name") if isinstance(serialized, dict) else "tool"
            
            self.steps_data.append({
                "step": self._step,
                "tool": name,
                "input": str(input_str),
                "started_at": datetime.now(timezone.utc).isoformat()
            })
            self._save_progress()

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
            if self.steps_data:
                self.steps_data[-1]["output"] = str(output)
                self.steps_data[-1]["finished_at"] = datetime.now(timezone.utc).isoformat()
                self._save_progress()

            text = str(output)
            if len(text) > 300:
                text = text[:300] + "..."
            print(f"[{self.model_name}] Result: {text}")
        except Exception:
            pass
