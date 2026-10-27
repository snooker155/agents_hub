"""
0048: the Planner schedules agent runs.

A new job kind, ``agent_run``, starts an agent in a conversation of its own
with no task (plans.service._fire_agent_run); ``schedule_agent_run`` is the
Planner's tool for it. The startup sync (common/bootstrap.py) skips a record
the operator edited, so this adds the tool to the ``planner`` record once.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

TOOL = "schedule_agent_run"


def upgrade(conn: Any, dialect: str) -> None:
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'documents'",
    ).fetchone()
    if not exists:
        return
    row = conn.execute(
        "SELECT doc FROM documents WHERE store = 'agents' AND key = 'planner'",
    ).fetchone()
    if row is None:
        return
    try:
        doc = json.loads(row[0])
    except (TypeError, ValueError):
        return
    if not isinstance(doc, dict):
        return
    tools = list(doc.get("tools") or [])
    if TOOL in tools:
        return
    at = tools.index("schedule_task") + 1 if "schedule_task" in tools else len(tools)
    doc["tools"] = tools[:at] + [TOOL] + tools[at:]
    conn.execute(
        "UPDATE documents SET doc = ?, updated_at = ? WHERE store = 'agents' AND key = 'planner'",
        (json.dumps(doc), datetime.now(timezone.utc).isoformat()),
    )
