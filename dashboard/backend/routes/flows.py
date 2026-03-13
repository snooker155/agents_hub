"""
User-defined Agent Flows (visual pipelines) API routes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from common import tasks_service
from agents import run_manager
from common.workspace import create_workspace_folder
from tasks import AgentState

try:
    from filelock import FileLock
    def _lock(path: str):
        return FileLock(path)
except ImportError:
    import contextlib
    def _lock(path: str):
        return contextlib.nullcontext()


router = APIRouter(prefix="/api/flows", tags=["flows"])

_FLOWS_FILE = Path(__file__).resolve().parents[4] / "agents" / "state" / "flows.json"


# ── helpers ───────────────────────────────────────────────────────────────────

def _load() -> List[Dict]:
    if not _FLOWS_FILE.exists():
        return []
    lock = _lock(str(_FLOWS_FILE) + ".lock")
    with lock:
        try:
            return json.loads(_FLOWS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []


def _save(flows: List[Dict]) -> None:
    _FLOWS_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock(str(_FLOWS_FILE) + ".lock")
    with lock:
        _FLOWS_FILE.write_text(
            json.dumps(flows, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


class FlowRun(BaseModel):
    workspace: Optional[str] = None
    description: Optional[str] = None


class FlowRunNode(BaseModel):
    node_id: str
    workspace: Optional[str] = None
    description: Optional[str] = None


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("")
async def list_flows(workspace: Optional[str] = None):
    flows = _load()
    if workspace:
        flows = [f for f in flows if not f.get("workspace") or f.get("workspace") == workspace]
    return flows


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


@router.get("/{flow_id}")
async def get_flow(flow_id: str):
    for f in _load():
        if f["id"] == flow_id:
            return f
    raise HTTPException(status_code=404, detail="Flow not found")


@router.put("/{flow_id}")
async def update_flow(flow_id: str, data: FlowUpdate):
    flows = _load()
    for i, f in enumerate(flows):
        if f["id"] == flow_id:
            patch = {k: v for k, v in data.model_dump().items() if v is not None}
            patch["updated_at"] = _now()
            flows[i] = {**f, **patch}
            _save(flows)
            return flows[i]
    raise HTTPException(status_code=404, detail="Flow not found")


@router.delete("/{flow_id}")
async def delete_flow(flow_id: str):
    flows = _load()
    new_flows = [f for f in flows if f["id"] != flow_id]
    if len(new_flows) == len(flows):
        raise HTTPException(status_code=404, detail="Flow not found")
    _save(new_flows)
    return {"message": "Flow deleted"}


# ── execution ─────────────────────────────────────────────────────────────────

@router.post("/{flow_id}/run")
async def run_flow(flow_id: str, req: FlowRun):
    flow = next((f for f in _load() if f["id"] == flow_id), None)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")

    ws_name = req.workspace or flow.get("workspace")
    if not ws_name:
        ws_name = create_workspace_folder().name

    t = tasks_service.create_task(
        title=f"Flow: {flow['name']}",
        description=req.description or flow.get("description", ""),
        workspace=ws_name,
    )

    graph_payload = {
        "nodes": flow["nodes"],
        "edges": flow["edges"],
        "task": {
            "id": flow.get("task_id"),
            "title": flow["name"],
            "description": req.description or flow.get("description", ""),
        },
        "shared_context": req.description or flow.get("description", ""),
    }

    params = {
        "action": "run_flow",
        "description": json.dumps(graph_payload),
        "workspace": ws_name,
        "flow_id": flow_id,
        "task_id": flow.get("task_id"),
    }

    try:
        run_id = run_manager.start_run(str(t.id), "factory-custom-graph", params)
        tasks_service.assign_agent(t.id, "factory-custom-graph", params, run_id=run_id)
        tasks_service.set_agent_state(t.id, AgentState.running, run_id=run_id)
        return {"status": "started", "task_id": str(t.id), "run_id": run_id, "workspace": ws_name}
    except Exception as e:
        tasks_service.set_agent_state(t.id, AgentState.failed)
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
    t = tasks_service.create_task(
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
        run_id = run_manager.start_run(str(t.id), agent_id, params)
        tasks_service.assign_agent(t.id, agent_id, params, run_id=run_id)
        tasks_service.set_agent_state(t.id, AgentState.running, run_id=run_id)
        return {"status": "started", "task_id": str(t.id), "run_id": run_id, "workspace": ws_name}
    except Exception as e:
        tasks_service.set_agent_state(t.id, AgentState.failed)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{flow_id}/logs")
async def get_flow_logs(flow_id: str, workspace: str):
    root = create_workspace_folder(workspace)
    log_path = root / "logs" / "interaction_log.json"
    if log_path.exists():
        try:
            return json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []
