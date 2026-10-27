"""
0047: a task no longer inherits its agent's criterion by itself.

Assigning an agent used to copy its ``default_outcome`` onto the task's own
``outcome``. Now the copy is a switch, ``use_agent_outcome`` (off by default).
A stored task whose outcome rubric equals its agent's default rubric was such
a copy: it gets the switch on and no outcome of its own, so it behaves as
before and the person can untick it. Gradings are kept.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def _rubric(outcome: Any) -> str:
    return str((outcome or {}).get("rubric") or "").strip() if isinstance(outcome, dict) else ""


def upgrade(conn: Any, dialect: str) -> None:
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    if not {"tasks", "documents"} <= have:
        return
    defaults = {}
    for key, doc in conn.execute("SELECT key, doc FROM documents WHERE store = 'agents'").fetchall():
        try:
            rubric = _rubric(json.loads(doc).get("default_outcome"))
        except (TypeError, ValueError, AttributeError):
            continue
        if rubric:
            defaults[key] = rubric
    if not defaults:
        return
    for tid, doc in conn.execute("SELECT id, doc FROM tasks").fetchall():
        try:
            data = json.loads(doc)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("use_agent_outcome"):
            continue
        own = _rubric(data.get("outcome"))
        if not own:
            continue
        executor = data.get("executor")
        agent = ""
        if isinstance(executor, dict) and executor.get("kind") == "agent":
            agent = str(executor.get("id") or "")
        agent = agent or str(data.get("assigned_agent_type") or "") or str(data.get("container_agent") or "")
        if not agent:
            # A blocked or reset task has no agent on it any more: the rubric
            # is a copy when exactly one agent has it as its default.
            owners = [k for k, rubric in defaults.items() if rubric == own]
            agent = owners[0] if len(owners) == 1 else ""
        if agent and defaults.get(agent) == own:
            data["outcome"] = None
            data["use_agent_outcome"] = True
            conn.execute("UPDATE tasks SET doc = ?, updated_at = ? WHERE id = ?",
                         (json.dumps(data), datetime.now(timezone.utc).isoformat(), tid))
