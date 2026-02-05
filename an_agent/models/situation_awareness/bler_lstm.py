from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from an_agent_core.types import Observation, WorldState


@dataclass
class BLERLSTMConfig:
    enabled: bool = False
    history_ttis: int = 100
    horizon_ttis: int = 5
    weights_path: str = "./data/models/bler_lstm.pt"


class BLERPredictor:
    """
    Интерфейс “LSTM прогноз BLER”.
    В skeleton:
      - если enabled=False или torch не установлен — используем fallback эвристику.
      - позже можно загрузить torch-модель и реализовать реальный forward.
    """

    def __init__(self, cfg: BLERLSTMConfig) -> None:
        self.cfg = cfg
        self._torch_model = None  # позже: torch.nn.Module

        if self.cfg.enabled:
            # best-effort: не падаем, если torch не стоит
            try:
                import torch  # noqa: F401
                # сюда же можно добавить загрузку весов
                # self._torch_model = torch.jit.load(cfg.weights_path) или torch.load(...)
            except Exception:
                self._torch_model = None

    def predict_bler(self, obs: Observation, ws: WorldState) -> List[float]:
        if self.cfg.enabled and self._torch_model is not None:
            # Реализация будет позже (нужно определить входы)
            return self._predict_with_torch(obs, ws)

        # fallback: если есть obs.bler, строим простую “инерцию”
        base = float(obs.bler) if obs.bler is not None else 0.05
        # учтём SINR: выше SINR -> ниже BLER (грубая эвристика)
        if ws.sinr_est_db is not None:
            base = max(0.0, min(1.0, base * (1.0 - 0.02 * max(0.0, ws.sinr_est_db))))

        horizon = max(1, int(self.cfg.horizon_ttis))
        return [float(max(0.0, min(1.0, base))) for _ in range(horizon)]

    def _predict_with_torch(self, obs: Observation, ws: WorldState) -> List[float]:
        # Placeholder: чтобы не вводить ложную точность — оставим простой прогноз.
        return self.predict_bler(obs, ws)
