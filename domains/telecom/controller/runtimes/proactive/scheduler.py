from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SchedulerConfig:
    periodic_check_sec: int = 5


class Scheduler:
    """Минимальный планировщик периодических задач для proactive.tick()."""

    def __init__(self, cfg: SchedulerConfig) -> None:
        self.cfg = cfg
        self._last_run_ms: int = 0

    def should_run(self, now_ms: int) -> bool:
        period_ms = max(1, int(self.cfg.periodic_check_sec * 1000))
        return (now_ms - self._last_run_ms) >= period_ms

    def mark_run(self, now_ms: int) -> None:
        self._last_run_ms = now_ms
