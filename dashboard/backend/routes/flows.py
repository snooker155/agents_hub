"""
User-defined Agent Flows (visual pipelines) API routes.
"""
from __future__ import annotations

import asyncio
import os
import signal
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from tasks import service as tasks_service
from managers import run_manager
from agents import agent_launcher
from flow import launcher as flow_launcher
from flow import store as flow_store
from flow.engine import ON_ERROR_POLICIES
from flow.estimate import estimate_flow_cost
from models import FlowListItem, FlowDetail, FlowPage
from workspace import (
    create_workspace_folder,
    get_workspace_metadata,
    update_workspace_metadata,
)


router = APIRouter(prefix="/api/flows", tags=["flows"])


# ── helpers ───────────────────────────────────────────────────────────────────
# Flows are persisted in the database via flow_store (the `flows` document store).
# These thin wrappers keep the existing list-oriented CRUD logic unchanged.

def _load() -> List[Dict]:
    return flow_store.list_flows()


def _save(flows: List[Dict]) -> None:
    """Persist a full flow list: upsert each flow, delete any that vanished."""
    existing_ids = {f["id"] for f in flow_store.list_flows() if f.get("id")}
    new_ids = {f["id"] for f in flows if f.get("id")}
    for flow in flows:
        if flow.get("id"):
            flow_store.save_flow(flow)
    for stale in existing_ids - new_ids:
        flow_store.delete_flow(stale)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _authorize_flow_in_workspace(workspace: str, flow_id: str) -> None:
    """Add ``flow_id`` to the workspace's ``allowed_flows`` allowlist (idempotent)."""
    metadata = get_workspace_metadata(workspace)
    allowed = list(metadata.get("allowed_flows") or [])
    if flow_id not in allowed:
        allowed.append(flow_id)
        update_workspace_metadata(workspace, {"allowed_flows": allowed})


# ── request models ────────────────────────────────────────────────────────────

class FlowCreate(BaseModel):
    name: str
    description: str = ""


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


class FlowImport(BaseModel):
    yaml: str
    name: Optional[str] = None


class FlowSharingUpdate(BaseModel):
    shared: bool


class FlowRun(BaseModel):
    workspace: Optional[str] = None
    description: Optional[str] = None
    task_id: Optional[str] = None


class FlowRunNode(BaseModel):
    node_id: str
    workspace: Optional[str] = None
    description: Optional[str] = None


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
                     offset: Optional[int] = None):
    """The flow list. With no ``limit``/``offset`` this is the full list, exactly
    as before; with either, it is one page: ``{items, total, limit, offset}``.

    Flows are file-backed (one YAML+JSON pair per flow via ``flow_store``), not
    a queryable store. Visibility filtering happens on the cheapest field that
    can answer it before anything is parsed off disk, and ``_public_flow`` (the
    only per-item work here) runs on the resulting page, not the whole catalog:

    - An explicit ``allowed_flows`` allowlist *is* the filter, metadata already
      in hand, so only the ids that land on the requested page get read.
    - Otherwise, ownership ("global flows + flows owned by this workspace") is
      a property of each flow's own record, so every flow still has to be
      parsed once to know it: flow_store keeps no index for that.
    - With no workspace at all, ``flow_store`` pages the file list itself and
      parses only those files.
    """
    allowed = get_workspace_metadata(workspace).get("allowed_flows") if workspace else None

    if allowed is not None:
        flow_ids = sorted(str(fid) for fid in allowed)
        total = len(flow_ids)
        page_ids = _paginate(flow_ids, limit, offset)
        flows = [f for f in (flow_store.get_flow(fid) for fid in page_ids) if f]
    elif workspace:
        # Keeps the flow picker in sync with the run_flow authorization check.
        visible = [f for f in _load()
                  if not f.get("workspace") or f.get("workspace") == workspace]
        total = len(visible)
        flows = _paginate(visible, limit, offset)
    else:
        total = flow_store.count_flows()
        flows = flow_store.list_flows(limit=limit, offset=offset)

    page = [_public_flow(f) for f in flows]
    if limit is None and offset is None:
        return page
    return {"items": page, "total": total, "limit": limit, "offset": offset}


@router.post("")
async def create_flow(data: FlowCreate):
    flows = _load()
    flow = {
        "id": str(uuid4()),
        "name": data.name,
        "description": data.description,
        "nodes": [],
        "edges": [],
        "workspace": None,
        "task_id": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    flows.append(flow)
    _save(flows)
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
async def update_flow(flow_id: str, data: FlowUpdate):
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
            patch["updated_at"] = _now()
            flows[i] = {**f, **patch}
            _save(flows)
            # Binding a flow to a workspace authorizes it there.
            ws_name = patch.get("workspace")
            if ws_name:
                _authorize_flow_in_workspace(ws_name, flow_id)
            return _public_flow(flows[i])
    raise HTTPException(status_code=404, detail="Flow not found")


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
        agent_ids = flow_store.flow_agent_ids(flow)
        blockers: List[str] = []
        for agent_id in agent_ids:
            spec = registry.get_agent(agent_id)
            if not spec:
                blockers.append(f"agent '{agent_id}' is not registered")
            elif spec.default_workspace_only:
                blockers.append(
                    f"agent '{agent_id}' is restricted to the default workspace"
                )
        if blockers:
            raise HTTPException(
                status_code=400,
                detail="Cannot publish flow to the marketplace: " + "; ".join(blockers),
            )
        for agent_id in agent_ids:
            spec = registry.get_agent(agent_id)
            if is_system_agent(agent_id) or spec.shared:
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
    return Response(
        content=yaml_text,
        media_type="application/x-yaml",
        headers={"Content-Disposition": f'attachment; filename="{slug}.yaml"'},
    )


@router.post("/import")
async def import_flow(data: FlowImport):
    """Validate an uploaded flow YAML and, if valid, store it as a new flow."""
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

    # A fresh import is a new flow: override name when provided, reset workspace
    # and timestamps so it isn't bound to the source flow's context.
    if data.name and data.name.strip():
        flow["name"] = data.name.strip()
    flow.setdefault("name", "Imported flow")
    flow["workspace"] = None
    flow["shared"] = False
    flow["created_at"] = _now()
    flow["updated_at"] = _now()
    flow_store.save_flow(flow)
    return flow


# ── execution ─────────────────────────────────────────────────────────────────

@router.post("/{flow_id}/run")
async def run_flow(flow_id: str, req: FlowRun):
    flow = next((f for f in _load() if f["id"] == flow_id), None)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")

    if req.task_id:
        t = tasks_service.get_task(req.task_id)
        if not t:
            raise HTTPException(status_code=404, detail=f"Task '{req.task_id}' not found")
        # An existing task runs the flow in its own workspace.
        ws_name = getattr(t, "workspace", None) or req.workspace or flow.get("workspace")
        if not ws_name:
            ws_name = create_workspace_folder().name
    else:
        ws_name = req.workspace or flow.get("workspace")
        if not ws_name:
            ws_name = create_workspace_folder().name
        t = tasks_service.create_task(
            title=f"Flow: {flow['name']}",
            description=req.description or flow.get("description", ""),
            workspace=ws_name,
        )

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
        run_id, session_id = flow_launcher.start_flow_run(str(t.id), flow_id, params)
        tasks_service.assign_agent(t.id, flow.get("name") or flow_id, params, run_id=run_id)
        return {
            "status": "started",
            "task_id": str(t.id),
            "run_id": run_id,
            "session_id": session_id,
            "workspace": ws_name,
            # What this run is expected to cost, from the same catalog prices
            # the cost page uses. Returned with the launch so the figure is in
            # front of whoever pressed Run, not only on the estimate endpoint.
            "estimated_cost": _safe_estimate(flow),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _safe_estimate(flow: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Price a flow, or return None. An estimate must never block a run."""
    try:
        return estimate_flow_cost(flow)
    except Exception:
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


@router.post("/{flow_id}/run-node")
async def run_flow_node(flow_id: str, req: FlowRunNode):
    flow = next((f for f in _load() if f["id"] == flow_id), None)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")

    node = next((n for n in flow.get("nodes", []) if n["id"] == req.node_id), None)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    node_data = node.get("data") if isinstance(node.get("data"), dict) else {}
    agent_id = node.get("agent_id") or node_data.get("agent_id") or ""
    if not agent_id:
        raise HTTPException(status_code=400, detail="Node has no agent_id")

    ws_name = req.workspace or flow.get("workspace")
    if not ws_name:
        ws_name = create_workspace_folder().name

    label = node.get("label") or node_data.get("label") or agent_id
    flow_task_id = flow.get("task_id")
    t = (tasks_service.get_task(flow_task_id) if flow_task_id else None) or tasks_service.create_task(
        title=f"Run {label} · {flow['name']}",
        description=req.description or f"Running {label} from flow {flow['name']}",
        workspace=ws_name,
    )

    params = {
        "description": req.description or flow.get("description", ""),
        "workspace": ws_name,
        "flow_id": flow_id,
        "node_id": req.node_id,
        "task_id": flow.get("task_id"),
    }

    try:
        run_id, session_id = agent_launcher.start_run(str(t.id), agent_id, params)
        tasks_service.assign_agent(t.id, agent_id, params, run_id=run_id)
        return {
            "status": "started",
            "task_id": str(t.id),
            "run_id": run_id,
            "session_id": session_id,
            "workspace": ws_name,
        }
    except Exception as e:
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
            except Exception:
                pass

        # 3) Stop this instance's in-flight node runs (they carry flow_run_id).
        for r in all_runs:
            if r.get("flow_run_id") != fr_id or r.get("status") not in {"running", "pending"}:
                continue
            if run_manager.stop_run_by_id(r.get("run_id")):
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
            except Exception:
                pass

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


@router.get("/{flow_id}/logs")
async def get_flow_logs(flow_id: str, workspace: Optional[str] = None):
    from flow import run_store
    return run_store.read_flow_logs(flow_id)


def _read_flow_log(flow_id: str) -> List[Dict]:
    from flow import run_store
    return run_store.read_flow_logs(flow_id)


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
async def generate_flow(data: FlowGenerateRequest):
    """Run the flow_creator agent to design, validate, and create a flow for the requirement."""
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
    except Exception as e:
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
