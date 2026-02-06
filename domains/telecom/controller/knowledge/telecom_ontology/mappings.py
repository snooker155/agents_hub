from __future__ import annotations

from typing import Any, Dict, Optional

from domains.telecom.controller.an_agent_core.types import Observation
from .schema import TelemetryFacts


def observation_to_facts(obs: Observation) -> TelemetryFacts:
    """
    Нормализация Observation -> TelemetryFacts.
    Здесь можно:
    - приводить единицы измерения
    - чистить NaN
    - квантизировать/бинировать CQI/MCS и т.д.
    """
    return TelemetryFacts(
        cqi=_f(obs.cqi),
        sinr_db=_f(obs.sinr_db),
        bler=_f(obs.bler),
        tpt_mbps=_f(obs.tpt_mbps),
        mcs_index=_i(obs.mcs_index),
        mimo_rank=_i(obs.mimo_rank),
    )


def _f(x: Optional[float]) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except Exception:  # noqa: BLE001
        return None


def _i(x: Optional[int]) -> Optional[int]:
    if x is None:
        return None
    try:
        return int(x)
    except Exception:  # noqa: BLE001
        return None
