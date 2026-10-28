"""
User-defined Agent Flows (visual pipelines) API routes.
"""
from __future__ import annotations

import ast
import asyncio
import os
import re
import signal
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union, cast
from urllib.parse import quote
from uuid import uuid4
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from tasks import service as tasks_service
from managers import run_manager
from flow import launcher as flow_launcher
from flow import store as flow_store
from flow import templates as flow_templates
from flow.engine import ON_ERROR_POLICIES
from models import FlowListItem, FlowDetail, FlowPage
from flow.estimate import estimate_flow_cost
from common.marketplace_names import agent_name_taken, flow_name_taken, name_key
from common.workspace_scope import same_workspace
from common import record_access
from workspace import (
    create_workspace_folder,
    get_workspace_metadata,
    update_workspace_metadata,
)
import logging

log = logging.getLogger(__name__)


router = APIRouter(prefix="/api/flows", tags=["flows"])


# ── helpers ───────────────────────────────────────────────────────────────────
# Flows are persisted in the database via flow_store (the `flows` document store).
# These thin wrappers keep the existing list-oriented CRUD logic unchanged.

def _load() -> List[Dict]:
    # Temporary flows too (flow_store.is_ephemeral): a run page edits, keeps
    # and runs one by id, and _save below must not read them as vanished.
    return flow_store.list_flows(include_ephemeral=True)


def _save(flows: List[Dict]) -> None:
    """Persist a full flow list: upsert each flow, delete any that vanished."""
    existing_ids = {f["id"] for f in flow_store.list_flows(include_ephemeral=True) if f.get("id")}
    new_ids = {f["id"] for f in flows if f.get("id")}
    for flow in flows:
        if flow.get("id"):
            flow_store.save_flow(flow)
    for stale in existing_ids - new_ids:
        flow_store.delete_flow(stale)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _flow_workspace(requested: Optional[str]) -> str:
    """The workspace a new flow is bound to: the one it is created in, the
    default workspace when the request names none."""
    return (requested or "").strip() or "default"


def _authorize_flow_in_workspace(workspace: str, flow_id: str) -> None:
    """Add ``flow_id`` to the workspace's ``allowed_flows`` allowlist (idempotent).

    Only a workspace that keeps such a list gets the id: one without lists its
    own flows anyway, and starting a list there would hide every other flow.
    """
    metadata = get_workspace_metadata(workspace)
    if metadata.get("allowed_flows") is None:
        return
    allowed = list(metadata.get("allowed_flows") or [])
    if flow_id not in allowed:
        allowed.append(flow_id)
        update_workspace_metadata(workspace, {"allowed_flows": allowed})


# ── request models ────────────────────────────────────────────────────────────

class FlowCreate(BaseModel):
    name: str
    description: str = ""
    # The workspace the flow is created in (default when none).
    workspace: Optional[str] = None


class FlowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    nodes: Optional[List[Dict[str, Any]]] = None
    edges: Optional[List[Dict[str, Any]]] = None
    workspace: Optional[str] = None
    task_id: Optional[str] = None
    # Flow-level meta (logic, persisted to YAML). entry_point names the start
    # node; mutability=false makes existing state keys write-once; state holds
    # the initial declared state-key defaults.
    entry_point: Optional[str] = None
    mutability: Optional[bool] = None
    recordability: Optional[str] = None
    state: Optional[Dict[str, Any]] = None
    # Execution policy (see flow.engine): how many nodes may run at once, and
    # what a failed node does to the rest of the graph. Both have engine
    # defaults, so a flow that never sets them behaves as it always did.
    max_parallel: Optional[int] = None
    on_error: Optional[str] = None
    # Write-only: the shared secret POST /{flow_id}/trigger requires a signature
    # against. Sending "" clears it; the flow is never read back with it.
    webhook_secret: Optional[str] = None
    # Example requests shown in the flow's empty chat: {lang: [text, ...]},
    # or a plain list for every language (flow.templates.normalize_examples).
    examples: Optional[Union[List[str], Dict[str, List[str]]]] = None
    # False keeps a temporary flow an agent built for one run (run_new_flow_tool):
    # it joins the flow list and is no longer swept after a week.
    ephemeral: Optional[bool] = None


class FlowImport(BaseModel):
    yaml: str
    name: Optional[str] = None
    # The workspace the flow is imported into (default when none).
    workspace: Optional[str] = None


class FlowSharingUpdate(BaseModel):
    shared: bool


class FlowRun(BaseModel):
    workspace: Optional[str] = None
    description: Optional[str] = None
    task_id: Optional[str] = None


class FlowRerun(BaseModel):
    """Body of POST /{flow_id}/runs/{run_group}/rerun: the node to start
    from, and ``single`` (that node only) or ``rest`` (it and all after it)."""
    node_id: str
    mode: str = "rest"


class FlowResume(BaseModel):
    """Body of POST /runs/{flow_run_id}/resume.

    ``answer`` is the person's reply when the run is parked on a
    ``human_interrupt`` node; a run that merely died has nothing to answer.
    """
    answer: Optional[str] = None


class FlowTrigger(BaseModel):
    """External trigger payload for POST /{flow_id}/trigger.

    ``seed`` is a JSON object merged into the flow's initial state. ``max_concurrent``
    (0 = unlimited) caps simultaneous instances so a webhook/cron can't stack up
    runaway runs.
    """
    workspace: Optional[str] = None
    description: Optional[str] = None
    seed: Optional[Dict[str, Any]] = None
    max_concurrent: int = 1


def _public_flow(flow: Dict[str, Any]) -> Dict[str, Any]:
    """A flow record safe to hand to the dashboard.

    The webhook secret is write-only: the trigger route compares against it, and
    the page only ever needs to know whether one is set.
    """
    if not isinstance(flow, dict):
        return flow
    public = {k: v for k, v in flow.items() if k != "webhook_secret"}
    public["webhook_secret_configured"] = bool(flow.get("webhook_secret"))
    return public


# ── CRUD ──────────────────────────────────────────────────────────────────────

def _paginate(items: list, limit: Optional[int], offset: Optional[int]) -> list:
    if limit is None and offset is None:
        return items
    start = offset or 0
    return items[start: start + limit] if limit is not None else items[start:]


@router.get("", response_model=Union[List[FlowListItem], FlowPage])
async def list_flows(workspace: Optional[str] = None, limit: Optional[int] = None,
                     offset: Optional[int] = None, request: Request = cast(Request, None)):
    """The flow list. With no ``limit``/``offset`` this is the full list, exactly
    as before; with either, it is one page: ``{items, total, limit, offset}``.

    Flows are file-backed (one YAML+JSON pair per flow via ``flow_store``), not
    a queryable store. Visibility filtering happens on the cheapest field that
    can answer it before anything is parsed off disk, and ``_public_flow`` (the
    only per-item work here) runs on the resulting page, not the whole catalog:

    - An explicit ``allowed_flows`` allowlist *is* the filter, metadata already
      in hand, so only the ids that land on the requested page get read.
    - Otherwise, ownership (the flows this workspace owns, a flow that names
      no workspace being the default one's) is a property of each flow's own record, so every flow still has to be
      parsed once to know it: flow_store keeps no index for that.
    - With no workspace at all, ``flow_store`` pages the file list itself and
      parses only those files.
    """
    allowed = get_workspace_metadata(workspace).get("allowed_flows") if workspace else None

    if allowed is not None:
        flow_ids = sorted(str(fid) for fid in allowed)
        total = len(flow_ids)
        page_ids = _paginate(flow_ids, limit, offset)
        flows = [f for f in (flow_store.get_flow(fid) for fid in page_ids)
                 if f and not flow_store.is_ephemeral(f)]
    elif workspace:
        # Keeps the flow picker in sync with the run_flow authorization check.
        visible = [f for f in flow_store.list_flows()
                   if same_workspace(f.get("workspace"), workspace)]
        total = len(visible)
        flows = _paginate(visible, limit, offset)
    elif record_access.visible_names(request) is not None:
        # A member asking for no workspace in particular sees the flows of
        # the workspaces they belong to (and shared ones), not every flow.
        names = record_access.visible_names(request)
        mine = [f for f in flow_store.list_flows()
                if f.get("shared") or record_access.visible(names, f.get("workspace"))]
        total = len(mine)
        flows = _paginate(mine, limit, offset)
    else:
        total = flow_store.count_flows()
        flows = flow_store.list_flows(limit=limit, offset=offset)

    page = [_public_flow(f) for f in flows]
    if limit is None and offset is None:
        return page
    return {"items": page, "total": total, "limit": limit, "offset": offset}


@router.post("")
async def create_flow(data: FlowCreate, request: Request = cast(Request, None)):
    # A new flow belongs to the workspace it is created in until it is
    # published; then other workspaces take a copy from the marketplace.
    workspace = _flow_workspace(data.workspace)
    record_access.require_in_workspace(request, workspace)
    flows = _load()
    flow = {
        "id": str(uuid4()),
        "name": data.name,
        "description": data.description,
        "nodes": [],
        "edges": [],
        "workspace": workspace,
        "task_id": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    flows.append(flow)
    _save(flows)
    _authorize_flow_in_workspace(workspace, flow["id"])
    return flow


@router.get("/{flow_id}", response_model=FlowDetail)
async def get_flow(flow_id: str):
    try:
        flow = flow_store.get_flow(flow_id)
    except flow_store.FlowParseError as e:
        # A node references an entity that no longer exists in the registry.
        raise HTTPException(status_code=422, detail=str(e))
    if flow is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    return _public_flow(flow)


@router.put("/{flow_id}")
async def update_flow(flow_id: str, data: FlowUpdate, request: Request = cast(Request, None)):
    if data.workspace:
        # Moving a flow needs the same right in the workspace it moves to as
        # in the one it leaves (checked by the middleware).
        record_access.require_in_workspace(request, data.workspace)
    flows = _load()
    for i, f in enumerate(flows):
        if f["id"] == flow_id:
            patch = {k: v for k, v in data.model_dump().items() if v is not None}
            if "on_error" in patch and patch["on_error"] not in ON_ERROR_POLICIES:
                raise HTTPException(
                    status_code=400,
                    detail=f"on_error must be one of {', '.join(ON_ERROR_POLICIES)}",
                )
            if "max_parallel" in patch and int(patch["max_parallel"]) < 1:
                raise HTTPException(status_code=400, detail="max_parallel must be at least 1")
            if "webhook_secret" in patch:
                # An empty string is how the UI clears the secret: drop the key
                # rather than storing "", which the trigger would read as falsy
                # anyway but which would keep reporting the flow as configured.
                secret = str(patch.pop("webhook_secret") or "").strip()
                if secret:
                    patch["webhook_secret"] = secret
                else:
                    f = {k: v for k, v in f.items() if k != "webhook_secret"}
            if "examples" in patch:
                patch["examples"] = flow_templates.normalize_examples(patch["examples"])
            if f.get("shared") and "name" in patch:
                taken = flow_name_taken(patch["name"], exclude=[flow_id])
                if taken:
                    raise HTTPException(status_code=409, detail=taken)
            patch["updated_at"] = _now()
            flows[i] = {**f, **patch}
            _save(flows)
            # Binding a flow to a workspace authorizes it there.
            ws_name = patch.get("workspace")
            if ws_name:
                _authorize_flow_in_workspace(ws_name, flow_id)
            return _public_flow(flows[i])
    raise HTTPException(status_code=404, detail="Flow not found")


@router.get("/{flow_id}/examples")
async def get_flow_examples(flow_id: str, lang: str = "en"):
    """Example requests for the flow's empty chat, in UI language *lang*:
    the flow's own ``examples`` (a template's, or set in the editor), else
    those of the agent its first step runs, since that step is the one the
    message goes to. ``source`` says which; empty when neither has any."""
    flow = flow_store.get_flow(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    own = flow_templates.examples_for(flow.get("examples"), lang)
    if own:
        return {"flow_id": flow_id, "source": "flow", "examples": own[:6]}
    entry = _entry_agent(flow)
    if entry:
        from routes.agents.definition import get_agent_examples
        try:
            found = (await get_agent_examples(entry, lang)).get("examples") or []
        except HTTPException:
            found = []
        if found:
            return {"flow_id": flow_id, "source": "agent", "agent_id": entry, "examples": found[:6]}
    return {"flow_id": flow_id, "source": None, "examples": []}


def _entry_agent(flow: Dict[str, Any]) -> Optional[str]:
    """The agent of the step the person's message reaches first: the entry
    point when one is set, else the first agent step with no incoming edge."""
    nodes = [n for n in flow.get("nodes") or [] if isinstance(n, dict)]
    targets = {e.get("target") for e in flow.get("edges") or [] if isinstance(e, dict)}

    def agent_of(node: Dict[str, Any]) -> Optional[str]:
        data = node.get("data")
        if not isinstance(data, dict):
            data = {}
        return data.get("agent_id") or node.get("agent_id") or None

    entry = flow.get("entry_point")
    ordered = [n for n in nodes if n.get("id") == entry] + [n for n in nodes if n.get("id") not in targets]
    for node in ordered:
        if agent_of(node):
            return agent_of(node)
    return None


@router.delete("/{flow_id}")
async def delete_flow(flow_id: str):
    flows = _load()
    new_flows = [f for f in flows if f["id"] != flow_id]
    if len(new_flows) == len(flows):
        raise HTTPException(status_code=404, detail="Flow not found")
    _save(new_flows)
    return {"message": "Flow deleted"}


# ── marketplace publishing ────────────────────────────────────────────────────

@router.post("/{flow_id}/sharing")
async def update_flow_sharing(flow_id: str, data: FlowSharingUpdate):
    """Publish or unpublish a flow on the marketplace.

    Publishing also publishes every non-system agent the flow uses (sets
    ``AgentSpec.shared``), so the whole pipeline can be added to any workspace.
    Unpublishing only hides the flow — its agents stay published, since they
    may be used by other published flows or have been published explicitly.
    """
    import dataclasses

    from agents import registry
    from workspace import is_system_agent

    flow = flow_store.get_flow(flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")

    published_agents: List[str] = []
    if data.shared:
        taken = flow_name_taken(flow.get("name"), exclude=[flow_id])
        if taken:
            raise HTTPException(status_code=409, detail=taken)
        agent_ids = flow_store.flow_agent_ids(flow)
        blockers: List[str] = []
        # The agents this publishing publishes take their names on the
        # marketplace too, so neither one already there nor each other's.
        newly_named: Dict[str, str] = {}
        for agent_id in agent_ids:
            spec = registry.get_agent(agent_id)
            if not spec:
                blockers.append(f"agent '{agent_id}' is not registered")
            elif spec.default_workspace_only:
                blockers.append(
                    f"agent '{agent_id}' is restricted to the default workspace"
                )
            elif not spec.shared and not is_system_agent(agent_id):
                taken = agent_name_taken(spec.name, exclude=[agent_id])
                twin = newly_named.get(name_key(spec.name))
                if taken:
                    blockers.append(f"agent '{agent_id}': {taken}")
                elif twin:
                    blockers.append(
                        f"agents '{twin}' and '{agent_id}' are both named '{spec.name}'"
                    )
                newly_named.setdefault(name_key(spec.name), agent_id)
        if blockers:
            raise HTTPException(
                status_code=400,
                detail="Cannot publish flow to the marketplace: " + "; ".join(blockers),
            )
        for agent_id in agent_ids:
            spec = registry.get_agent(agent_id)
            if spec is None or is_system_agent(agent_id) or spec.shared:
                continue
            registry.add_agent(dataclasses.replace(spec, shared=True))
            published_agents.append(agent_id)

    flow["shared"] = bool(data.shared)
    flow["updated_at"] = _now()
    flow_store.save_flow(flow)
    return {"id": flow_id, "shared": flow["shared"], "published_agents": published_agents}


# ── import / export ─────────────────────────────────────────────────────────────

@router.get("/{flow_id}/export")
async def export_flow(flow_id: str):
    """Download a flow's logic definition as a YAML file."""
    try:
        yaml_text = flow_store.export_flow_yaml(flow_id)
    except flow_store.FlowParseError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if yaml_text is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    flow = flow_store.get_flow(flow_id)
    # Slugify the flow name for the download filename.
    name = (flow.get("name") if flow else None) or flow_id
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in name).strip("-") or "flow"
    # Headers are latin-1, so a non-ASCII name goes in filename* only.
    ascii_slug = "".join(c if c.isascii() else "_" for c in slug)
    disposition = f"attachment; filename=\"{ascii_slug}.yaml\"; filename*=UTF-8''{quote(slug)}.yaml"
    return Response(
        content=yaml_text,
        media_type="application/x-yaml",
        headers={"Content-Disposition": disposition},
    )


@router.post("/import")
async def import_flow(data: FlowImport, request: Request = cast(Request, None)):
    """Validate an uploaded flow YAML and, if valid, store it as a new flow."""
    record_access.require_in_workspace(request, data.workspace)
    if not (data.yaml or "").strip():
        raise HTTPException(status_code=400, detail="No YAML content provided")

    flow_id = str(uuid4())
    try:
        flow = flow_store.import_flow_yaml(data.yaml, flow_id)
    except flow_store.FlowParseError as e:
        # Structurally valid YAML, but a node references an unknown entity.
        raise HTTPException(status_code=422, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # A fresh import is a new flow: override name when provided, bind it to
    # the workspace it is imported into and reset the timestamps.
    if data.name and data.name.strip():
        flow["name"] = data.name.strip()
    flow.setdefault("name", "Imported flow")
    workspace = _flow_workspace(data.workspace)
    # An agent that workspace may not run is refused here, not at the first run.
    from flow.validate import workspace_agent_errors
    refused = workspace_agent_errors(flow.get("nodes", []), workspace)
    if refused:
        raise HTTPException(status_code=422, detail="; ".join(refused))
    flow["workspace"] = workspace
    flow["shared"] = False
    flow["created_at"] = _now()
    flow["updated_at"] = _now()
    flow_store.save_flow(flow)
    _authorize_flow_in_workspace(workspace, flow_id)
    return flow


# ── n8n ───────────────────────────────────────────────────────────────────────
# The n8n connector (connectors/n8n) in effect for the workspace: list its
# workflows, and turn one (from the instance, or a pasted export) into a hub
# flow, either a single node that runs it in n8n or its steps as hub nodes
# (flow/n8n_import.py).

class N8nImport(BaseModel):
    #: "run" keeps the workflow in n8n behind one run_n8n node; "convert"
    #: makes its steps hub nodes.
    mode: str = "convert"
    #: A workflow on the connected instance...
    workflow_id: Optional[str] = None
    #: ...or an exported workflow, as JSON text or an object.
    workflow: Optional[Union[str, Dict[str, Any]]] = None
    name: Optional[str] = None
    workspace: Optional[str] = None


def _n8n_client(workspace: Optional[str]):
    from connectors.n8n import n8n_client
    from connectors.n8n.client import N8nError

    try:
        return n8n_client(workspace or None)
    except N8nError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/n8n/workflows")
async def list_n8n_workflows(workspace: Optional[str] = None):
    """The connected n8n instance's workflows, with the trigger a run would use."""
    from connectors.n8n.client import N8nError

    client = _n8n_client(workspace)
    try:
        workflows = await asyncio.to_thread(client.list_workflows)
    except N8nError as e:
        raise HTTPException(status_code=502, detail=str(e))
    # The list call carries nodes on current n8n versions; older ones leave
    # them out, and then the trigger is unknown until the workflow is read.
    from connectors.n8n.client import find_trigger

    return [{
        "id": str(w.get("id") or ""),
        "name": w.get("name") or "",
        "active": bool(w.get("active")),
        "updated_at": w.get("updatedAt"),
        "tags": [t.get("name") for t in (w.get("tags") or []) if isinstance(t, dict)],
        "nodes": len(w.get("nodes") or []),
        "trigger": find_trigger(w) if w.get("nodes") else None,
    } for w in workflows]


@router.post("/import/n8n")
async def import_n8n_flow(data: N8nImport, request: Request = cast(Request, None)):
    """Create a hub flow from an n8n workflow. Returns ``{flow, report}``; the
    report says what each n8n step became and what did not carry over."""
    record_access.require_in_workspace(request, data.workspace)
    from connectors.n8n.client import N8nError
    from flow import n8n_import

    mode = (data.mode or "convert").strip().lower()
    if mode not in ("convert", "run"):
        raise HTTPException(status_code=400, detail="mode must be convert or run")
    workflow_id = (data.workflow_id or "").strip()
    try:
        if workflow_id:
            client = _n8n_client(data.workspace)
            workflow = await asyncio.to_thread(client.get_workflow, workflow_id)
        elif data.workflow:
            workflow = n8n_import.parse_workflow(data.workflow)
        else:
            raise HTTPException(status_code=400, detail="Send a workflow_id or the workflow JSON")
        if mode == "run":
            fields, report = n8n_import.run_node_flow(workflow, workflow_id=workflow_id)
        else:
            fields, report = n8n_import.convert(workflow)
    except N8nError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except n8n_import.N8nImportError as e:
        raise HTTPException(status_code=400, detail=str(e))

    workspace = (data.workspace or "").strip() or None
    flow = {
        "id": str(uuid4()),
        **fields,
        "workspace": workspace,
        "task_id": None,
        "shared": False,
        "source": {"kind": "n8n", "mode": mode, "workflow_id": workflow_id or str(workflow.get("id") or "")},
        "created_at": _now(),
        "updated_at": _now(),
    }
    if data.name and data.name.strip():
        flow["name"] = data.name.strip()
    flow_store.save_flow(flow)
    if workspace:
        _authorize_flow_in_workspace(workspace, flow["id"])
    return {"flow": _public_flow(flow), "report": report}


# ── execution ─────────────────────────────────────────────────────────────────

@router.post("/{flow_id}/run")
async def run_flow(flow_id: str, req: FlowRun):
    flow = next((f for f in _load() if f["id"] == flow_id), None)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")

    if req.task_id:
        t = tasks_service.get_task(cast(Any, req.task_id))  # the store takes a str id as well
        if not t:
            raise HTTPException(status_code=404, detail=f"Task '{req.task_id}' not found")
        # An existing task runs the flow in its own workspace.
        ws_name = getattr(t, "workspace", None) or req.workspace or flow.get("workspace")
        if not ws_name:
            ws_name = create_workspace_folder().name
    else:
        # No task named: the run is a run, not a task.
        t = None
        ws_name = req.workspace or flow.get("workspace")
        if not ws_name:
            ws_name = create_workspace_folder().name

    # Authorization: a workspace may restrict which flows can run in it.
    # ``allowed_flows`` absent (None) means unrestricted, mirroring allowed_agents.
    allowed = get_workspace_metadata(ws_name).get("allowed_flows")
    if allowed is not None and flow_id not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"Flow '{flow['name']}' is not authorized for workspace '{ws_name}'",
        )

    params = {
        "workspace": ws_name,
        "description": req.description or flow.get("description", ""),
        "flow_id": flow_id,
    }

    try:
        run_id, session_id = flow_launcher.start_flow_run(str(t.id) if t else None, flow_id, params)
        if t is not None:
            tasks_service.assign_agent(t.id, flow.get("name") or flow_id, params, run_id=run_id)
        return {
            "status": "started",
            "task_id": str(t.id) if t else None,
            "run_id": run_id,
            "session_id": session_id,
            "workspace": ws_name,
            # What this run is expected to cost, from the same catalog prices
            # the cost page uses. Returned with the launch so the figure is in
            # front of whoever pressed Run, not only on the estimate endpoint.
            "estimated_cost": _safe_estimate(flow),
        }
    except Exception as e:  # noqa: BLE001 - reported to the client as an HTTP error
        raise HTTPException(status_code=500, detail=str(e))


def _safe_estimate(flow: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Price a flow, or return None. An estimate must never block a run."""
    try:
        return estimate_flow_cost(flow)
    except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
        log.debug("_safe_estimate: falling back after a failure", exc_info=True)
        return None


@router.get("/{flow_id}/estimate")
async def estimate_flow(flow_id: str):
    """What one run of this flow is expected to cost, before starting it.

    One call per agent node, priced from the Models page catalog. Nodes are
    listed individually because a graph whose cost is one expensive node is a
    different decision from one that spreads it evenly.
    """
    flow = next((f for f in _load() if f["id"] == flow_id), None)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    return estimate_flow_cost(flow)


@router.post("/runs/{flow_run_id}/resume")
async def resume_flow_run(flow_run_id: str, req: Optional[FlowResume] = None):
    """Continue a flow run from its checkpoint.

    Two runs need this: one parked on a ``human_interrupt`` node, which resumes
    with the person's ``answer`` written into flow state, and one whose process
    died, which resumes with the nodes it had already finished replayed rather
    than re-run. Declared above ``/{flow_id}`` variants with the same shape so
    "runs" is never read as a flow id.
    """
    answer = (req.answer if req else None)
    try:
        return flow_launcher.resume_flow_run(flow_run_id, answer)
    except flow_launcher.FlowResumeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{flow_id}/trigger")
async def trigger_flow_webhook(flow_id: str, request: Request, req: Optional[FlowTrigger] = None):
    """Webhook entry point: start a flow run from an external caller with an
    optional JSON ``seed``, guarded by a per-flow concurrency cap.

    Auth: when ``AGENTS_HUB_API_TOKEN`` is configured, the global middleware
    already requires it on this ``/api`` route — no extra check here. When the
    flow's own record carries a ``webhook_secret``, the caller must also sign
    the request the same way ``notify.outbound`` signs what this hub sends out
    (see docs/notifications.md); a flow with no secret configured keeps
    today's behaviour unchanged. Returns 429 when the concurrency cap is hit,
    403 when the flow isn't allowed in the workspace, 404 for an unknown flow,
    401 for a missing/invalid signature and 409 for a replayed delivery.
    """
    flow = flow_store.get_flow(flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")

    secret = flow.get("webhook_secret")
    if secret:
        from notify.inbound import seen_delivery, verify_signature

        raw_body = await request.body()
        signature = request.headers.get("X-AgentsHub-Signature", "")
        timestamp = request.headers.get("X-AgentsHub-Timestamp", "")
        delivery_id = request.headers.get("X-AgentsHub-Delivery", "")
        if not verify_signature(secret, raw_body, signature, timestamp):
            raise HTTPException(status_code=401, detail="Invalid or missing signature")
        if delivery_id and seen_delivery(delivery_id):
            raise HTTPException(status_code=409, detail="Delivery already processed")

    req = req or FlowTrigger()
    try:
        result = flow_launcher.trigger_flow(
            flow_id,
            workspace=req.workspace,
            description=req.description,
            seed=req.seed,
            max_concurrent=req.max_concurrent,
            created_by="webhook",
        )
        return {"status": "started", **result}
    except flow_launcher.FlowNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except flow_launcher.FlowNotAuthorizedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except flow_launcher.FlowConcurrencyError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{flow_id}/runs/{run_group}/rerun")
async def rerun_flow_run(flow_id: str, run_group: str, req: FlowRerun):
    """Run a past task run of the flow again from one node, as a new run
    (flow/launcher.rerun_flow_run). A chat record runs again as a turn of
    its own conversation instead (chat/pipelines.py, ``flow_rerun``)."""
    flow = next((f for f in _load() if f["id"] == flow_id), None)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    try:
        return flow_launcher.rerun_flow_run(flow_id, run_group, req.node_id, req.mode)
    except flow_launcher.FlowResumeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001 - reported to the client as an HTTP error
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{flow_id}/stop")
async def stop_flow(flow_id: str, flow_run_id: Optional[str] = None):
    """Stop running instance(s) of a flow.

    A flow can run in parallel, so this stops every active flow-run record by
    default; pass ``flow_run_id`` to stop just one instance. Each instance: its
    flow-run record is marked stopped, its orchestrator process is killed, its
    in-flight node runs are stopped, and its task is stopped.
    """
    from flow.launcher import _set_flow_running
    from flow import run_store
    from datetime import datetime, timezone
    from uuid import UUID

    def _append_flow_log(entry: dict) -> None:
        # Per-run log file keyed by the entry's run_group (the flow_run_id).
        run_store.append_flow_log(flow_id, entry.get("run_group") or "", entry)

    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    active = run_store.get_active_flow_runs(flow_id)
    if flow_run_id:
        active = [fr for fr in active if fr.get("flow_run_id") == flow_run_id]

    all_runs = run_manager.load_runs()
    stopped = False

    for fr in active:
        fr_id = fr.get("flow_run_id")
        if not fr_id:
            continue

        # 1) Mark the flow run stopped — the orchestrator polls this between nodes.
        run_store.close_flow_run(fr_id, status="stopped", exit_code=1, error="Stopped by user")

        # 2) Kill the orchestrator process (immediate, mid-node).
        pid = fr.get("pid")
        if pid:
            try:
                os.kill(int(pid), signal.SIGTERM)
                stopped = True
            except (ProcessLookupError, PermissionError, ValueError, TypeError):
                pass
            except Exception:  # noqa: BLE001 - best-effort step, the request goes on without it
                log.debug("now: best-effort step failed", exc_info=True)
        # A flow run in docker mode has a container instead of a pid; its
        # nodes and any nested run it started live in there too.
        container = fr.get("container_name")
        if container:
            try:
                from managers.container_manager import stop_container
                if await asyncio.to_thread(stop_container, str(container), 5):
                    stopped = True
            except Exception:  # noqa: BLE001 - best-effort step, the request goes on without it
                log.debug("flow container stop failed", exc_info=True)

        # 3) Stop this instance's in-flight node runs (they carry flow_run_id).
        for r in all_runs:
            if r.get("flow_run_id") != fr_id or r.get("status") not in {"running", "pending"}:
                continue
            if r.get("run_id") and run_manager.stop_run_by_id(r["run_id"]):
                stopped = True
                label = r.get("flow_node_label") or r.get("agent_id", "")
                _append_flow_log({
                    "timestamp": now(), "type": "agent_stopped",
                    "agent_id": r.get("agent_id", ""), "agent_name": label,
                    "content": f"{label} stopped by user", "status": "stopped",
                    "run_group": fr_id, "kind": "task",
                })

        # 4) Stop the task (covers a stop landing between nodes with no node run).
        task_id = fr.get("task_id")
        if task_id:
            try:
                tasks_service.clear_agent(UUID(str(task_id)))
                tasks_service.stop_task(UUID(str(task_id)))
                stopped = True
            except Exception:  # noqa: BLE001 - best-effort step, the request goes on without it
                log.debug("now: best-effort step failed", exc_info=True)

        _append_flow_log({
            "timestamp": now(), "type": "flow_stopped",
            "content": "Flow stopped by user", "status": "stopped",
            "run_group": fr_id, "kind": "task",
        })

    # No instance left running → clear the coarse marker.
    if not run_store.get_active_flow_runs(flow_id):
        _set_flow_running(flow_id, False)

    return {"stopped": stopped}


@router.get("/{flow_id}/instances")
async def get_flow_instances(flow_id: str, active_only: bool = False):
    """List this flow's run instances (one record per execution).

    A flow can run in parallel, so this returns every flow-run record for it;
    pass ``active_only=true`` for just the running/pending ones.
    """
    from flow import run_store
    if active_only:
        return run_store.get_active_flow_runs(flow_id)
    return [r for r in run_store.load_flow_runs() if r.get("flow_id") == flow_id]


#: The log lines that end an agent node, which carry the node's run.
_NODE_END_TYPES = ("agent_finish", "agent_error", "agent_stopped")


_BRANCH_TAIL = re.compile(r"→ branch (\[.*\])\s*$")


def _attach_routes(flow_id: str, events: List[Dict]) -> List[Dict]:
    """Give the line of a switch or condition written before it carried its
    ``route`` the one it implies: the steps it chose, by name (the line only
    names their ids, in its ``content``)."""
    wanted = [i for i, ev in enumerate(events)
              if ev.get("type") == "agent_finish" and not ev.get("route")
              and _BRANCH_TAIL.search(str(ev.get("content") or ""))]
    if not wanted:
        return events
    from flow.branching import node_route
    flow = flow_store.get_flow(flow_id) or {}
    out = list(events)
    for i in wanted:
        ev = out[i]
        tail = _BRANCH_TAIL.search(str(ev["content"]))
        if tail is None:
            continue
        try:
            goto = ast.literal_eval(tail.group(1))
        except (ValueError, SyntaxError):
            continue
        if not isinstance(goto, list):
            continue
        route = node_route({"goto": [str(g) for g in goto], "text": ev.get("output") or ""}, flow)
        if route:
            out[i] = {**ev, "route": route}
    return out


def _attach_node_runs(events: List[Dict]) -> List[Dict]:
    """Give the node lines written before they carried ``run_id`` their run.

    The dashboard folds a node's steps (tool calls, thoughts) into its log line
    from that run (``GET /messages/{run_id}/trail``). A flow chat's node run is
    registered under the conversation as ``task_id``, with the node in its
    ``extra``, and finishes at the very timestamp its log line carries, so the
    line finds its run exactly. Lines that already name a run are left alone.
    """
    wanted = [ev for ev in events
              if ev.get("type") in _NODE_END_TYPES and not ev.get("run_id")
              and ev.get("kind") == "chat" and ev.get("run_group") and ev.get("node_id")]
    if not wanted:
        return events
    groups = sorted({str(ev["run_group"]) for ev in wanted})
    by_node: Dict[tuple, List[tuple]] = {}
    try:
        from common import db
        conn = db.get_conn()
        for i in range(0, len(groups), 400):
            chunk = groups[i:i + 400]
            marks = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT run_id, task_id, finished_at, extra FROM runs WHERE task_id IN ({marks})",
                chunk,
            ).fetchall()
            for row in rows:
                r = dict(row)
                extra = r.get("extra") or {}
                if isinstance(extra, str):
                    try:
                        extra = json.loads(extra)
                    except ValueError:
                        extra = {}
                node = (extra or {}).get("flow_node_id")
                if node:
                    by_node.setdefault((str(r["task_id"]), str(node)), []).append(
                        (str(r.get("finished_at") or ""), str(r["run_id"])))
    except Exception:  # noqa: BLE001 - the log reads fine without the links
        log.debug("flow log: could not link node runs", exc_info=True)
        return events
    for ev in wanted:
        runs = by_node.get((str(ev["run_group"]), str(ev["node_id"]))) or []
        ts = str(ev.get("timestamp") or "")
        exact = next((rid for fin, rid in runs if fin == ts), None)
        if exact:
            ev["run_id"] = exact
        elif len(runs) == 1:
            ev["run_id"] = runs[0][1]
    return events


@router.get("/{flow_id}/logs")
async def get_flow_logs(flow_id: str, workspace: Optional[str] = None):
    return _read_flow_log(flow_id)


def _read_flow_log(flow_id: str) -> List[Dict]:
    from flow import run_store
    return _close_ended_runs(_attach_routes(flow_id, _attach_node_runs(run_store.read_flow_logs(flow_id))))


def _close_ended_runs(events: List[Dict]) -> List[Dict]:
    """Give a task run that ended without a last line in its log (a loop pass
    before the loop wrote one, a process that died) the line its execution
    record implies, so neither the History nor the live view takes it for a
    run still going."""
    from flow import run_store
    last: Dict[str, int] = {}
    ended: set = set()
    for i, ev in enumerate(events):
        group = ev.get("run_group")
        if not group or (ev.get("kind") or "task") != "task":
            continue
        last[group] = i
        if ev.get("type") in ("flow_finish", "flow_stopped"):
            ended.add(group)
        elif ev.get("type") == "flow_start":
            ended.discard(group)
    closing: Dict[int, Dict] = {}
    for group, i in last.items():
        if group in ended:
            continue
        record = run_store.get_flow_run(group) or {}
        status = str(record.get("status") or "")
        if status not in ("completed", "failed", "stopped"):
            continue
        stopped = status == "stopped"
        closing[i] = {
            "type": "flow_stopped" if stopped else "flow_finish",
            "timestamp": record.get("finished_at") or events[i].get("timestamp"),
            "run_group": group, "kind": "task",
            "status": "stopped" if stopped else "completed",
            "content": "Flow stopped" if stopped else "Flow finished",
        }
    if not closing:
        return events
    out: List[Dict] = []
    for i, ev in enumerate(events):
        out.append(ev)
        if i in closing:
            out.append(closing[i])
    return out


@router.get("/{flow_id}/runs")
async def get_flow_runs(flow_id: str, workspace: Optional[str] = None):
    """
    Return the flow's execution history grouped into one record per run.

    Each record is either a task run (one flow_start→flow_finish from runtime/flow_run.py)
    or a chat conversation (all turns of one flow chat collapse into a single
    record). Records carry their own slice of log events so the dashboard can
    render per-run logs. Legacy events without a run_group are grouped by
    flow_start boundaries so old logs still surface.
    """
    
    events = _read_flow_log(flow_id)

    groups: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    legacy_seq = 0

    for ev in events:
        key = ev.get("run_group")
        if not key:
            # Legacy events: start a new synthetic group on each flow_start.
            if ev.get("type") == "flow_start" or not order:
                legacy_seq += 1
            key = f"legacy-{legacy_seq}"
        if key not in groups:
            groups[key] = {
                "run_group": key,
                "kind": ev.get("kind") or "task",
                "title": None,
                "started_at": None,
                "finished_at": None,
                "status": "running",
                "events": [],
            }
            order.append(key)
        g = groups[key]
        g["events"].append(ev)

        if ev.get("kind"):
            g["kind"] = ev["kind"]
        ts = ev.get("timestamp")
        etype = ev.get("type")
        if etype == "flow_start":
            if g["started_at"] is None:
                g["started_at"] = ts
            # Chat conversations log one flow_start per turn; keep the first
            # non-empty title (the conversation's first message) rather than
            # letting each later turn overwrite it.
            if ev.get("title") and not g["title"]:
                g["title"] = ev["title"]
            if ev.get("session_id"):
                g["session_id"] = ev["session_id"]
            if ev.get("conversation_id"):
                g["conversation_id"] = ev["conversation_id"]
            if ev.get("task_id"):
                g["task_id"] = ev["task_id"]
        elif etype in ("flow_finish", "flow_stopped"):
            g["finished_at"] = ts
            g["status"] = "stopped" if etype == "flow_stopped" else "completed"
        if g["started_at"] is None and ts:
            g["started_at"] = ts

    # Most recent first.
    records = [groups[k] for k in order]
    records.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return records


@router.get("/{flow_id}/runs/{run_group}")
async def get_flow_run_record(flow_id: str, run_group: str):
    """One History entry with its log events: what a chat that started the
    run shows of it once the run has ended and the page was reloaded."""
    from flow import run_store
    events = run_store.read_run_group_events(flow_id, run_group)
    if not events:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run_group": run_group, "kind": events[0].get("kind") or "task",
            "events": _attach_routes(flow_id, _attach_node_runs(events))}


@router.delete("/{flow_id}/runs/{run_group}")
async def delete_flow_chat(flow_id: str, run_group: str):
    """Drop one entry from the History tab: a chat, with its conversation, or a
    task run, with its execution record (it can no longer be resumed).

    The task a run worked on stays on the board, and the run ledger keeps the
    node runs, as when a chat is deleted on the Chat page. Anything still
    running is refused: its next event would write the log back.
    """
    from common import chat_store, live_runs
    from flow import run_store
    kind = run_store.flow_history_kind(flow_id, run_group)
    if kind is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if kind == "chat":
        if run_group in live_runs.running_conversations():
            raise HTTPException(status_code=409, detail="The chat is still answering; stop it first")
    else:
        record = run_store.get_flow_run(run_group) or {}
        if str(record.get("status") or "") in ("running", "pending", "stopping"):
            raise HTTPException(status_code=409, detail="The run is still going; stop it first")
    if run_store.delete_flow_history(flow_id, run_group) == "not_found":
        raise HTTPException(status_code=404, detail="Run not found")
    if kind == "chat":
        chat_store.delete_chat(run_group)
    return {"deleted": True, "kind": kind}


class TurnSummaryRequest(BaseModel):
    """A flow chat turn that ended without a summary: its id (the turn_id of
    its flow_start line, or that line's timestamp for an older record), the
    message it answered and what each node answered."""
    turn_id: str
    user_message: str = ""
    responses: List[Dict[str, Any]] = []


@router.post("/{flow_id}/runs/{run_group}/summary")
async def summarize_flow_chat_turn(flow_id: str, run_group: str, body: TurnSummaryRequest):
    """Write the summary of one turn of a flow chat after the fact: the turn
    was sent in the Chat view, and the person switched to the Summary view and
    asked for one. Kept in the chat's log, naming its turn, so a reopened
    chat shows it under that turn (chat/flow_summary.py)."""
    from chat import flow_summary
    from chat.runs import utc_iso
    from flow import run_store
    if run_store.flow_history_kind(flow_id, run_group) != "chat":
        raise HTTPException(status_code=404, detail="Chat not found")
    responses = [
        {"agent_label": str(r.get("agent_label") or ""), "agent_id": r.get("agent_id"),
         "response": str(r.get("response") or ""), "error": r.get("error") or None,
         "is_agent": r.get("is_agent", True) is not False}
        for r in body.responses if isinstance(r, dict)
    ]
    last_agent = next((r.get("agent_id") for r in reversed(responses) if r.get("is_agent")), None)
    summary = await flow_summary.write_summary(
        body.user_message, responses, **flow_summary.agent_model(last_agent))
    if not summary:
        raise HTTPException(status_code=502, detail="The summary could not be written")
    run_store.log_flow_event(flow_id, run_group, {
        "timestamp": utc_iso(), "type": "flow_summary", "turn_id": body.turn_id,
        "content": "Summary of the turn", "output": summary,
    }, kind="chat")
    return {"turn_id": body.turn_id, "summary": summary}


# ── AI flow generation ─────────────────────────────────────────────────────────

class FlowGenerateRequest(BaseModel):
    requirement: str
    workspace: Optional[str] = None
    provider: Optional[str] = None   # openai | anthropic | google | ollama | lmstudio | None=inherit
    model: Optional[str] = None
    base_url: Optional[str] = None   # for ollama / lmstudio


FLOW_CREATOR_AGENT_ID = "flow_creator"


def _ensure_flow_creator_registered() -> None:
    """Self-heal installs whose agents.json predates the flow_creator agent.

    Bootstrap only seeds agents.json when it doesn't exist, so existing
    deployments never pick up new system agents from bootstrap/. Mirror the
    bootstrap entry here when it is missing.
    """
    from agents.registry import AgentSpec, add_agent, get_agent

    if get_agent(FLOW_CREATOR_AGENT_ID):
        return
    add_agent(AgentSpec(
        id=FLOW_CREATOR_AGENT_ID,
        name="Flow Creator",
        type="langchain",
        entrypoint="agents.agent_factory:build_agent_executor",
        description=(
            "Designs, creates, modifies, validates, and deletes agent flows "
            "(multi-agent pipelines) using the registered agents."
        ),
        domain="orchestration",
        tools=[
            "list_agents_tool",
            "list_flows_tool",
            "create_flow_tool",
            "get_flow_tool",
            "modify_flow_tool",
            "delete_flow_tool",
            "validate_flow_tool",
        ],
        capacity=1,
    ))


@router.post("/generate")
async def generate_flow(data: FlowGenerateRequest, request: Request = cast(Request, None)):
    """Run the flow_creator agent to design, validate, and create a flow for the requirement."""
    record_access.require_in_workspace(request, data.workspace)
    from agents import registry
    from common.workspace_context import _workspace_ctx, filter_agents_for_workspace

    _ensure_flow_creator_registered()

    # Fail fast when the workspace has no agents to build from.
    available = filter_agents_for_workspace(registry.list_agents(), data.workspace)
    if not [a for a in available if a.id != FLOW_CREATOR_AGENT_ID]:
        return {
            "type": "limitations",
            "message": "No agents are available in the current workspace. Please add agents to the workspace first.",
            "partial_flow": None,
        }

    try:
        from agents.agent_factory import create_agent as build_flow_creator
        from workspace import resolve_workspace_arg

        ws_path, ws_name = resolve_workspace_arg(data.workspace) if data.workspace else (None, None)
        # Tools resolve the workspace from this ContextVar (it propagates into
        # asyncio.to_thread), so created flows land in the requested workspace.
        if ws_name:
            _workspace_ctx.set(ws_name)

        overrides = {}
        if data.provider:
            overrides["provider"] = data.provider
        if data.model:
            overrides["model"] = data.model
        if data.base_url:
            overrides["base_url"] = data.base_url

        agent = build_flow_creator(FLOW_CREATOR_AGENT_ID, workspace=ws_path, **overrides)

        instruction = (
            "Design and create an agent flow for the following requirement. "
            "Follow your standard workflow: list the available agents, design the "
            "graph, validate it, then create it with create_flow_tool. If the "
            "available agents cannot cover the requirement, create nothing and "
            "explain exactly what is missing.\n\n"
            f"Requirement:\n{data.requirement}"
        )
        result = await asyncio.to_thread(agent.run, instruction)
    except Exception as e:  # noqa: BLE001 - reported to the client as an HTTP error
        raise HTTPException(status_code=500, detail=f"Flow generation failed: {e}")

    # The agent persists the flow itself; recover what it created from the
    # create_flow_tool step so the UI can link straight to the editor.
    created_flow: Optional[Dict[str, Any]] = None
    for step in getattr(result, "steps", []) or []:
        if step.name != "create_flow_tool":
            continue
        try:
            out = json.loads(step.output)
        except (json.JSONDecodeError, TypeError):
            continue
        if out.get("ok") and out.get("flow"):
            created_flow = out["flow"]

    summary = (getattr(result, "agent_output", None) or "").strip()

    if created_flow:
        return {
            "type": "flow",
            "flow_id": created_flow.get("id"),
            "name": created_flow.get("name"),
            "description": created_flow.get("description") or "",
            "reasoning": summary,
            "nodes": created_flow.get("nodes") or [],
            "edges": created_flow.get("edges") or [],
        }

    if not getattr(result, "ok", False):
        raise HTTPException(
            status_code=500,
            detail=f"Flow generation failed: {getattr(result, 'error', None) or 'agent run failed'}",
        )

    # Agent finished without creating a flow → it judged the requirement
    # uncoverable; its final answer explains what is missing.
    return {
        "type": "limitations",
        "message": summary or "The flow creator could not design a flow for this requirement.",
        "partial_flow": None,
    }
