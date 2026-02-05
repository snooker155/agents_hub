from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """A single tool call result captured from the agent run."""
    name: str
    args: Dict[str, Any] = Field(default_factory=dict)
    output: str = ""


class AgentResult(BaseModel):
    """Normalized public result returned by the swe_agent APIs."""
    ok: bool
    status: str
    task_id: Optional[str] = None
    agent_output: Optional[str] = None
    error: Optional[str] = None
    steps: List[ToolResult] = Field(default_factory=list)
    changed_files: List[str] = Field(default_factory=list)


__all__ = ["ToolResult", "AgentResult"]
