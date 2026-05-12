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
    notes: List[Dict[str, Any]] = Field(default_factory=list)   # [{id, title, content, created_at}]
    kv_pairs: List[Dict[str, Any]] = Field(default_factory=list) # legacy — migrated on load
    structured_data: Dict[str, Dict[str, Any]] = Field(default_factory=dict)  # slot -> data dict
    rag_files: List[Dict[str, Any]] = Field(default_factory=list) # [{filename, workspace, status, indexed_at, chunks}]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def model_post_init(self, __context: Any) -> None:
        """Migrate legacy formats into the flat slot -> data dict on load."""
        # legacy kv_pairs list
        if self.kv_pairs:
            for kv in self.kv_pairs:
                key = kv.get("key")
                if key and key not in self.structured_data:
                    self.structured_data[key] = {"value": kv.get("value", "")}
            self.kv_pairs = []
        # legacy {schema_type, data} wrapper — unwrap to just the data dict
        for slot, val in list(self.structured_data.items()):
            if isinstance(val, dict) and "data" in val and "schema_type" in val:
                self.structured_data[slot] = val["data"]

    def touch(self) -> None:
        """Update the updated_at timestamp to now (UTC)."""
        try:
            object.__setattr__(self, "updated_at", datetime.now(timezone.utc))
        except Exception:
            setattr(self, "updated_at", datetime.now(timezone.utc))
