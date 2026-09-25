"""
Where a workspace file is used.

Each object that takes a file keeps the id on its own record, so "where used"
reads those records back rather than a second index that could drift:

- a memory pool: ``rag_files[].workspace_file_id`` on the pool (set by
  ``POST /api/shared-memory/{id}/files/from-workspace``);
- a task: ``Task.file_ids``;
- an eval case: ``Case.file_ids`` inside its eval set.

A chat turn keeps no record of its own that outlives the request, so the
attachment path writes a row to ``workspace_file_uses`` instead
(:func:`files.service.record_use`), keyed by the conversation id.

Every lookup is best-effort and scoped to the file's own workspace: a store
that cannot be read leaves its list empty rather than failing the whole
answer.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from files import service

log = logging.getLogger(__name__)


def _memory_pools(file_id: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        from memory.store import MemoryStore
        for mem in MemoryStore().load():
            for entry in getattr(mem, "rag_files", None) or []:
                if isinstance(entry, dict) and entry.get("workspace_file_id") == file_id:
                    out.append({
                        "pool_id": str(mem.id), "name": mem.name,
                        "filename": entry.get("filename"), "status": entry.get("status"),
                        "chunks": entry.get("chunks"),
                    })
    except Exception:  # noqa: BLE001 - one unreadable store leaves its list empty
        log.debug("files: could not scan memory pools for %s", file_id, exc_info=True)
    return out


def _tasks(file_id: str, workspace: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        from tasks import service as tasks_service
        for task in tasks_service.list_tasks():
            if (task.workspace or "") != workspace:
                continue
            if file_id in (getattr(task, "file_ids", None) or []):
                status = getattr(task.status, "value", task.status)
                out.append({"task_id": str(task.id), "key": task.key, "title": task.title,
                            "status": status})
    except Exception:  # noqa: BLE001 - one unreadable store leaves its list empty
        log.debug("files: could not scan tasks for %s", file_id, exc_info=True)
    return out


def _eval_cases(file_id: str, workspace: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        from evals import store as eval_store
        for evalset in eval_store.list_eval_sets(workspace):
            for case in evalset.cases:
                if file_id in (getattr(case, "file_ids", None) or []):
                    out.append({"eval_set_id": evalset.eval_set_id, "name": evalset.name,
                                "case_id": case.case_id, "input": (case.input or "")[:120]})
    except Exception:  # noqa: BLE001 - one unreadable store leaves its list empty
        log.debug("files: could not scan eval sets for %s", file_id, exc_info=True)
    return out


def where_used(file_id: str) -> Dict[str, Any]:
    """Every place that references ``file_id``: chat conversations, memory
    pools, tasks and eval cases, plus the run that produced it when an agent
    saved it. Works on a deleted file too (its tombstone names the
    workspace), so the Files page can say what a deletion left dangling."""
    record = service.get_file(file_id, include_deleted=True)
    if record is None:
        raise KeyError(file_id)
    workspace = record["workspace"]
    chats = [{"conversation_id": u["ref_id"], "title": u["label"], "at": u["at"]}
             for u in service.list_uses(file_id, "chat")]
    result: Dict[str, Any] = {
        "file_id": file_id,
        "chats": chats,
        "memory_pools": _memory_pools(file_id),
        "tasks": _tasks(file_id, workspace),
        "eval_cases": _eval_cases(file_id, workspace),
    }
    meta = record.get("meta") or {}
    if record.get("source") == "agent" and (meta.get("run_id") or meta.get("agent_id")):
        result["produced_by"] = {"run_id": meta.get("run_id"), "agent_id": meta.get("agent_id")}
    result["total"] = sum(len(result[k]) for k in ("chats", "memory_pools", "tasks", "eval_cases"))
    return result


__all__ = ["where_used"]
