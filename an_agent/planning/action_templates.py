from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from an_agent_core.types import Action


@dataclass(frozen=True)
class RadioParams:
    mcs_index: int
    mimo_rank: int


class ActionTemplates:
    """
    Набор фабрик действий. Держим в одном месте, чтобы:
      - planner не “размазывал” форматы southbound команд
      - проще менять типы actions под конкретный драйвер
    """

    @staticmethod
    def set_radio_params(mcs_index: int, mimo_rank: int, **extra: Any) -> Action:
        params: Dict[str, Any] = {"mcs_index": int(mcs_index), "mimo_rank": int(mimo_rank)}
        params.update(extra)
        return Action(type="SET_RADIO_PARAMS", params=params)

    @staticmethod
    def no_op(reason: str = "no_op") -> Action:
        return Action(type="NO_OP", params={"reason": reason})

    @staticmethod
    def clamp_radio_params(
        desired_mcs: int,
        desired_rank: int,
        constraints: Dict[str, Any],
    ) -> RadioParams:
        mcs_min = int(constraints.get("mcs_min", 0))
        mcs_max = int(constraints.get("mcs_max", 27))
        allowed_ranks = [int(r) for r in constraints.get("mimo_allowed", [1, 2, 4])]

        mcs = max(mcs_min, min(mcs_max, int(desired_mcs)))
        rank = int(desired_rank)
        if rank not in allowed_ranks:
            rank = allowed_ranks[0] if allowed_ranks else 1

        return RadioParams(mcs_index=mcs, mimo_rank=rank)
