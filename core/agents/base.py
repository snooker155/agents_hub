from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from core.models.task import Task
from core.models.telecom_types import Event

class BaseAgent(ABC):
    """
    Abstract base class for all agents in the framework.
    """

    def __init__(self, agent_id: str, metadata: Optional[Dict[str, Any]] = None):
        self.agent_id = agent_id
        self.metadata = metadata or {}

    @abstractmethod
    async def process_event(self, event: Event) -> None:
        """Handle an event from the Event Bus."""
        pass

    @abstractmethod
    async def execute_task(self, task: Task) -> Any:
        """Execute a specific development or operational task."""
        pass

class OperationalAgent(BaseAgent):
    """Base class for telecom domain agents."""
    pass

class EvolutionaryAgent(BaseAgent):
    """Base class for SWE domain agents."""
    pass
