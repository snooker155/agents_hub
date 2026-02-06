from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from domains.telecom.controller.an_agent_core.types import Action, WorldState


@dataclass
class DQNConfig:
    enabled: bool = False
    weights_path: str = "./data/models/dqn.pt"
    epsilon: float = 0.05
    gamma: float = 0.99


class DQNPolicy:
    """
    Заглушка DQN:
      - В будущем: torch модель, state->q_values по дискретным действиям.
      - Сейчас: best-effort рекомендация не делать слишком агрессивных шагов.
    """

    def __init__(self, cfg: DQNConfig) -> None:
        self.cfg = cfg
        self._model = None

        if self.cfg.enabled:
            try:
                import torch  # noqa: F401
                # self._model = torch.load(cfg.weights_path)
            except Exception:
                self._model = None

    def choose_action(self, candidate: Action, ws: WorldState) -> Action:
        # пока просто пропускаем candidate
        # можно добавить epsilon-greedy шум/ограничение
        return candidate

    def q_debug(self) -> Dict[str, Any]:
        return {"enabled": self.cfg.enabled, "has_model": self._model is not None}
