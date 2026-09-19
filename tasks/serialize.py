"""
Turning a Task into the plain dict every surface reports.

Kept out of the API layer so a caller that only wants to *show* a task does not
have to import the routes — which pull in the agent factory and the whole LLM
stack behind it, seconds of import time for one serializer.
"""
from __future__ import annotations

from typing import Any, Dict


def task_to_dict(task: Any) -> Dict[str, Any]:
    """Convert task object to dictionary."""
    data = task.model_dump() if hasattr(task, "model_dump") else task.dict()
    data["id"] = str(data["id"])
    if data.get("parent_id"):
        data["parent_id"] = str(data["parent_id"])
    if data.get("depends"):
        data["depends"] = [str(d) for d in data["depends"]]
    # agent_state is a @property not a field, so model_dump() omits it — add explicitly
    try:
        data["agent_state"] = task.agent_state.value
    except Exception:
        data.setdefault("agent_state", "none")
    return data
