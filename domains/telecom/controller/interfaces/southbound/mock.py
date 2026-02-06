from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger

from domains.telecom.controller.an_agent_core.types import Decision
from interfaces.telemetry.ran_adapter import MockRANTelemetryAdapter


@dataclass
class MockSouthboundConfig:
    enabled: bool = True


class MockSouthboundExecutor:
    """
    В mock-режиме “применение действия” просто:
      - логируем
      - (опционально) влияем на состояние mock телеметрии (MCS/Rank)
    """

    def __init__(self, cfg: MockSouthboundConfig, telemetry: Optional[MockRANTelemetryAdapter] = None) -> None:
        self.cfg = cfg
        self.telemetry = telemetry

    def apply(self, decision: Decision) -> None:
        if not self.cfg.enabled:
            return

        a = decision.action
        if a.type == "SET_RADIO_PARAMS":
            mcs = a.params.get("mcs_index")
            rank = a.params.get("mimo_rank")
            logger.info("APPLY SET_RADIO_PARAMS mcs={} rank={} ok={} latency_ms={:.3f}",
                        mcs, rank, decision.constraints_passed, decision.latency_ms)

            # если связали с mock телеметрией — обновим “текущие” параметры
            if self.telemetry is not None:
                try:
                    if mcs is not None:
                        self.telemetry._mcs = int(mcs)  # noqa: SLF001 (в mock допустимо)
                    if rank is not None:
                        self.telemetry._rank = int(rank)  # noqa: SLF001
                except Exception:
                    pass
        elif a.type == "NO_OP":
            logger.debug("APPLY NO_OP reason={}", a.params.get("reason"))
        else:
            logger.warning("APPLY unknown action type={} params={}", a.type, a.params)
