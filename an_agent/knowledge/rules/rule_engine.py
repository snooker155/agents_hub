from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from an_agent_core.types import Observation, ServiceMode


@dataclass
class RuleEngineConfig:
    embb_bler_max: float = 0.10
    urllc_bler_max: float = 0.001


class RuleEngine:
    """
    Простейший rule engine:
    - выявляет нарушения порогов
    - может давать рекомендации (например, “снизить MCS”)
    В reactive это можно использовать как fallback “rules_only”.
    """

    def __init__(self, cfg: RuleEngineConfig) -> None:
        self.cfg = cfg

    def check(self, obs: Observation) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        bler = obs.bler
        if bler is None:
            return out

        if obs.service_mode == ServiceMode.URLLC and bler > self.cfg.urllc_bler_max:
            out.append({"type": "threshold", "kind": "bler", "mode": "urllc", "value": bler, "max": self.cfg.urllc_bler_max})
        if obs.service_mode == ServiceMode.EMBB and bler > self.cfg.embb_bler_max:
            out.append({"type": "threshold", "kind": "bler", "mode": "embb", "value": bler, "max": self.cfg.embb_bler_max})
        return out
