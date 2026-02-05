from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from an_agent_core.types import Observation, WorldState


@dataclass
class ConstraintsConfig:
    # общие
    mcs_min: int = 0
    mcs_max: int = 27
    mcs_max_step: int = 2
    mimo_allowed: tuple[int, ...] = (1, 2, 4)

    # режимные пороги
    embb_bler_max: float = 0.10
    urllc_bler_max: float = 0.001


class ConstraintsProvider:
    """
    Поставщик constraints для reactive.
    Proactive может менять режим и включать degraded-mode.
    """

    def __init__(self, cfg: ConstraintsConfig) -> None:
        self.cfg = cfg
        self._mode: str = "embb"
        self._degraded: bool = False
        self._degraded_reason: str = ""

    def set_mode(self, mode: str) -> None:
        self._mode = mode.lower()

    def set_degraded(self, degraded: bool, reason: str = "") -> None:
        self._degraded = degraded
        self._degraded_reason = reason

    def get_constraints(self, obs: Observation, ws: WorldState) -> Dict[str, Any]:
        c: Dict[str, Any] = {
            "mcs_min": self.cfg.mcs_min,
            "mcs_max": self.cfg.mcs_max,
            "mcs_max_step": self.cfg.mcs_max_step if not self._degraded else 1,  # в деградации делаем шаг меньше
            "mimo_allowed": list(self.cfg.mimo_allowed),
            "embb_bler_max": self.cfg.embb_bler_max,
            "urllc_bler_max": self.cfg.urllc_bler_max,
            "current_mcs": obs.mcs_index,
            "current_rank": obs.mimo_rank,
            "degraded": self._degraded,
            "degraded_reason": self._degraded_reason,
        }

        # режим может идти из Observation (источник истины), но proactive может подсказать глобально
        # В skeleton: если observation уже несёт mode — используем его
        # Иначе можно было бы применять self._mode.
        return c
