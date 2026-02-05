from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ServiceMode(str, Enum):
    """Режим верхнего уровня (пример под RAN LA Agent)."""

    EMBB = "embb"
    URLLC = "urllc"


class EventType(str, Enum):
    """События для event-driven контура proactive (и для наблюдаемости)."""

    OBSERVATION = "observation"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    THRESHOLD_VIOLATION = "threshold_violation"
    HUMAN_INSTRUCTION = "human_instruction"
    MODE_CHANGED = "mode_changed"
    ERROR = "error"


@dataclass(frozen=True)
class Event:
    type: EventType
    ts_ms: int
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    """
    Сырые/нормализованные наблюдения.
    Поля — примерные для RAN: можно адаптировать под любую доменную телеметрию.
    """

    ts_ms: int
    deadline_ms: int = 10

    # доменные метрики (пример)
    cqi: Optional[float] = None
    sinr_db: Optional[float] = None  # измеренная SINR (до фильтра)
    ack_rate: Optional[float] = None
    nack_rate: Optional[float] = None
    bler: Optional[float] = None
    tpt_mbps: Optional[float] = None
    mcs_index: Optional[int] = None
    mimo_rank: Optional[int] = None

    # контекст/режим
    service_mode: ServiceMode = ServiceMode.EMBB

    # произвольные поля
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorldState:
    """
    Обогащённое состояние для принятия решения:
    - фильтрация/сглаживание,
    - прогнозы,
    - retrieval контекст из памяти/графа,
    - активные ограничения/режимы.
    """

    ts_ms: int

    # фильтрованные/оценённые показатели
    sinr_est_db: Optional[float] = None
    ack_rate_smoothed: Optional[float] = None
    nack_rate_smoothed: Optional[float] = None
    tpt_smoothed_mbps: Optional[float] = None

    # прогноз
    bler_forecast: Optional[List[float]] = None  # horizon points

    # knowledge context
    rag_items: List[Dict[str, Any]] = field(default_factory=list)
    graph_facts: List[Dict[str, Any]] = field(default_factory=list)

    # режим/ограничения
    service_mode: ServiceMode = ServiceMode.EMBB
    constraints: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Intent:
    """Высокоуровневая интерпретация потребности/инструкции (proactive)."""

    name: str
    confidence: float = 1.0
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Goal:
    """Цель, которую можно оптимизировать/планировать (reactive/proactive)."""

    name: str
    priority: float = 1.0
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Action:
    """
    Конкретное действие "на юг".
    Для RAN: например SetMCS/SetMIMORank. В общем виде — тип + параметры.
    """

    type: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Decision:
    """Результат шага reactive: выбранное действие + метаданные."""

    ts_ms: int
    action: Action
    constraints_passed: bool = True
    expected_outcome: Dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    latency_ms: float = 0.0
    debug: Dict[str, Any] = field(default_factory=dict)
