from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List

from an_agent_core.types import Goal, WorldState


@dataclass
class GoalMLPConfig:
    enabled: bool = True
    temperature: float = 1.0


class GoalScorer:
    """
    В статье MLP ранжирует candidate goals быстро (<1ms).
    В skeleton:
      - без torch: делаем простой скоринг по эвристике и softmax-подобной нормализации.
      - позже можно заменить на torch MLP.
    """

    def __init__(self, cfg: GoalMLPConfig) -> None:
        self.cfg = cfg

    def rank(self, goals: List[Goal], ws: WorldState) -> List[Goal]:
        if not self.cfg.enabled or not goals:
            return goals

        scores = []
        for g in goals:
            s = self._score(g, ws)
            scores.append(s)

        # softmax для приоритетов
        temp = max(1e-6, float(self.cfg.temperature))
        exps = [math.exp(s / temp) for s in scores]
        z = sum(exps) or 1.0
        probs = [e / z for e in exps]

        out: List[Goal] = []
        for g, p in zip(goals, probs):
            out.append(Goal(name=g.name, priority=float(p), params=dict(g.params)))
        out.sort(key=lambda x: x.priority, reverse=True)
        return out

    def _score(self, goal: Goal, ws: WorldState) -> float:
        # Минимально разумная эвристика
        mode = ws.service_mode.value
        bler0 = ws.bler_forecast[0] if ws.bler_forecast else None
        tpt = ws.tpt_smoothed_mbps if ws.tpt_smoothed_mbps is not None else 0.0

        if mode == "urllc":
            if goal.name in ("minimize_bler", "keep_latency_sla"):
                base = 2.0
            else:
                base = 0.5
            if bler0 is not None:
                base += min(2.0, bler0 * 1000.0)  # чем хуже BLER, тем сильнее “minimize”
            return base

        # embb
        if goal.name == "maximize_throughput":
            return 1.5 + min(2.0, tpt / 100.0)
        if goal.name == "keep_bler_under":
            if bler0 is None:
                return 0.8
            return 0.8 + min(1.5, bler0 * 5.0)
        return 0.1
