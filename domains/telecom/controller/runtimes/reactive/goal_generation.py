from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from domains.telecom.controller.an_agent_core.clock import Deadline
from domains.telecom.controller.an_agent_core.types import Goal, WorldState


@dataclass
class GoalGenConfig:
    max_goals: int = 8


class GoalGenerator:
    """
    Генерация candidate goals.
    Здесь можно подключить models.policy.goal_mlp (MLP) или простые heuristics.
    """

    def __init__(self, cfg: GoalGenConfig, goal_scorer: Optional[Any] = None) -> None:
        self.cfg = cfg
        self.goal_scorer = goal_scorer  # models.policy.goal_mlp.GoalScorer

    def propose(self, ws: WorldState, deadline: Deadline) -> List[Goal]:
        # Базовые цели — зависят от режима
        if ws.service_mode.value == "urllc":
            base = [
                Goal(name="minimize_bler", priority=1.0, params={"target_bler": 0.001}),
                Goal(name="keep_latency_sla", priority=0.9, params={}),
            ]
        else:
            base = [
                Goal(name="maximize_throughput", priority=1.0, params={}),
                Goal(name="keep_bler_under", priority=0.6, params={"max_bler": 0.10}),
            ]

        goals = base

        # Если есть MLP-оценщик — можно переоценить/добавить кандидатов
        if self.goal_scorer is not None and not deadline.is_late():
            goals = self.goal_scorer.rank(goals, ws)

        return goals[: self.cfg.max_goals]


class GoalSelector:
    """Выбор одной цели из списка (простая стратегия + возможность расширения)."""

    def select(self, goals: List[Goal], ws: WorldState, deadline: Deadline) -> Goal:
        # пока просто max(priority)
        goals_sorted = sorted(goals, key=lambda g: g.priority, reverse=True)
        return goals_sorted[0]
