from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from domains.telecom.controller.an_agent_core.clock import now_ms
from domains.telecom.controller.an_agent_core.types import Observation, ServiceMode


@dataclass
class RANAdapterConfig:
    """
    В skeleton это mock-адаптер.
    Потом можно заменить на реальный адаптер (E2, gNB logs, UE metrics, etc.).
    """

    poll_interval_ms: int = 5
    mode: str = "embb"


class MockRANTelemetryAdapter:
    """
    Генерирует правдоподобные метрики:
    - SINR шумит
    - BLER зависит от SINR и MCS
    - TPT зависит от MCS и SINR
    """

    def __init__(self, cfg: RANAdapterConfig) -> None:
        self.cfg = cfg
        self._last_ts = 0
        self._sinr = 10.0
        self._mcs = 10
        self._rank = 1

    def poll(self) -> Optional[Observation]:
        ts = now_ms()
        if ts - self._last_ts < self.cfg.poll_interval_ms:
            return None
        self._last_ts = ts

        # эволюция канала
        self._sinr += random.uniform(-0.8, 0.8)
        self._sinr = max(-5.0, min(30.0, self._sinr))

        # имитация "текущих" параметров (как будто из RAN)
        # в реальной системе это пришло бы из телеметрии
        mcs = self._mcs
        rank = self._rank

        # BLER грубо: выше MCS и ниже SINR -> хуже
        bler = _sigmoid((mcs - 12) * 0.25 - (self._sinr - 10) * 0.18)
        bler = max(0.0, min(1.0, bler + random.uniform(-0.02, 0.02)))

        # throughput: растёт с MCS и SINR, но падает от BLER
        tpt = max(0.0, (mcs + 1) * 5.0 + self._sinr * 2.5)
        tpt *= (1.0 - bler)
        tpt += random.uniform(-2.0, 2.0)

        # ack/nack
        ack = max(0.0, min(1.0, 1.0 - bler + random.uniform(-0.01, 0.01)))
        nack = max(0.0, min(1.0, bler + random.uniform(-0.01, 0.01)))

        cqi = max(0.0, min(15.0, (self._sinr + 5.0) / 2.0))

        mode = ServiceMode(self.cfg.mode.lower()) if self.cfg.mode.lower() in ("embb", "urllc") else ServiceMode.EMBB

        return Observation(
            ts_ms=ts,
            deadline_ms=10,
            cqi=cqi,
            sinr_db=self._sinr,
            ack_rate=ack,
            nack_rate=nack,
            bler=bler,
            tpt_mbps=tpt,
            mcs_index=mcs,
            mimo_rank=rank,
            service_mode=mode,
        )


def _sigmoid(x: float) -> float:
    # стабильная сигмоида
    if x >= 0:
        z = 1.0 / (1.0 + pow(2.718281828, -x))
    else:
        ex = pow(2.718281828, x)
        z = ex / (1.0 + ex)
    return z
