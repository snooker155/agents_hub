from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class SharedMemory(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    type: str = "text"
    description: str = ""
    workspace: Optional[str] = None
    files: List[Dict[str, Any]] = Field(default_factory=list)    # [{name, content, rag_status, ...}]
    notes: List[Dict[str, Any]] = Field(default_factory=list)    # [{id, title, content, created_at}]
    kv_pairs: List[Dict[str, Any]] = Field(default_factory=list) # [{key, value, description}]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        """Update the updated_at timestamp to now (UTC)."""
        try:
            object.__setattr__(self, "updated_at", datetime.now(timezone.utc))
        except Exception:
            setattr(self, "updated_at", datetime.now(timezone.utc))
