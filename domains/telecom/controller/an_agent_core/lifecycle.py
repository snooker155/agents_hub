from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import yaml
from loguru import logger

from .coordinator import Coordinator, CoordinatorConfig
from .events import EventBus


def _load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config at {path} must be a mapping")
    return data


@dataclass
class AppLifecycle:
    """
    Утилита для загрузки конфигов и запуска coordinator-а.
    Сборка зависимостей (DI) сделана максимально простым способом:
    в experiments/ мы будем создавать конкретные реализации telemetry/reactive/proactive/southbound.
    """

    config_dir: str = "config"
    default_config: str = "default.yaml"

    def load_config(self) -> Dict[str, Any]:
        path = os.path.join(self.config_dir, self.default_config)
        cfg = _load_yaml(path)
        return cfg

    def make_event_bus(self) -> EventBus:
        return EventBus()

    def make_coordinator_config(self, cfg: Dict[str, Any]) -> CoordinatorConfig:
        c = cfg.get("coordinator", {})
        return CoordinatorConfig(
            tick_hz=float(c.get("tick_hz", 100.0)),
            reactive_deadline_ms=int(c.get("reactive_deadline_ms", 10)),
            proactive_tick_hz=float(c.get("proactive_tick_hz", 2.0)),
            enable_proactive=bool(c.get("enable_proactive", True)),
        )

    def run(
        self,
        coordinator: Coordinator,
        max_ticks: Optional[int] = None,
    ) -> None:
        logger.info("AppLifecycle: starting app")
        coordinator.run(max_ticks=max_ticks)
        logger.info("AppLifecycle: app stopped")
