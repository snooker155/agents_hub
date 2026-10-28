"""
Tasks for the sync engine: the work item, its activity and its results.

Status is content: a task finished on one hub is finished on the other, and
the person who closed it expects it closed everywhere. What is dropped is
what only means something on the hub that ran it: the workspace and creating
user (set on apply), the run and session a worker attached, retry and review
counters, and the pending question, pending approval and approved calls. An
approval in particular is a decision about this hub's tools and must not be
imported from another. A task already here keeps its own values for those.

Children travel in the document: the activity log and the results, each
capped (newest kept, and the oldest dropped until the whole fits), so one
long-running task cannot make a record the other side refuses. The routing
log is global and is not part of a task.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from common import db
from hubsync.adapters.base import Adapter, AdapterError, register

_LOCAL_KEYS = (
    "id", "workspace", "created_by_user", "pending_question", "pending_approval",
    "approved_calls", "retry_count", "review_cycles", "assigned_agent_run_id",
    "session_id", "pre_assignment_status",
)
_MAX_ACTIVITY = 200
_MAX_RESULTS = 50
_MAX_BYTES = 1_000_000


def _fit(entries: List[dict], limit: int) -> List[dict]:
    """The newest ``limit`` entries, fewer if the JSON is still too big."""
    kept = entries[-limit:]
    while kept and len(json.dumps(kept, default=str)) > _MAX_BYTES:
        kept = kept[1:]
    return kept


class TaskAdapter(Adapter):
    kind = "task"
    order = 45
    label = "Tasks"

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        rows = db.get_conn().execute(
            "SELECT id, created_by_user FROM tasks WHERE workspace = ? ORDER BY created_at, id",
            (workspace,)).fetchall()
        return [str(r["id"]) for r in rows
                if (r["created_by_user"] or "local") in (owner, "local")]

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        from tasks import storage
        task = storage.TaskStore().get(record_id)
        if task is None:
            return None
        doc = {k: v for k, v in task.model_dump(mode="json").items() if k not in _LOCAL_KEYS}
        doc["activity"] = _fit(storage.get_activity_log(None, record_id), _MAX_ACTIVITY)
        doc["results"] = _fit(storage.get_task_results(None, record_id), _MAX_RESULTS)
        return doc

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        from tasks import storage
        store = storage.TaskStore()
        prev = store.get(record_id)
        if prev is not None and prev.workspace != workspace:
            raise AdapterError(f"task {record_id} lives in another workspace here")
        data = {k: v for k, v in doc.items() if k not in ("activity", "results")}
        data["id"] = record_id
        data["workspace"] = workspace
        local = prev.model_dump(mode="json") if prev is not None else {}
        for key in _LOCAL_KEYS:
            if key in local and key not in ("id", "workspace"):
                data[key] = local[key]
        data["created_by_user"] = local.get("created_by_user") or owner
        try:
            task = storage._parse_task(data)
        except ValueError as exc:
            raise AdapterError(f"unreadable task: {exc}") from exc
        store.add(task)
        if prev is None:
            # Arrived from another hub: it runs there, never here (hubsync/copies.py).
            from hubsync import copies
            copies.mark(record_id)
        storage.delete_activity_log(None, record_id)
        for entry in doc.get("activity") or []:
            storage.append_activity_log(None, record_id, entry)
        # set_task_result stamps updated_at, which would change the content on
        # every apply; the entry is stored as it came.
        with db.transaction() as conn:
            conn.execute("DELETE FROM task_results WHERE task_id = ?", (record_id,))
            for entry in doc.get("results") or []:
                run_id = entry.get("run_id")
                conn.execute(
                    "INSERT INTO task_results (task_id, run_id, entry) VALUES (?, ?, ?)",
                    (record_id, str(run_id) if run_id else None, db.dumps(entry)))
        return record_id

    def delete(self, workspace: str, record_id: str) -> None:
        from tasks import storage
        storage.TaskStore().delete(record_id)
        storage.delete_activity_log(None, record_id)
        storage.delete_task_result(None, record_id)
        from hubsync import copies
        copies.unmark(record_id)


register(TaskAdapter())
