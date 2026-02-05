from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from an_agent_core.clock import Deadline
from an_agent_core.types import Action, Goal, WorldState


@dataclass
class PlannerConfig:
    # базовые шаги
    mcs_step: int = 1
    default_mimo_rank: int = 1


class Planner:
    """
    Constraint-based planner: превращает Goal -> Action.
    Здесь пока: простая логика регулирования MCS и MIMO rank.
    """

    def __init__(self, cfg: PlannerConfig) -> None:
        self.cfg = cfg

    def plan(self, goal: Goal, ws: WorldState, deadline: Deadline) -> Action:
        constraints = ws.constraints or {}

        # Идея: если прогноз BLER высокий — снизить MCS; если низкий — повысить MCS.
        mcs_limits = (constraints.get("mcs_min", 0), constraints.get("mcs_max", 27))
        max_step = int(constraints.get("mcs_max_step", self.cfg.mcs_step))

        current_mcs = self._safe_int(constraints.get("current_mcs"), default=10)
        if ws.bler_forecast and len(ws.bler_forecast) > 0:
            bler0 = ws.bler_forecast[0]
        else:
            bler0 = None

        mode = ws.service_mode.value
        if mode == "urllc":
            target = float(constraints.get("urllc_bler_max", 0.001))
        else:
            target = float(constraints.get("embb_bler_max", 0.10))

        new_mcs = current_mcs
        if bler0 is not None:
            if bler0 > target:
                new_mcs = max(mcs_limits[0], current_mcs - max_step)
            else:
                # если запас по BLER есть — пробуем поднять
                new_mcs = min(mcs_limits[1], current_mcs + max_step)

        # MIMO rank — в URLLC можно предпочесть более стабильный rank=1
        allowed_ranks = constraints.get("mimo_allowed", [1, 2, 4])
        if mode == "urllc":
            rank = 1 if 1 in allowed_ranks else allowed_ranks[0]
        else:
            rank = max(allowed_ranks) if allowed_ranks else self.cfg.default_mimo_rank

        # Выбираем одно действие или составное. Для простоты — составное SET_PARAMS
        return Action(type="SET_RADIO_PARAMS", params={"mcs_index": int(new_mcs), "mimo_rank": int(rank)})

    def safe_fallback(self, ws: WorldState, deadline: Deadline) -> Action:
        """
        Безопасное действие, если deadline или валидация не прошла.
        Пример: не повышать MCS, зафиксировать более консервативные параметры.
        """
        constraints = ws.constraints or {}
        current_mcs = self._safe_int(constraints.get("current_mcs"), default=10)
        mcs_min = int(constraints.get("mcs_min", 0))
        max_step = int(constraints.get("mcs_max_step", 1))

        conservative_mcs = max(mcs_min, current_mcs - max_step)

        allowed_ranks = constraints.get("mimo_allowed", [1, 2, 4])
        rank = 1 if 1 in allowed_ranks else (allowed_ranks[0] if allowed_ranks else 1)

        return Action(
            type="SET_RADIO_PARAMS",
            params={"mcs_index": int(conservative_mcs), "mimo_rank": int(rank), "fallback": True},
        )

    @staticmethod
    def _safe_int(x: Any, default: int) -> int:
        try:
            if x is None:
                return default
            return int(x)
        except Exception:  # noqa: BLE001
            return default
