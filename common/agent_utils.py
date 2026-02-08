from __future__ import annotations

from typing import Optional, List, Any, Dict
from datetime import datetime, timezone
from pathlib import Path
import json

from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.callbacks import BaseCallbackHandler

from common.config import settings

def build_chat_model(
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    api_key: Optional[str] = None
) -> ChatOpenAI:
    """Standardized ChatOpenAI builder using unified settings."""
    return ChatOpenAI(
        model=model or settings.model,
        temperature=temperature if temperature is not None else settings.temperature,
        max_tokens=max_tokens if max_tokens is not None else settings.max_tokens,
        api_key=api_key or settings.openai_api_key,
    )

class SharedProgressCallback(BaseCallbackHandler):
    """
    Console logger and file-based progress tracker.
    Adapted from swe_agent._PrintProgressCallback.
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
