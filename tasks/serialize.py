"""
Turning a Task into the plain dict every surface reports.

Kept out of the API layer so a caller that only wants to *show* a task does not
have to import the routes — which pull in the agent factory and the whole LLM
stack behind it, seconds of import time for one serializer.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

log = logging.getLogger(__name__)


def task_to_dict(task: Any) -> Dict[str, Any]:
    """Convert task object to dictionary."""
    data = task.model_dump()
    data["id"] = str(data["id"])
    if data.get("parent_id"):
        data["parent_id"] = str(data["parent_id"])
    if data.get("depends"):
        data["depends"] = [str(d) for d in data["depends"]]
    # agent_state/overdue are @property, not fields, so model_dump() omits
    # them — add explicitly.
    try:
        data["agent_state"] = task.agent_state.value
    except Exception:  # noqa: BLE001 - a property that raises must not break serialization
        log.debug("agent_state read failed", exc_info=True)
        data.setdefault("agent_state", "none")
    try:
        data["overdue"] = task.overdue
    except Exception:  # noqa: BLE001 - a property that raises must not break serialization
        log.debug("overdue read failed", exc_info=True)
        data.setdefault("overdue", False)
    return data
