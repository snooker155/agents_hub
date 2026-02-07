from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from uuid import UUID, uuid4
from enum import Enum

class RequirementStatus(str, Enum):
    draft = "draft"
    approved = "approved"
    in_development = "in_development"
    implemented = "implemented"
    verified = "verified"
    rejected = "rejected"

class Requirement(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    title: str
    description: str
    source_incident_id: Optional[str] = None
    status: RequirementStatus = Field(default=RequirementStatus.draft)
    priority: int = 1

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    metadata: Dict[str, Any] = Field(default_factory=dict)
