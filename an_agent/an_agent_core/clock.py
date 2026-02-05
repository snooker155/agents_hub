from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


def now_ms() -> int:
    return int(time.time() * 1000)


class Stopwatch:
    """Простой таймер для профилирования."""

    def __init__(self) -> None:
        self._start = time.perf_counter()

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._start) * 1000.0


@dataclass
class Deadline:
    """
    Deadline-aware helper.
    Используется в Coordinator/Reactive: можно проверять late и деградировать.
    """

    budget_ms: int
    start_perf: float = time.perf_counter()

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.start_perf) * 1000.0

    def remaining_ms(self) -> float:
        return max(0.0, float(self.budget_ms) - self.elapsed_ms())

    def is_late(self) -> bool:
        return self.elapsed_ms() > float(self.budget_ms)

    def mark(self) -> float:
        """Возвращает elapsed_ms, удобно логировать контрольные точки."""
        return self.elapsed_ms()


def sleep_until_next_tick(tick_hz: float, tick_start_perf: float) -> None:
    """Утилита для tick-loop: спать до следующего тика."""
    if tick_hz <= 0:
        return
    period = 1.0 / tick_hz
    elapsed = time.perf_counter() - tick_start_perf
    remaining = period - elapsed
    if remaining > 0:
        time.sleep(remaining)
