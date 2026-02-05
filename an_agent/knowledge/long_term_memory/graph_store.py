from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from an_agent_core.types import Observation, WorldState


@dataclass
class GraphStoreConfig:
    backend: str = "inmemory"  # inmemory|neo4j
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = "neo4j"
    database: str = "neo4j"


class GraphStore:
    """
    Минимальный интерфейс графа знаний (онтология, свойства MCS и т.п.).
    Сейчас: inmemory словарь.
    Позже: Neo4j driver.
    """

    def __init__(self, cfg: GraphStoreConfig) -> None:
        self.cfg = cfg

        # Заглушка: свойства MCS (примерные, не претендуют на точность 3GPP)
        self._mcs_facts: Dict[int, Dict[str, Any]] = {
            0: {"modulation": "QPSK", "coding_rate": 0.12, "spectral_eff": 0.23},
            10: {"modulation": "16QAM", "coding_rate": 0.44, "spectral_eff": 2.40},
            20: {"modulation": "64QAM", "coding_rate": 0.73, "spectral_eff": 4.80},
            27: {"modulation": "256QAM", "coding_rate": 0.93, "spectral_eff": 7.40},
        }

    def lookup_facts(self, obs: Observation, ws: WorldState) -> List[Dict[str, Any]]:
        facts: List[Dict[str, Any]] = []
        mcs = obs.mcs_index
        if mcs is None:
            return facts
        # ближайшее “известное” значение
        key = self._nearest_known_mcs(int(mcs))
        facts.append({"type": "mcs_properties", "mcs_index": mcs, "approx_from": key, **self._mcs_facts[key]})
        return facts

    def _nearest_known_mcs(self, mcs: int) -> int:
        keys = sorted(self._mcs_facts.keys())
        best = keys[0]
        best_d = abs(best - mcs)
        for k in keys[1:]:
            d = abs(k - mcs)
            if d < best_d:
                best = k
                best_d = d
        return best
