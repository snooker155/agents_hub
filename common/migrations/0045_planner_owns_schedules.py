"""
0045: the Planner owns schedules and deployments; the main agent no longer does.

The main agent used to hold the scheduling tools itself. They now belong to
the system agent ``planner`` (agents/definitions/planner), which the main
agent delegates to as ``@planner``, together with the new deployment tools.

The startup sync of system agents (common/bootstrap.py) only ever adds tools
to a record and skips one the operator edited, so neither half would reach an
existing install. This does both, once: the scheduling tools leave the
``main-agent`` record (and its tool policy), and the Planner's record gains
the full set. An install with no registry yet gets both from the seed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

MOVED = ("schedule_notification", "schedule_task", "list_scheduled",
         "update_scheduled", "cancel_scheduled")

PLANNER_TOOLS = MOVED + ("schedule_flow", "schedule_loop", "schedule_memory_consolidation",
                         "get_scheduled", "pause_scheduled", "resume_scheduled",
                         "run_scheduled_now", "list_agents_tool", "list_flows_tool",
                         "list_loops_tool")


def _load(conn: Any, key: str):
    row = conn.execute(
        "SELECT doc FROM documents WHERE store = 'agents' AND key = ?", (key,),
    ).fetchone()
    if row is None:
        return None
    try:
        doc = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _save(conn: Any, key: str, doc: dict) -> None:
    conn.execute(
        "UPDATE documents SET doc = ?, updated_at = ? WHERE store = 'agents' AND key = ?",
        (json.dumps(doc), datetime.now(timezone.utc).isoformat(), key),
    )


def upgrade(conn: Any, dialect: str) -> None:
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'documents'",
    ).fetchone()
    if not exists:
        return

    main = _load(conn, "main-agent")
    if main is not None:
        tools = [t for t in (main.get("tools") or []) if t not in MOVED]
        policy = main.get("tool_policy")
        changed = tools != list(main.get("tools") or [])
        if isinstance(policy, dict) and any(t in policy for t in MOVED):
            main["tool_policy"] = {k: v for k, v in policy.items() if k not in MOVED}
            changed = True
        if changed:
            main["tools"] = tools
            _save(conn, "main-agent", main)

    planner = _load(conn, "planner")
    if planner is not None:
        tools = list(planner.get("tools") or [])
        missing = [t for t in PLANNER_TOOLS if t not in tools]
        if missing:
            planner["tools"] = tools + missing
            _save(conn, "planner", planner)
