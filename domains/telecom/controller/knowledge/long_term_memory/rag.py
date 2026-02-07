from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from domains.telecom.controller.an_agent_core.types import Observation, WorldState

from .vector_store import VectorStore


@dataclass
class RAGConfig:
    enabled: bool = True
    top_k: int = 8
    min_score: float = 0.15  # в inmemory мы используем L2, поэтому min_score тут best-effort
    max_context_items: int = 10


class Retriever:
    """
    Retriever (RAG): ищет похожие кейсы в vector_store и возвращает “контекстные подсказки”.
    Сейчас: просто top-k похожих observation payloads.
    """

    def __init__(self, cfg: RAGConfig, vector_store: VectorStore) -> None:
        self.cfg = cfg
        self.vector_store = vector_store

    def retrieve(self, obs: Observation, ws: WorldState) -> List[Dict[str, Any]]:
        if not self.cfg.enabled:
            return []
        q = self.vector_store.embed_observation(obs)
        items = self.vector_store.search(q, top_k=self.cfg.top_k)
        return items[: self.cfg.max_context_items]
