"""
Entity sink — a context-var channel for reporting service entities a run touched.

An agent turn rarely leaves its result only in the reply text: it creates tasks,
edits views, saves flows, schedules jobs, writes files. Each of those has a page
in the dashboard, but the chat bubble has nothing pointing at it, so the user has
to go hunting. The chat pipelines install an :class:`EntitySink` on a ContextVar
for the duration of a run; the tools call :func:`record_entity` if (and only if)
a sink is present, and the pipeline turns the collected records into links
(see ``common.entity_links``).

Outside the chat pipeline (CLI, worker subprocesses, tests) the ContextVar is
empty and the tools no-op — the same shape as ``common.artifact_sink``. This
module generalises the earlier view-only sink: a view is just one entity kind.
"""
from __future__ import annotations

import contextvars
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Actions, weakest first. A run that reads a task and then updates it should
# report "updated": a mutation always outranks a plain read, whichever came
# first. Among mutations the first one wins, so a view created and then edited
# in the same run still reads as "created".
_READ_ACTIONS = frozenset({"viewed"})

EntityKey = Tuple[str, str]


def _key(kind: str, entity_id: str) -> EntityKey:
    return (str(kind or "").strip(), str(entity_id or "").strip())


class EntitySink:
    """Ordered record of the entities one run created, changed or read.

    Keyed by ``(kind, id)``, so a task updated five times is reported once.
    ``ignore`` holds keys that must never be reported — the Visualization Studio
    binds a conversation to one view the user is already looking at, and
    re-linking it every turn is noise.
    """

    def __init__(self, ignore: Optional[Iterable[Any]] = None) -> None:
        self._records: Dict[EntityKey, Dict[str, Any]] = {}
        self._ignore: set = set()
        for item in ignore or ():
            if not item:
                continue
            if isinstance(item, (tuple, list)) and len(item) == 2:
                self._ignore.add(_key(item[0], item[1]))
            else:
                # A bare id ignores that id under any kind — the Studio binding
                # predates kinds and passes a view id on its own.
                self._ignore.add(("*", str(item).strip()))

    def _ignored(self, key: EntityKey) -> bool:
        return key in self._ignore or ("*", key[1]) in self._ignore

    def record(
        self,
        kind: str,
        entity_id: str,
        action: str = "updated",
        label: str = "",
        **meta: Any,
    ) -> None:
        """Note that this run touched ``kind``/``entity_id``.

        ``label`` is the caller's cheap display name (a tool that just created a
        task already holds its title); ``common.entity_links`` falls back to a
        store lookup when it is absent. ``meta`` carries kind-specific link data,
        e.g. ``workspace`` for a file.
        """
        key = _key(kind, entity_id)
        if not key[0] or not key[1] or self._ignored(key):
            return
        action = str(action or "updated").strip() or "updated"
        existing = self._records.get(key)
        if existing is None:
            self._records[key] = {
                "kind": key[0],
                "id": key[1],
                "action": action,
                "label": str(label or "").strip(),
                "meta": {k: v for k, v in meta.items() if v is not None},
            }
            return
        # Keep the first mutation, but let one upgrade an earlier read.
        if existing["action"] in _READ_ACTIONS and action not in _READ_ACTIONS:
            existing["action"] = action
        if label and not existing["label"]:
            existing["label"] = str(label).strip()
        for k, v in meta.items():
            if v is not None:
                existing["meta"].setdefault(k, v)

    def records(self) -> List[Dict[str, Any]]:
        """``[{kind, id, action, label, meta}, …]`` in first-touch order."""
        return [dict(rec, meta=dict(rec["meta"])) for rec in self._records.values()]

    def __bool__(self) -> bool:
        return bool(self._records)

    def __len__(self) -> int:
        return len(self._records)


_entity_sink: contextvars.ContextVar[Optional[EntitySink]] = contextvars.ContextVar(
    "entity_sink", default=None
)


def set_sink(sink: Optional[EntitySink]):
    """Install a sink for the current context. Returns the reset token."""
    return _entity_sink.set(sink)


def reset_sink(token) -> None:
    """Restore the previous sink using a token from :func:`set_sink`."""
    try:
        _entity_sink.reset(token)
    except Exception:
        pass


def record_entity(kind: str, entity_id: str, action: str = "updated", label: str = "", **meta: Any) -> None:
    """Report a touched entity to the active sink, if any.

    Never raises: reporting is best-effort and must not break a tool call.
    """
    sink = _entity_sink.get()
    if sink is None:
        return
    try:
        sink.record(kind, entity_id, action, label, **meta)
    except Exception:
        pass


__all__ = ["EntitySink", "set_sink", "reset_sink", "record_entity"]
