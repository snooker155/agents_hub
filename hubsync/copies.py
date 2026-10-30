"""
Tasks that arrived by sync, so this hub never runs them.

A task is synced with its status, because a task done on one hub is done on
the other. But a task that is ``ready`` here only because it is ready on the
person's laptop must not be picked up by this hub's orchestrator too: the
same work would run twice, on two hubs, with two sets of keys. So a task is
run where it was created, and its runs and results travel back by sync. This
module is the list of tasks that came in from elsewhere; the orchestrator's
sweep and ``TaskStore.claim`` skip them.

The mark is local on purpose: it is not part of the synced document, so the
hub that created the task does not see it, and a task created here is never
marked however often it travels.
"""
from __future__ import annotations

import logging
from typing import Set

from common import remote_state

log = logging.getLogger(__name__)

_STORE_NAME = "hubsync_task_copies"


def _store():
    from common.docstore import DocStore
    return DocStore(_STORE_NAME)


def mark(task_id: str) -> None:
    _store().put(str(task_id), {"task_id": str(task_id)})


def unmark(task_id: str) -> None:
    _store().delete(str(task_id))


@remote_state.hub_call("task:task_id")
def is_copy(task_id: object) -> bool:
    try:
        return _store().exists(str(task_id))
    except Exception:  # noqa: BLE001 - a missing store means nothing was ever synced in
        log.debug("sync: task copy lookup failed", exc_info=True)
        return False


@remote_state.hub_call("global", cache_seconds=5.0)
def task_copies() -> Set[str]:
    try:
        return set(_store().keys())
    except Exception:  # noqa: BLE001
        log.debug("sync: task copies unreadable", exc_info=True)
        return set()


__all__ = ["is_copy", "mark", "task_copies", "unmark"]
