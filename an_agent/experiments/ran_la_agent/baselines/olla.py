from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from an_agent_core.types import Action, Observation


@dataclass
class OLLAConfig:
    """
    Упрощённый OLLA baseline:
    - если BLER выше target -> уменьшаем MCS
    - если ниже -> увеличиваем
    """

    target_bler: float = 0.10
    step: int = 1
    mcs_min: int = 0
    mcs_max: int = 27


class OLLAController:
    def __init__(self, cfg: OLLAConfig) -> None:
        self.cfg = cfg

    def decide(self, obs: Observation) -> Action:
        mcs = int(obs.mcs_index or 10)
        bler = obs.bler

        if bler is None:
            return Action(type="NO_OP", params={"reason": "no_bler"})

        if bler > self.cfg.target_bler:
            mcs = max(self.cfg.mcs_min, mcs - self.cfg.step)
        else:
            mcs = min(self.cfg.mcs_max, mcs + self.cfg.step)

        # baseline не трогает rank
        rank = int(obs.mimo_rank or 1)
        return Action(type="SET_RADIO_PARAMS", params={"mcs_index": mcs, "mimo_rank": rank, "baseline": "OLLA"})
