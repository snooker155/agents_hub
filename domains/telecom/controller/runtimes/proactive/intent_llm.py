from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

"""
В каркасе LLM необязателен. Этот файл — интерфейс-заглушка.
Когда подключишь провайдера (OpenAI/локальный), реализуешь метод `parse`.
"""


@dataclass
class IntentLLMConfig:
    enabled: bool = False


class IntentParser:
    def __init__(self, cfg: IntentLLMConfig, client: Optional[Any] = None) -> None:
        self.cfg = cfg
        self.client = client

    def parse(self, text: str) -> Dict[str, Any]:
        # Заглушка: возвращаем пусто.
        # В будущем: few-shot -> JSON {mode: {set: ...}, goals: [...], constraints: ...}
        return {}
