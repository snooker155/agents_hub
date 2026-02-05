from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

from loguru import logger

from .clock import Deadline, Stopwatch, now_ms, sleep_until_next_tick
from .events import EventBus
from .types import Decision, Event, EventType, Observation


class ReactiveRuntime(Protocol):
    def step(self, obs: Observation, deadline: Deadline) -> Decision: ...


class ProactiveRuntime(Protocol):
    def on_event(self, event: Event) -> None: ...
    def tick(self) -> None: ...


class TelemetrySource(Protocol):
    def poll(self) -> Optional[Observation]: ...


class SouthboundExecutor(Protocol):
    def apply(self, decision: Decision) -> None: ...


@dataclass
class CoordinatorConfig:
    tick_hz: float = 100.0
    reactive_deadline_ms: int = 10
    proactive_tick_hz: float = 2.0
    enable_proactive: bool = True


class Coordinator:
    """
    Центральный оркестратор:
    - читает телеметрию,
    - запускает reactive step с дедлайном,
    - публикует события,
    - даёт proactive (event-driven + periodic tick) работать вне hot path,
    - отправляет действие на southbound.
    """

    def __init__(
        self,
        cfg: CoordinatorConfig,
        event_bus: EventBus,
        telemetry: TelemetrySource,
        reactive: ReactiveRuntime,
        southbound: SouthboundExecutor,
        proactive: Optional[ProactiveRuntime] = None,
    ) -> None:
        self.cfg = cfg
        self.event_bus = event_bus
        self.telemetry = telemetry
        self.reactive = reactive
        self.proactive = proactive
        self.southbound = southbound

        self._running = False
        self._tick_counter = 0
        self._proactive_divider = max(1, int(cfg.tick_hz / max(cfg.proactive_tick_hz, 1e-6)))

        if self.proactive is not None:
            # Проксируем события proactive runtime-у
            self.event_bus.subscribe(EventType.OBSERVATION, self.proactive.on_event)
            self.event_bus.subscribe(EventType.THRESHOLD_VIOLATION, self.proactive.on_event)
            self.event_bus.subscribe(EventType.DEADLINE_EXCEEDED, self.proactive.on_event)
            self.event_bus.subscribe(EventType.HUMAN_INSTRUCTION, self.proactive.on_event)
            self.event_bus.subscribe(EventType.MODE_CHANGED, self.proactive.on_event)
            self.event_bus.subscribe(EventType.ERROR, self.proactive.on_event)

    def run(self, max_ticks: Optional[int] = None) -> None:
        self._running = True
        logger.info(
            "Coordinator starting: tick_hz={}, deadline_ms={}, proactive={}",
            self.cfg.tick_hz,
            self.cfg.reactive_deadline_ms,
            bool(self.proactive and self.cfg.enable_proactive),
        )

        while self._running:
            tick_start = Stopwatch()
            tick_start_perf = tick_start._start  # internal perf_counter from Stopwatch

            obs = self.telemetry.poll()
            if obs is None:
                # Даже без наблюдения можно дать proactive тикнуть + drain событий
                self._maybe_tick_proactive()
                self.event_bus.drain()
                sleep_until_next_tick(self.cfg.tick_hz, tick_start_perf)
                self._maybe_stop(max_ticks)
                continue

            # Публикуем событие наблюдения
            self.event_bus.publish(
                Event(type=EventType.OBSERVATION, ts_ms=obs.ts_ms, payload={"observation": obs})
            )

            # Reactive step (hot path)
            deadline = Deadline(budget_ms=min(obs.deadline_ms, self.cfg.reactive_deadline_ms))
            decision = self._safe_reactive_step(obs, deadline)
            decision.latency_ms = deadline.elapsed_ms()

            # Если дедлайн превышен — событие (для proactive + мониторинга)
            if deadline.is_late():
                self.event_bus.publish(
                    Event(
                        type=EventType.DEADLINE_EXCEEDED,
                        ts_ms=now_ms(),
                        payload={
                            "elapsed_ms": decision.latency_ms,
                            "budget_ms": deadline.budget_ms,
                            "decision": decision,
                        },
                    )
                )

            # Southbound apply (может быть sync; в реальности лучше неблокирующий драйвер)
            self._safe_apply(decision)

            # Доставляем события подписчикам (proactive слушает тут же, но вне hot decision loop)
            self.event_bus.drain()

            # Периодический proactive tick
            self._maybe_tick_proactive()

            # tick sleep
            sleep_until_next_tick(self.cfg.tick_hz, tick_start_perf)
            self._maybe_stop(max_ticks)

        logger.info("Coordinator stopped.")

    def stop(self) -> None:
        self._running = False

    def _maybe_stop(self, max_ticks: Optional[int]) -> None:
        self._tick_counter += 1
        if max_ticks is not None and self._tick_counter >= max_ticks:
            self.stop()

    def _maybe_tick_proactive(self) -> None:
        if not (self.proactive and self.cfg.enable_proactive):
            return
        if self._tick_counter % self._proactive_divider == 0:
            try:
                self.proactive.tick()
            except Exception as e:  # noqa: BLE001
                logger.exception("Proactive tick error: {}", e)
                self.event_bus.publish(
                    Event(type=EventType.ERROR, ts_ms=now_ms(), payload={"error": repr(e)})
                )

    def _safe_reactive_step(self, obs: Observation, deadline: Deadline) -> Decision:
        try:
            return self.reactive.step(obs, deadline)
        except Exception as e:  # noqa: BLE001
            logger.exception("Reactive step error: {}", e)
            self.event_bus.publish(
                Event(type=EventType.ERROR, ts_ms=now_ms(), payload={"error": repr(e)})
            )
            # безопасный no-op
            from .types import Action, Decision  # локально, чтобы избежать циклов

            return Decision(
                ts_ms=now_ms(),
                action=Action(type="NO_OP", params={"reason": "reactive_error"}),
                constraints_passed=False,
                rationale=f"Reactive error: {e!r}",
            )

    def _safe_apply(self, decision: Decision) -> None:
        try:
            self.southbound.apply(decision)
        except Exception as e:  # noqa: BLE001
            logger.exception("Southbound apply error: {}", e)
            self.event_bus.publish(
                Event(type=EventType.ERROR, ts_ms=now_ms(), payload={"error": repr(e)})
            )
