from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from domains.telecom.controller.an_agent_core.types import Observation


@dataclass
class VectorStoreConfig:
    backend: str = "inmemory"  # inmemory|faiss (позже)
    dim: int = 8
    normalize: bool = True


class VectorStore:
    """
    Минимальный интерфейс векторной памяти.
    Сейчас: inmemory (list + cosine/евклид по простым feature-векторам).
    Позже: подменяется на FAISS.
    """

    def __init__(self, cfg: VectorStoreConfig) -> None:
        self.cfg = cfg
        self._items: List[Tuple[List[float], Dict[str, Any]]] = []

    def add(self, vector: List[float], payload: Dict[str, Any]) -> None:
        self._items.append((vector, payload))

    def add_observation(self, obs: Observation) -> None:
        vec = self.embed_observation(obs)
        payload = {
            "ts_ms": obs.ts_ms,
            "cqi": obs.cqi,
            "sinr_db": obs.sinr_db,
            "bler": obs.bler,
            "tpt_mbps": obs.tpt_mbps,
            "mcs_index": obs.mcs_index,
            "mimo_rank": obs.mimo_rank,
            "service_mode": obs.service_mode.value,
        }
        self.add(vec, payload)

    def search(self, query_vec: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        # простейшая L2 метрика
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for v, payload in self._items:
            d = _l2(query_vec, v)
            scored.append((d, payload))
        scored.sort(key=lambda x: x[0])
        return [p for _, p in scored[:top_k]]

    def embed_observation(self, obs: Observation) -> List[float]:
        # В каркасе эмбеддинг — ручной feature-вектор фиксированной длины.
        # В реальной системе: learned embedding или конкатенация нормализованных KPI.
        def f(x: Optional[float]) -> float:
            return 0.0 if x is None else float(x)

        return [
            f(obs.cqi),
            f(obs.sinr_db),
            f(obs.ack_rate),
            f(obs.nack_rate),
            f(obs.bler),
            f(obs.tpt_mbps),
            float(obs.mcs_index or 0),
            float(obs.mimo_rank or 0),
        ]


def _l2(a: List[float], b: List[float]) -> float:
    n = min(len(a), len(b))
    s = 0.0
    for i in range(n):
        d = a[i] - b[i]
        s += d * d
    return s ** 0.5
