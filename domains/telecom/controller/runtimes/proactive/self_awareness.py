from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger

from domains.telecom.controller.an_agent_core.types import Event, EventType, ServiceMode


@dataclass
class SelfAwarenessConfig:
    # пороги для переключения режимов (пример)
    embb_bler_max: float = 0.10
    urllc_bler_max: float = 0.001
    deadline_exceed_limit: int = 5


class SelfAwareness:
    """
    Self-awareness:
    - отслеживает качество, дедлайны, SLA
    - может переключать режим (eMBB/URLLC) как meta-goal
    - может интерпретировать human instruction (позже через intent_llm)
    """

    def __init__(self, cfg: SelfAwarenessConfig, intent_parser: Optional[Any] = None) -> None:
        self.cfg = cfg
        self.intent_parser = intent_parser

        self._mode: ServiceMode = ServiceMode.EMBB
        self._pending_updates: Dict[str, Any] = {}
        self._deadline_violations = 0
        self._last_bler: Optional[float] = None

    def on_observation(self, event: Event) -> None:
        obs = event.payload.get("observation")
        if obs is None:
            return
        bler = getattr(obs, "bler", None)
        self._last_bler = bler

        # простая эвристика: если BLER слишком высокий для eMBB — предложить более консервативную стратегию
        if self._mode == ServiceMode.EMBB and bler is not None and bler > self.cfg.embb_bler_max:
            self._pending_updates["threshold_violation"] = {
                "kind": "bler_high",
                "bler": float(bler),
                "suggest_mode": "urllc",
            }

    def on_trigger(self, event: Event) -> None:
        if event.type == EventType.HUMAN_INSTRUCTION:
            text = event.payload.get("text", "")
            updates = self._parse_human_instruction(text)
            if updates:
                self._pending_updates.update(updates)
            return

        if event.type == EventType.THRESHOLD_VIOLATION:
            self._pending_updates["threshold_violation"] = event.payload

    def on_deadline(self, event: Event) -> None:
        self._deadline_violations += 1
        if self._deadline_violations >= self.cfg.deadline_exceed_limit:
            # если часто нарушаем дедлайны, ограничиваем “сложность” решений
            self._pending_updates["performance"] = {
                "degrade": True,
                "reason": "too_many_deadline_violations",
                "count": self._deadline_violations,
            }

    def on_error(self, event: Event) -> None:
        self._pending_updates["error_seen"] = event.payload

    def periodic_check(self, now_ms: int) -> Dict[str, Any]:
        # пример: если долго в URLLC, а BLER стабилен и низкий — можно вернуть eMBB
        if self._mode == ServiceMode.URLLC and self._last_bler is not None:
            if self._last_bler < (self.cfg.embb_bler_max * 0.5):
                return {"mode": {"set": "embb", "reason": "bler_stable_low"}}
        return {}

    def has_updates(self) -> bool:
        return bool(self._pending_updates)

    def consume_updates(self) -> Dict[str, Any]:
        out = dict(self._pending_updates)
        self._pending_updates.clear()
        return out

    def _parse_human_instruction(self, text: str) -> Dict[str, Any]:
        t = text.strip().lower()
        if not t:
            return {}

        # без LLM: минимальный парсер
        if "urllc" in t or "ultra" in t:
            return {"mode": {"set": "urllc", "reason": "human_instruction"}}
        if "embb" in t or "throughput" in t or "скорость" in t:
            return {"mode": {"set": "embb", "reason": "human_instruction"}}

        # если подключим intent_parser (LLM/шаблоны) — он вернёт обновления
        if self.intent_parser is not None:
            try:
                return self.intent_parser.parse(text)
            except Exception as e:  # noqa: BLE001
                logger.warning("intent_parser failed: {}", e)
        return {}
