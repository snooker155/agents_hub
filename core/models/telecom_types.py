from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class ServiceMode(str, Enum):
    EMBB = "embb"
    URLLC = "urllc"

class EventType(str, Enum):
    OBSERVATION = "observation"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    THRESHOLD_VIOLATION = "threshold_violation"
    HUMAN_INSTRUCTION = "human_instruction"
    MODE_CHANGED = "mode_changed"
    ERROR = "error"

    # New architecture events
    KPI_DEGRADED = "kpi_degraded"
    ANOMALY_DETECTED = "anomaly_detected"
    POLICY_FAILED = "policy_failed"
    TASK_CREATED = "task_created"
    CODE_PATCHED = "code_patched"
    TEST_PASSED = "test_passed"

class Event(BaseModel):
    type: EventType
    ts_ms: int
    payload: Dict[str, Any] = Field(default_factory=dict)

class Observation(BaseModel):
    ts_ms: int
    deadline_ms: int = 10
    cqi: Optional[float] = None
    sinr_db: Optional[float] = None
    ack_rate: Optional[float] = None
    nack_rate: Optional[float] = None
    bler: Optional[float] = None
    tpt_mbps: Optional[float] = None
    mcs_index: Optional[int] = None
    mimo_rank: Optional[int] = None
    service_mode: ServiceMode = ServiceMode.EMBB
    extras: Dict[str, Any] = Field(default_factory=dict)

class WorldState(BaseModel):
    ts_ms: int
    sinr_est_db: Optional[float] = None
    ack_rate_smoothed: Optional[float] = None
    nack_rate_smoothed: Optional[float] = None
    tpt_smoothed_mbps: Optional[float] = None
    bler_forecast: Optional[List[float]] = None
    rag_items: List[Dict[str, Any]] = Field(default_factory=list)
    graph_facts: List[Dict[str, Any]] = Field(default_factory=list)
    service_mode: ServiceMode = ServiceMode.EMBB
    constraints: Dict[str, Any] = Field(default_factory=dict)

class Intent(BaseModel):
    name: str
    confidence: float = 1.0
    params: Dict[str, Any] = Field(default_factory=dict)

class Goal(BaseModel):
    name: str
    priority: float = 1.0
    params: Dict[str, Any] = Field(default_factory=dict)

class Action(BaseModel):
    type: str
    params: Dict[str, Any] = Field(default_factory=dict)

class Decision(BaseModel):
    ts_ms: int
    action: Action
    constraints_passed: bool = True
    expected_outcome: Dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    latency_ms: float = 0.0
    debug: Dict[str, Any] = Field(default_factory=dict)
