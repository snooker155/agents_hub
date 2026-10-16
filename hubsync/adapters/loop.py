"""
Loops for the sync engine: a flow plus the terms on which repeating it stops.

Same shape as teams: the row names a workspace, loops with none are shared and
belong to no workspace, so they are not listed. The flow a loop repeats and
its evaluator agent are referenced by id, and both sync before loops. The
workspace is set on apply, ``updated_at`` is stamped by ``save_loop``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from hubsync.adapters.base import Adapter, AdapterError, register


class LoopAdapter(Adapter):
    kind = "loop"
    order = 34
    label = "Loops"

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        from loops import store
        return sorted(x.loop_id for x in store.list_loops(workspace) if x.workspace == workspace)

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        from loops import store
        loop = store.get_loop(record_id)
        if loop is None:
            return None
        doc = loop.to_dict()
        doc.pop("workspace", None)
        doc.pop("loop_id", None)
        return doc

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        from loops import store
        from loops.models import Loop
        prev = store.get_loop(record_id)
        if prev is not None and prev.workspace != workspace:
            raise AdapterError(f"loop {record_id} lives in another workspace here")
        try:
            loop = Loop.from_dict({**doc, "loop_id": record_id, "workspace": workspace})
        except (TypeError, ValueError, KeyError) as exc:
            raise AdapterError(f"unreadable loop: {exc}") from exc
        store.save_loop(loop)
        return record_id

    def delete(self, workspace: str, record_id: str) -> None:
        from loops import store
        store.delete_loop(record_id)


register(LoopAdapter())
