from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from an_agent_core.types import Goal, WorldState


@dataclass
class ConflictResolverConfig:
    """
    Простые правила при конфликте целей.
    Можно расширять: учитывать weights из RewardShaper, SLA, доменную онтологию и т.д.
    """

    urllc_bler_priority_boost: float = 2.0
    embb_tpt_priority_boost: float = 1.0


class ConflictResolver:
    """
    Разрешает конфликты между goals:
      - если URLLC: приоритет “minimize_bler”
      - если eMBB: приоритет “maximize_throughput”
    """

    def __init__(self, cfg: ConflictResolverConfig) -> None:
        self.cfg = cfg

    def reconcile(self, goals: List[Goal], ws: WorldState) -> List[Goal]:
        if not goals:
            return []

        mode = ws.service_mode.value
        out: List[Goal] = []
        for g in goals:
            pr = float(g.priority)
            if mode == "urllc" and g.name in ("minimize_bler", "keep_latency_sla"):
                pr *= self.cfg.urllc_bler_priority_boost
            if mode == "embb" and g.name == "maximize_throughput":
                pr *= self.cfg.embb_tpt_priority_boost
            out.append(Goal(name=g.name, priority=pr, params=dict(g.params)))

        out.sort(key=lambda x: x.priority, reverse=True)
        return out

    def top(self, goals: List[Goal], ws: WorldState) -> Goal:
        return self.reconcile(goals, ws)[0]
