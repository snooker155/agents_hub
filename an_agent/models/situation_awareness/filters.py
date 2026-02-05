from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class SmoothingConfig:
    window: int = 20


class Smoother:
    """
    Sliding-window smoother для метрик (ACK/NACK/TPT и т.п.).
    """

    def __init__(self, cfg: SmoothingConfig) -> None:
        self.cfg = cfg
        self._bufs: Dict[str, list[float]] = {}

    def update(self, key: str, value: Optional[float]) -> Optional[float]:
        if value is None:
            return self.current(key)

        buf = self._bufs.setdefault(key, [])
        buf.append(float(value))
        if len(buf) > self.cfg.window:
            del buf[0 : len(buf) - self.cfg.window]
        return sum(buf) / len(buf)

    def current(self, key: str) -> Optional[float]:
        buf = self._bufs.get(key)
        if not buf:
            return None
        return sum(buf) / len(buf)


@dataclass
class KalmanConfig:
    process_var: float = 1.0e-3  # Q
    meas_var: float = 1.0e-2     # R
    init_est: float = 0.0
    init_var: float = 1.0


class Kalman1D:
    """
    Очень простой 1D Kalman filter:
      x_k = x_{k-1} + w
      z_k = x_k + v
    """

    def __init__(self, cfg: KalmanConfig) -> None:
        self.cfg = cfg
        self.x = float(cfg.init_est)
        self.p = float(cfg.init_var)

    def update(self, z: float) -> float:
        # predict
        self.p = self.p + float(self.cfg.process_var)

        # update
        k = self.p / (self.p + float(self.cfg.meas_var))
        self.x = self.x + k * (float(z) - self.x)
        self.p = (1.0 - k) * self.p
        return self.x
