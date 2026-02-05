from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger

from an_agent_core.types import Decision, Observation
from knowledge.long_term_memory.relational_store import RelationalStore


@dataclass
class KPILoggerConfig:
    enabled: bool = True
    log_every: int = 20  # писать в stdout раз в N наблюдений


class KPILogger:
    """
    Логирует KPI и решения:
    - в stdout (логгер)
    - в relational store (sqlite)
    """

    def __init__(self, cfg: KPILoggerConfig, store: Optional[RelationalStore] = None) -> None:
        self.cfg = cfg
        self.store = store
        self._count = 0

    def on_observation(self, obs: Observation) -> None:
        if not self.cfg.enabled:
            return
        self._count += 1
        if self.store is not None:
            self.store.log_observation(obs)

        if self._count % max(1, self.cfg.log_every) == 0:
            logger.info(
                "OBS ts={} mode={} sinr={:.2f} mcs={} rank={} bler={:.4f} tpt={:.2f}",
                obs.ts_ms,
                obs.service_mode.value,
                obs.sinr_db or 0.0,
                obs.mcs_index,
                obs.mimo_rank,
                obs.bler or 0.0,
                obs.tpt_mbps or 0.0,
            )

    def on_decision(self, d: Decision) -> None:
        if not self.cfg.enabled:
            return
        if self.store is not None:
            self.store.log_decision(d)

        # решения логируем реже — но можно всегда
        logger.debug(
            "DEC ts={} action={} params={} ok={} latency_ms={:.3f}",
            d.ts_ms,
            d.action.type,
            d.action.params,
            d.constraints_passed,
            d.latency_ms,
        )
