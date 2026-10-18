"""Lookup kinds: runtime (see chat/lookup_kinds/__init__.py).

What is running, as the person sees it on the Instances page:

- ``instance``: a copy of an agent, resident or not (``/instances``,
  ``/nodes``: nodes are gone, the alias stays for what people still call it).

A card never carries an instance's messages, inbox or logs: see the module
docstring of chat/lookup.py, "Metadata, not content".
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

from chat import lookup
from chat.actions import HubAction, register_action
from chat.lookup import LookupError_, LookupKind


# ── instance (/instances, /nodes) ────────────────────────────────────────────

def _list_instances(ctx: Any, query: str, limit: int) -> List[Dict[str, Any]]:
    from instances import store as instance_store
    rows: List[Dict[str, Any]] = []
    for ws in ctx.workspaces:
        page = instance_store.list_instances(workspace=ws, limit=limit + 10)
        for inst in page.get("items", []):
            if not lookup.matches(query, inst.get("label"), inst.get("agent_id"), inst.get("instance_id")):
                continue
            at = inst.get("last_activity_at") or inst.get("started_at") or inst.get("created_at")
            rows.append(lookup.row(
                inst.get("instance_id"), inst.get("label") or inst.get("agent_id") or "instance",
                " · ".join(x for x in [inst.get("state"), inst.get("agent_id"), lookup.short_time(at)] if x),
                f"/instances/{quote(str(inst.get('instance_id')))}",
                workspace=ws if ctx.workspace is None else None, at=at,
            ))
    rows.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
    return rows[:limit]


def _instance_card(ctx: Any, instance_id: str) -> Optional[Dict[str, Any]]:
    from instances import carrier
    # One sync, for this one record only: a fresh read of whether the
    # carrier process is actually still there (chat/lookup.py's "cheap" rule
    # is about a list of many rows, not a single one the person just asked
    # about by id).
    inst = carrier.get(instance_id)
    if inst is None or not lookup.visible(ctx, inst.get("workspace")):
        return None
    fields = {
        "instance_id": inst.get("instance_id"),
        "label": inst.get("label"),
        "status": inst.get("state"),
        "resident": carrier.is_resident(inst),
        "kind": inst.get("kind"),
        "agent": inst.get("agent_id"),
        "workspace": inst.get("workspace") or "default",
        "started": inst.get("started_at") or inst.get("created_at"),
        "last_seen": inst.get("last_activity_at"),
        "runs_count": inst.get("runs_count"),
        "error": lookup.first_line(inst.get("error")),
    }
    return {"title": inst.get("label") or inst.get("agent_id") or instance_id, "fields": fields,
            "note": "Messages, the inbox and logs are on the instance's own page, not here.",
            "url": f"/instances/{quote(instance_id)}"}


def _instance_target(ctx: Any, instance_id: str) -> Optional[Dict[str, Any]]:
    from instances import carrier, store as instance_store
    inst = instance_store.get(instance_id)
    if inst is None or not lookup.visible(ctx, inst.get("workspace")):
        return None
    return {
        "workspace": inst.get("workspace") or "default",
        "label": inst.get("label") or inst.get("agent_id") or instance_id,
        "url": f"/instances/{quote(instance_id)}",
        "resident": carrier.is_resident(inst),
    }


def _instance_stop(ctx: Any, instance_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    """Same split the route makes (dashboard/backend/routes/instances.py
    ``stop_instance``): a resident copy's process is stopped, any other
    copy's current run is stopped and the row is marked stopped."""
    from instances import carrier, registry as instance_registry, store as instance_store
    from managers import run_manager
    inst = instance_store.get(instance_id)
    if inst is None:
        raise LookupError_(f"No instance '{instance_id}'.", code="not_found")
    if carrier.is_resident(inst):
        if inst.get("carrier_status") not in carrier.LIVE_CARRIER_STATUSES:
            raise LookupError_("This instance's process is already stopped.", code="conflict")
        stopped = carrier.stop(instance_id)
        return {"stopped": stopped}
    if inst.get("state") in instance_store.TERMINAL_STATES:
        raise LookupError_("This instance is already stopped.", code="conflict")
    stopped_run = False
    run_id = inst.get("current_run_id")
    if run_id:
        try:
            stopped_run = run_manager.stop_run_by_id(str(run_id))
        except Exception:  # noqa: BLE001 - the instance is still marked stopped below
            stopped_run = False
    instance_registry.mark_stopped(instance_id, "stopped by operator")
    return {"stopped_run": stopped_run}


def _instance_restart(ctx: Any, instance_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    """Only a resident copy has a process to replace (the route's own 400),
    matching ``carrier.restart``."""
    from instances import carrier
    if not target.get("resident"):
        raise LookupError_("Only an instance started with Run has a process of its own.", code="conflict")
    try:
        instance = carrier.restart(instance_id)
    except Exception as exc:  # noqa: BLE001 - the agent gets the reason, not a stack
        raise LookupError_(f"Could not restart the instance: {exc}", code="internal")
    if instance is None:
        raise LookupError_("The process runs on another host; restart it from there.", code="conflict")
    return {"state": instance.get("state")}


lookup.register(LookupKind(
    "instance", "resident and task copies of an agent: status, agent, workspace, runs",
    _list_instances, _instance_card, ("/instances", "/nodes"), aliases=("node",),
))
register_action(HubAction(
    "instance", "stop", "stop an instance's process, or its current run",
    _instance_target, _instance_stop,
    "Stop instance {label} in {workspace}: it stops answering until started again; a run in progress fails.",
))
register_action(HubAction(
    "instance", "restart", "replace a resident instance's process with a fresh one",
    _instance_target, _instance_restart,
    "Restart instance {label} in {workspace}: a fresh process replaces it; its conversations and runs stay.",
))
