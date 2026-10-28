"""
0046: the Task Manager keeps the tracker; the Planner keeps only the Plan.

The Planner used to hold the task tools as well as the scheduling ones, and
a small model asked "which jobs do we have?" answered with tracker tasks, or
created a task when asked to pause a job. Tasks now belong to a system agent
of their own, ``task_manager``, which also replaces the Planner in the task
planner chat of the Tasks page and of a project's Tasks tab.

The startup sync (common/bootstrap.py) adds the new agent to an existing
registry, but it never takes a tool away and skips records the operator
edited, so this does the rest once: the task tools and the task planning
commands leave the ``planner`` record, and every agent that delegates to
``@planner`` also reaches ``@task_manager``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from common.migrations import table_exists

TASK_TOOLS = ("list_tasks", "get_task", "create_task", "add_subtask", "create_sequence",
              "update_task", "block_task", "set_task_dependencies", "get_task_result")
TASK_COMMANDS = ("/plan", "/plan-from-view")


def _rows(conn: Any):
    for key, doc in conn.execute("SELECT key, doc FROM documents WHERE store = 'agents'").fetchall():
        try:
            parsed = json.loads(doc)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            yield key, parsed


def _save(conn: Any, key: str, doc: dict) -> None:
    conn.execute(
        "UPDATE documents SET doc = ?, updated_at = ? WHERE store = 'agents' AND key = ?",
        (json.dumps(doc), datetime.now(timezone.utc).isoformat(), key),
    )


def upgrade(conn: Any, dialect: str) -> None:
    if not table_exists(conn, dialect, "documents"):
        return
    for key, doc in list(_rows(conn)):
        changed = False
        if key == "planner":
            tools = [t for t in (doc.get("tools") or []) if t not in TASK_TOOLS]
            if tools != list(doc.get("tools") or []):
                doc["tools"] = tools
                changed = True
            policy = doc.get("tool_policy")
            if isinstance(policy, dict) and any(t in policy for t in TASK_TOOLS):
                doc["tool_policy"] = {k: v for k, v in policy.items() if k not in TASK_TOOLS}
                changed = True
            commands = doc.get("commands")
            if isinstance(commands, list):
                kept = [c for c in commands if not (isinstance(c, dict) and c.get("name") in TASK_COMMANDS)]
                if kept != commands:
                    doc["commands"] = kept
                    changed = True
        delegates = doc.get("delegates")
        if isinstance(delegates, list) and "@planner" in delegates and "@task_manager" not in delegates:
            delegates.insert(delegates.index("@planner") + 1, "@task_manager")
            doc["delegates"] = delegates
            changed = True
        if changed:
            _save(conn, key, doc)
