"""The watcher collection: one document per watcher in ``DocStore("watchers")``."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from common.docstore import DocStore
from watchers.models import Watcher

log = logging.getLogger(__name__)


def _dump(w: Watcher) -> Dict[str, Any]:
    data = w.model_dump()
    for key, value in list(data.items()):
        if isinstance(value, datetime):
            data[key] = value.isoformat()
    return data


class WatcherStore:
    def __init__(self) -> None:
        self.docs = DocStore("watchers")

    def _parse(self, doc: Any) -> Optional[Watcher]:
        if not isinstance(doc, dict):
            return None
        try:
            return Watcher(**doc)
        except Exception:  # noqa: BLE001 - a corrupt row is skipped, not fatal for the list
            log.warning("watchers: skipping an unreadable record %r", doc.get("id"), exc_info=True)
            return None

    def list(self, workspace: Optional[str] = None) -> List[Watcher]:
        items = [w for w in (self._parse(d) for d in self.docs.values()) if w is not None]
        if workspace:
            items = [w for w in items if w.workspace == workspace]
        return sorted(items, key=lambda w: (w.workspace, w.name.lower(), w.id))

    def get(self, watcher_id: str) -> Optional[Watcher]:
        return self._parse(self.docs.get(str(watcher_id)))

    def put(self, watcher: Watcher) -> Watcher:
        watcher.touch()
        self.docs.put(watcher.id, _dump(watcher))
        return watcher

    def update(self, watcher_id: str, **fields: Any) -> Optional[Watcher]:
        """Merge *fields* into the stored record under one transaction, so a
        poll writing its state and a route writing a name never clobber each
        other's columns."""
        with self.docs.transaction():
            doc = self.docs.get(str(watcher_id))
            if not isinstance(doc, dict):
                return None
            data = dict(doc)
            data.update(fields)
            updated = Watcher(**data)
            updated.touch()
            self.docs.put(updated.id, _dump(updated))
            return updated

    def delete(self, watcher_id: str) -> bool:
        return self.docs.delete(str(watcher_id))


store = WatcherStore()

__all__ = ["WatcherStore", "store"]
