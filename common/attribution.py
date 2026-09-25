"""
Who a run is charged to (docs/costs.md "Attribution").

A new run record carries ``launched_by`` (the user), ``key_id`` (the personal
API key the request came in on, if any) and ``project_id`` (the project of its
task, if any). ``managers.runs.store`` stamps agent runs and
``common.entity_runs`` stamps flow, loop, team and scenario runs, both through
:func:`stamp`, once, when the record is created.

In the backend both come from the request's context variables
(``common.identity`` and ``common.api_keys``). A launched run's process has no
request, so the launcher hands both down through the environment
(:data:`USER_ENV`, :data:`KEY_ENV`, set by ``common.subprocess_env``); a run
the child creates in turn (a delegated subtask, a grading) is then charged to
the same person and key as the run that made it, and counts against that
key's monthly quota.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

USER_ENV = "AGENTS_HUB_RUN_LAUNCHED_BY"
KEY_ENV = "AGENTS_HUB_RUN_KEY_ID"


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


def launching_key() -> Optional[str]:
    """The personal API key a run created now is charged to, if any."""
    from common.api_keys import current_key_id
    return current_key_id() or (os.environ.get(KEY_ENV) or "").strip() or None


def child_env(user_id: Optional[str], key_id: Optional[str]) -> Dict[str, str]:
    """What a launched process needs to charge its own runs the same way."""
    env: Dict[str, str] = {}
    if user_id:
        env[USER_ENV] = str(user_id)
    if key_id:
        env[KEY_ENV] = str(key_id)
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
    """Fill ``launched_by``, ``key_id`` and ``project_id`` on a brand-new run
    record, keeping any the caller already set."""
    rec.setdefault("launched_by", launching_user())
    key_id = launching_key()
    if key_id and not rec.get("key_id"):
        rec["key_id"] = key_id
    if rec.get("task_id") and not rec.get("project_id"):
        project_id = _project_id_for_task(rec["task_id"])
        if project_id:
            rec["project_id"] = project_id


def check_launch_budget() -> None:
    """Refuse a launch charged to a key that already spent its month
    (``budget_usd_per_month``, docs/api-keys.md "Money quota"). Raises
    ``common.api_keys.KeyBudgetExceededError``, which the backend answers
    with a 429."""
    key_id = launching_key()
    if not key_id:
        return
    from common.api_keys import KeyBudgetExceededError, get_key, key_month_spend_usd
    cap = (get_key(key_id) or {}).get("budget_usd_per_month")
    if not cap:
        return
    spend = key_month_spend_usd(key_id)
    if spend >= float(cap):
        raise KeyBudgetExceededError(key_id, spend, float(cap))


__all__ = ["USER_ENV", "KEY_ENV", "launching_user", "launching_key", "child_env",
           "stamp", "check_launch_budget"]
