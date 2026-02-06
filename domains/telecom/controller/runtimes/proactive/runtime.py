from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from domains.telecom.controller.an_agent_core.clock import now_ms
from domains.telecom.controller.an_agent_core.types import Event, EventType

from .self_awareness import SelfAwareness
from .choice_making import ChoiceMaker
from .scheduler import Scheduler


@dataclass
class ProactiveRuntimeConfig:
    enable_llm: bool = False
    periodic_check_sec: int = 5


class ProactiveRuntime:
    """
    Proactive Behavior Runtime (slow-loop / event-driven):
    - слушает события (thresholds, human instruction)
    - обновляет self-awareness (режим, meta-goals)
    - choice-making (приоритеты/настройка reward)
    - периодически выполняет self-check
    """

    def __init__(
        self,
        cfg: ProactiveRuntimeConfig,
        self_awareness: SelfAwareness,
        choice_maker: ChoiceMaker,
        scheduler: Scheduler,
    ) -> None:
        self.cfg = cfg
        self.self_awareness = self_awareness
        self.choice_maker = choice_maker
        self.scheduler = scheduler

    def on_event(self, event: Event) -> None:
        try:
            if event.type == EventType.OBSERVATION:
                self.self_awareness.on_observation(event)
            elif event.type in (EventType.THRESHOLD_VIOLATION, EventType.HUMAN_INSTRUCTION):
                self.self_awareness.on_trigger(event)
            elif event.type == EventType.DEADLINE_EXCEEDED:
                self.self_awareness.on_deadline(event)
            elif event.type == EventType.ERROR:
                self.self_awareness.on_error(event)

            # После обработки — может обновиться режим/intent/meta-goal
            if self.self_awareness.has_updates():
                updates = self.self_awareness.consume_updates()
                self.choice_maker.apply_updates(updates)
        except Exception as e:  # noqa: BLE001
            logger.exception("Proactive on_event error: {}", e)

    def tick(self) -> None:
        # периодические задания
        now = now_ms()
        if self.scheduler.should_run(now):
            self.scheduler.mark_run(now)
            updates = self.self_awareness.periodic_check(now)
            if updates:
                self.choice_maker.apply_updates(updates)
