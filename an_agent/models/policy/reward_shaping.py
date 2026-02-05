from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass
class RewardShapingConfig:
    mode: str = "embb"
    embb_max_bler: float = 0.10
    urllc_max_bler: float = 0.001

    # веса (пример)
    embb_w_tpt: float = 1.0
    embb_w_bler: float = -0.5

    urllc_w_bler: float = -2.0
    urllc_w_tpt: float = 0.2


class RewardShaper:
    """
    Единая точка для:
    - текущего режима (embb/urllc)
    - reward функций (для RL) или просто весов KPI в scoring/выборе целей
    """

    def __init__(self, cfg: RewardShapingConfig) -> None:
        self.cfg = cfg
        self._mode = cfg.mode.lower()

    def set_mode(self, mode: str) -> None:
        self._mode = mode.lower()

    def mode(self) -> str:
        return self._mode

    def weights(self) -> Dict[str, float]:
        if self._mode == "urllc":
            return {"w_bler": self.cfg.urllc_w_bler, "w_tpt": self.cfg.urllc_w_tpt, "max_bler": self.cfg.urllc_max_bler}
        return {"w_bler": self.cfg.embb_w_bler, "w_tpt": self.cfg.embb_w_tpt, "max_bler": self.cfg.embb_max_bler}

    def compute_reward(self, bler: float, tpt: float) -> float:
        w = self.weights()
        # штраф за превышение max_bler
        penalty = 0.0
        if bler > float(w["max_bler"]):
            penalty = -1.0 * (bler - float(w["max_bler"])) * 10.0
        return float(w["w_tpt"]) * tpt + float(w["w_bler"]) * bler + penalty
