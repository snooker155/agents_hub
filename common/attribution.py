"""
Who a run is charged to.

A new run record carries ``launched_by`` (the user) and ``project_id`` (the
project of its task, if any). ``managers.runs.store`` stamps agent runs and
``common.entity_runs`` stamps flow, loop and team runs, both through
:func:`stamp`, once, when the record is created.

In the backend ``launched_by`` comes from the request's context variable
(``common.identity``). A launched run's process has no request, so the
launcher hands it down through the environment (:data:`USER_ENV`, set by
``common.subprocess_env``); a run the child creates in turn (a delegated
subtask, a grading) is then charged to the same person as the run that made
it.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

USER_ENV = "AGENTS_HUB_RUN_LAUNCHED_BY"


def launching_user() -> str:
    """The user a run created now belongs to: the request's, else the one
    the parent run was launched by, else the local operator."""
    from common.auth import LOCAL_OPERATOR_ID
    from common.identity import current_user_id
    user = current_user_id()
    if user == LOCAL_OPERATOR_ID:
        inherited = (os.environ.get(USER_ENV) or "").strip()
        if inherited:
            return inherited
    return user


def child_env(user_id: Optional[str]) -> Dict[str, str]:
    """What a launched process needs to charge its own runs the same way."""
    env: Dict[str, str] = {}
    if user_id:
        env[USER_ENV] = str(user_id)
    return env


def _project_id_for_task(task_id: Any) -> Optional[str]:
    if not task_id:
        return None
    try:
        from tasks import service as tasks_service
        task = tasks_service.get_task(task_id)
    except Exception:  # noqa: BLE001 - an unreadable task never blocks writing the run
        return None
    project_id = getattr(task, "project_id", None) if task is not None else None
    return str(project_id) if project_id else None


def stamp(rec: Dict[str, Any]) -> None:
    """Fill ``launched_by`` and ``project_id`` on a brand-new run record,
    keeping any the caller already set."""
    rec.setdefault("launched_by", launching_user())
    if rec.get("task_id") and not rec.get("project_id"):
        project_id = _project_id_for_task(rec["task_id"])
        if project_id:
            rec["project_id"] = project_id


__all__ = ["USER_ENV", "launching_user", "child_env", "stamp"]
