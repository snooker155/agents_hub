from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class MCSProperties:
    mcs_index: int
    modulation: str
    coding_rate: float
    spectral_eff: float


@dataclass(frozen=True)
class TelemetryFacts:
    # минимальный набор “фактов” после нормализации
    cqi: Optional[float]
    sinr_db: Optional[float]
    bler: Optional[float]
    tpt_mbps: Optional[float]
    mcs_index: Optional[int]
    mimo_rank: Optional[int]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "cqi": self.cqi,
            "sinr_db": self.sinr_db,
            "bler": self.bler,
            "tpt_mbps": self.tpt_mbps,
            "mcs_index": self.mcs_index,
            "mimo_rank": self.mimo_rank,
        }
