"""
Playground scenarios for the sync engine: the cast, the world and the knobs.

The row names a workspace; scenarios with none are shared and are not listed
(no single workspace owns them). A scenario points at its world by id, and
custom worlds sync first (order 35 for worlds, 36 here); a built-in world is
code and is the same on both hubs. The workspace is set on apply and
``updated_at`` is stamped by ``save_scenario``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from hubsync.adapters.base import Adapter, AdapterError, register


class ScenarioAdapter(Adapter):
    kind = "scenario"
    order = 36
    label = "Scenarios"

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        from playground import store
        return sorted(s.scenario_id for s in store.list_scenarios(workspace)
                      if s.workspace == workspace)

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        from playground import store
        scenario = store.get_scenario(record_id)
        if scenario is None:
            return None
        doc = scenario.to_dict()
        doc.pop("workspace", None)
        doc.pop("scenario_id", None)
        return doc

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        from playground import store
        from playground.models import Scenario
        prev = store.get_scenario(record_id)
        if prev is not None and prev.workspace != workspace:
            raise AdapterError(f"scenario {record_id} lives in another workspace here")
        try:
            scenario = Scenario.from_dict({**doc, "scenario_id": record_id, "workspace": workspace})
        except (TypeError, ValueError, KeyError) as exc:
            raise AdapterError(f"unreadable scenario: {exc}") from exc
        store.save_scenario(scenario)
        return record_id

    def delete(self, workspace: str, record_id: str) -> None:
        from playground import store
        store.delete_scenario(record_id)


register(ScenarioAdapter())
