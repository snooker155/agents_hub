from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from domains.telecom.controller.an_agent_core.types import Action, WorldState

"""
MCTS в статье упоминается как возможное расширение для multi-step planning.
В skeleton — интерфейс и простая заглушка: "rollout" через provided transition/reward.
"""


@dataclass
class MCTSConfig:
    enabled: bool = False
    simulations: int = 100
    max_depth: int = 5


TransitionFn = Callable[[WorldState, Action], WorldState]
RewardFn = Callable[[WorldState], float]


class MCTSPlanner:
    def __init__(self, cfg: MCTSConfig, transition: TransitionFn, reward: RewardFn) -> None:
        self.cfg = cfg
        self.transition = transition
        self.reward = reward

    def plan(self, ws: WorldState, candidates: List[Action]) -> Action:
        if not self.cfg.enabled or not candidates:
            return candidates[0] if candidates else Action(type="NO_OP", params={"reason": "no_candidates"})

        # Заглушка: оцениваем кандидатов одноступенчато (без дерева)
        best_a = candidates[0]
        best_r = float("-inf")
        for a in candidates:
            ws2 = self.transition(ws, a)
            r = self.reward(ws2)
            if r > best_r:
                best_r = r
                best_a = a
        return best_a
