"""
Flows for the sync engine: the graph a person drew, with its layout.

A flow is not stored per workspace. Who sees it follows the Flows page
(dashboard/backend/routes/flows.py): a workspace with an ``allowed_flows``
list sees exactly that list; one without sees the global flows (``workspace``
empty) and the flows it owns. So ``list_ids`` reads the list when there is
one and the owned flows otherwise.

A synced flow lands owned by the workspace it was synced into, never global:
a global flow shows in every workspace without a list, so a flow synced into
one workspace would surface in all the others. A workspace with a list also
gets it added there, since the list is all that workspace sees. And a
deletion from the other side never removes a flow another workspace can see:
a global one (every workspace without a list sees it, and every one made
later), one another workspace owns, one another lists. Those are only taken
out of this workspace; a flow is deleted when it was this workspace's alone.

The document is the stored flow as it is, positions and sizes included: where
a node sits is something the person arranged, so it travels. Left out is what
belongs to this hub and not to the flow: the owner, the review and sharing
state (each hub has its own policy), the run marker and last task id, the
workspace, and above all ``webhook_secret``, a secret value that must never
leave the hub (the receiving side keeps its own).

Flow entities the person defined (``flow_entities``, source ``user``) ride
inside the flow under ``entities`` instead of being a record of their own:
nothing else refers to them, and a flow whose nodes name an entity the other
hub lacks would be refused at run time. Agents are a separate kind (order 10)
and land before flows.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from hubsync.adapters.base import Adapter, AdapterError, register

# Local to this hub, never part of the content.
_LOCAL_KEYS = (
    "id", "owner_user", "review_status", "review_note", "reviewed_by",
    "reviewed_at", "shared", "task_id", "running", "workspace", "webhook_secret",
)
# Kept from the flow already here when the incoming document replaces it.
_KEEP_LOCAL = tuple(k for k in _LOCAL_KEYS if k != "id")


def _allowed(workspace: str) -> Optional[List[str]]:
    from workspace.storage import get_workspace_metadata
    allowed = get_workspace_metadata(workspace).get("allowed_flows")
    return [str(x) for x in allowed] if allowed is not None else None


def _stored(flow_id: str) -> Optional[Dict[str, Any]]:
    from flow import store as flow_store
    try:
        return flow_store.get_flow(flow_id)
    except flow_store.FlowParseError:
        # A node names an entity that is gone. The flow still exists and must
        # not read as deleted (a missing record is a deletion to the engine).
        return flow_store._FLOWS.get(flow_id)


def _user_entities(flow: Dict[str, Any]) -> List[Dict[str, Any]]:
    from flow import registry
    from flow import store as flow_store
    out: Dict[str, Dict[str, Any]] = {}
    for node in flow.get("nodes") or []:
        ref = flow_store._entity_ref(node)
        if not ref or ref in out:
            continue
        spec = registry.get_entity(ref)
        if spec is not None and spec.source == "user":
            d = spec.to_dict()
            d.pop("source", None)
            out[spec.id] = d
    return [out[k] for k in sorted(out)]


def _listed_elsewhere(flow_id: str, workspace: str) -> List[str]:
    """Workspaces other than ``workspace`` whose ``allowed_flows`` names the
    flow: they see it whoever owns it."""
    from workspace.storage import get_workspace_metadata, list_workspace_folders
    held = []
    for folder in list_workspace_folders():
        name = folder.name
        if name == workspace:
            continue
        allowed = (get_workspace_metadata(name) or {}).get("allowed_flows")
        if allowed and flow_id in [str(x) for x in allowed]:
            held.append(name)
    return sorted(held)


def _visible_here(flow: Dict[str, Any], flow_id: str, workspace: str, allowed: Optional[List[str]]) -> bool:
    """Whether ``workspace`` already sees ``flow`` as its own: listed there,
    or owned by it. A global flow seen only by default is not: taking it
    over from a sync would rewrite what every other workspace sees."""
    if allowed is not None:
        return flow_id in allowed
    return flow.get("workspace") == workspace


class FlowAdapter(Adapter):
    kind = "flow"
    order = 30
    label = "Flows"

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        from flow import store as flow_store
        allowed = _allowed(workspace)
        if allowed is None:
            ids = [str(f.get("id")) for f in flow_store.list_flows()
                   if f.get("workspace") == workspace]
        else:
            ids = list(allowed)
        out: List[str] = []
        for fid in sorted(set(ids)):
            flow = _stored(fid)
            if flow is None:
                continue
            holder = flow.get("owner_user")
            if holder and holder != owner:
                continue
            out.append(fid)
        return out

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        flow = _stored(record_id)
        if flow is None:
            return None
        doc = {k: v for k, v in flow.items() if k not in _LOCAL_KEYS}
        doc["entities"] = _user_entities(flow)
        return doc

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        from flow import registry
        from flow import store as flow_store
        if not isinstance(doc.get("nodes"), list) or not isinstance(doc.get("edges"), list):
            raise AdapterError("a flow needs nodes and edges")
        prev = flow_store._FLOWS.get(record_id)
        allowed = _allowed(workspace)
        if prev is not None and prev.get("owner_user") not in (None, "", owner):
            raise AdapterError(f"flow {record_id} belongs to another person here")
        if prev is not None and not _visible_here(prev, record_id, workspace, allowed):
            raise AdapterError(f"flow {record_id} is another workspace's flow here, or a global one")
        incoming = {k: v for k, v in doc.items() if k != "entities"}
        for raw in doc.get("entities") or []:
            try:
                spec = registry.FlowEntitySpec(**{
                    k: raw[k] for k in ("id", "name", "category", "entrypoint", "description",
                                        "group", "inputs", "outputs", "config_schema", "icon")
                    if k in raw})
            except (TypeError, KeyError) as exc:
                raise AdapterError(f"bad flow entity: {exc}") from exc
            existing = registry.get_entity(spec.id)
            if existing is None or existing.source == "user":
                registry.add_user_entity(spec)
        flow = dict(incoming)
        flow["id"] = record_id
        for key in _KEEP_LOCAL:
            if prev is not None and key in prev:
                flow[key] = prev[key]
        flow["owner_user"] = (prev or {}).get("owner_user") or owner
        if prev is None:
            # Owned here, not global: see the module docstring.
            flow["workspace"] = workspace
        flow_store.save_flow(flow)
        if allowed is not None and record_id not in allowed:
            # A list is all such a workspace sees. One without a list sees the
            # flow as its own already, and giving it a list would hide every
            # global flow it saw until now.
            from workspace.storage import update_workspace_metadata
            update_workspace_metadata(workspace, {"allowed_flows": allowed + [record_id]})
        return record_id

    def delete(self, workspace: str, record_id: str) -> None:
        """Take the flow out of this workspace, and delete it only when no
        other workspace can see it. A global flow stays (every workspace
        without a list sees it), so does one another workspace owns or
        lists. One this workspace owned but others still list is handed to
        the first of them, or it would stay listed here (``list_ids`` falls
        back to the owner) and travel back."""
        from flow import store as flow_store
        from workspace.storage import update_workspace_metadata
        allowed = _allowed(workspace)
        if allowed is not None and record_id in allowed:
            update_workspace_metadata(workspace, {
                "allowed_flows": [x for x in allowed if x != record_id]})
        flow = flow_store._FLOWS.get(record_id)
        if flow is None or flow.get("workspace") != workspace:
            return
        others = _listed_elsewhere(record_id, workspace)
        if others:
            flow_store.save_flow({**flow, "workspace": others[0]})
            return
        flow_store.delete_flow(record_id)


register(FlowAdapter())
