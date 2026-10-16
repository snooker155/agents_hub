"""
Custom playground worlds for the sync engine.

Only worlds a person authored are rows; the shipped ones are code and exist on
every hub. As with scenarios, a world with no workspace is shared and belongs
to none, so only worlds naming this workspace are listed. The document is the
whole world spec (it is one JSON document in the store already) minus its id
and workspace.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from hubsync.adapters.base import Adapter, AdapterError, register


class WorldAdapter(Adapter):
    kind = "world"
    order = 35
    label = "Worlds"

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        from playground import store
        return sorted(w.world_id for w in store.list_worlds(workspace) if w.workspace == workspace)

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        from playground import store
        world = store.get_world(record_id)
        if world is None:
            return None
        doc = world.to_dict()
        doc.pop("workspace", None)
        doc.pop("world_id", None)
        return doc

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        from playground import store
        from playground.worlds import WorldSpec
        prev = store.get_world(record_id)
        if prev is not None and prev.workspace != workspace:
            raise AdapterError(f"world {record_id} lives in another workspace here")
        try:
            world = WorldSpec.from_dict({**doc, "world_id": record_id, "workspace": workspace})
        except (TypeError, ValueError, KeyError) as exc:
            raise AdapterError(f"unreadable world: {exc}") from exc
        store.save_world(world)
        return record_id

    def delete(self, workspace: str, record_id: str) -> None:
        from playground import store
        store.delete_world(record_id)


register(WorldAdapter())
