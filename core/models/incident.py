from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from uuid import UUID, uuid4
from enum import Enum

class IncidentSeverity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"

class Incident(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    origin: str  # node_id
    summary: str
    severity: IncidentSeverity = Field(default=IncidentSeverity.medium)
    causes: List[str] = Field(default_factory=list)
    suggested_actions: List[str] = Field(default_factory=list)
    confidence: float = 1.0
    ts: float = Field(default_factory=lambda: datetime.now(timezone.utc).timestamp())

    # Extended fields
    resolved_ts: Optional[float] = None
    opinions: Dict[str, Dict[str, Any]] = Field(default_factory=dict) # nid -> opinion
    status: str = "active" # active, resolved, escalated
