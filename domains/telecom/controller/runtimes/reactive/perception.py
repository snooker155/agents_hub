from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from domains.telecom.controller.an_agent_core.clock import Deadline
from domains.telecom.controller.an_agent_core.types import Observation, WorldState

# зависимости подключим позже (knowledge/models)
# здесь оставляем мягкие интерфейсы (duck-typing)


@dataclass
class PerceptionConfig:
    enable_rag: bool = True


class Perception:
    """
    Perception:
    - нормализует observation
    - применяет фильтры/оценки (например Kalman SINR)
    - может подтянуть контекст из памяти (RAG/граф), но умеет пропустить по дедлайну
    """

    def __init__(
        self,
        cfg: PerceptionConfig,
        smoother: Any,  # models.situation_awareness.filters.Smoother
        sinr_filter: Any,  # models.situation_awareness.filters.Kalman1D
        rag: Optional[Any] = None,  # knowledge.long_term_memory.rag.Retriever
        graph: Optional[Any] = None,  # knowledge.long_term_memory.graph_store.GraphStore
        constraints_provider: Optional[Any] = None,  # knowledge.rules.constraints.ConstraintsProvider
        memory_writer: Optional[Any] = None,  # knowledge.long_term_memory.vector_store.VectorStore
        predictor: Optional[Any] = None,  # models.situation_awareness.bler_lstm.BLERPredictor
    ) -> None:
        self.cfg = cfg
        self.smoother = smoother
        self.sinr_filter = sinr_filter
        self.rag = rag
        self.graph = graph
        self.constraints_provider = constraints_provider
        self.memory_writer = memory_writer
        self.predictor = predictor

    def enrich(self, obs: Observation, deadline: Deadline) -> WorldState:
        ws = WorldState(ts_ms=obs.ts_ms, service_mode=obs.service_mode)

        # 1) фильтрация/сглаживание
        ws.ack_rate_smoothed = self.smoother.update("ack", obs.ack_rate)
        ws.nack_rate_smoothed = self.smoother.update("nack", obs.nack_rate)
        ws.tpt_smoothed_mbps = self.smoother.update("tpt", obs.tpt_mbps)

        if obs.sinr_db is not None:
            ws.sinr_est_db = self.sinr_filter.update(obs.sinr_db)
        else:
            ws.sinr_est_db = None

        # 2) обновление памяти (best-effort)
        if self.memory_writer is not None:
            self.memory_writer.add_observation(obs)

        # 3) ограничения/правила
        if self.constraints_provider is not None:
            ws.constraints = self.constraints_provider.get_constraints(obs, ws)

        # 4) retrieval (best-effort, отключаем по дедлайну)
        if self.cfg.enable_rag and self.rag is not None and not deadline.is_late():
            ws.rag_items = self.rag.retrieve(obs, ws)

        if self.graph is not None and not deadline.is_late():
            # Например: факты про текущий MCS или допустимые диапазоны
            ws.graph_facts = self.graph.lookup_facts(obs, ws)

        # 5) прогноз (best-effort)
        if self.predictor is not None and not deadline.is_late():
            ws.bler_forecast = self.predictor.predict_bler(obs, ws)

        return ws
