"""
User-defined Agent Flows (visual pipelines) API routes.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from common import tasks_service
from agents import run_manager, worker_runner, flow_runner
from workspace import create_workspace_folder

try:
    from filelock import FileLock
    def _lock(path: str):
        return FileLock(path)
except ImportError:
    import contextlib
    def _lock(path: str):
        return contextlib.nullcontext()


router = APIRouter(prefix="/api/flows", tags=["flows"])

_FLOWS_FILE = Path(__file__).resolve().parents[3] / "agents" / "state" / "flows.json"


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
    task_id: Optional[str] = None


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

    if req.task_id:
        t = tasks_service.get_task(req.task_id)
        if not t:
            raise HTTPException(status_code=404, detail=f"Task '{req.task_id}' not found")
    else:
        t = tasks_service.create_task(
            title=f"Flow: {flow['name']}",
            description=req.description or flow.get("description", ""),
            workspace=ws_name,
        )

    params = {
        "workspace": ws_name,
        "description": req.description or flow.get("description", ""),
    }

    try:
        run_id, session_id = flow_runner.start_flow_run(str(t.id), flow_id, params)
        tasks_service.assign_agent(t.id, "flow-custom-graph", params, run_id=run_id)
        return {
            "status": "started",
            "task_id": str(t.id),
            "run_id": run_id,
            "session_id": session_id,
            "workspace": ws_name,
        }
    except Exception as e:
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

    # Resolve factory-* aliases to their YAML definition ids
    _FACTORY_AGENT_MAP = {
        "factory-pm": "pm_agent", "factory-ba": "ba_agent",
        "factory-sd": "sd_agent", "factory-tl": "tl_agent",
        "factory-be": "dev_agent", "factory-fe": "dev_agent",
        "factory-qa": "qa_agent", "factory-ops": "devops_agent",
    }
    agent_id = _FACTORY_AGENT_MAP.get(agent_id, agent_id)

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
        run_id, session_id = worker_runner.start_run(str(t.id), agent_id, params)
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
async def stop_flow(flow_id: str):
    from agents import run_manager
    from agents.run_manager import STATE_DIR
    from agents.flow_runner import _set_flow_running
    from datetime import datetime, timezone

    def _append_flow_log(flow_id: str, entry: dict) -> None:
        log_path = STATE_DIR / "flow_logs" / f"{flow_id}.json"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            existing = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
            existing.append(entry)
            log_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    now = lambda: datetime.now(timezone.utc).isoformat()

    runs = run_manager.load_runs()
    active_runs = [
        r for r in runs
        if r.get("flow_id") == flow_id
        and r.get("status") in {"running", "pending"}
    ]

    stopped = False
    for r in active_runs:
        run_id = r.get("run_id")
        agent_id = r.get("agent_id", "")
        label = r.get("flow_node_label") or agent_id
        is_meta = bool(r.get("is_flow"))

        result = run_manager.stop_run_by_id(run_id)
        if result:
            stopped = True
            if is_meta:
                _append_flow_log(flow_id, {
                    "timestamp": now(),
                    "type": "flow_stopped",
                    "content": "Flow stopped by user",
                    "status": "stopped",
                })
            else:
                _append_flow_log(flow_id, {
                    "timestamp": now(),
                    "type": "agent_stopped",
                    "agent_id": agent_id,
                    "agent_name": label,
                    "content": f"{label} stopped by user",
                    "status": "stopped",
                })

    _set_flow_running(flow_id, False)
    return {"stopped": stopped}


@router.get("/{flow_id}/logs")
async def get_flow_logs(flow_id: str, workspace: Optional[str] = None):
    from agents.run_manager import STATE_DIR
    log_path = STATE_DIR / "flow_logs" / f"{flow_id}.json"
    if log_path.exists():
        try:
            return json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


# ── AI flow generation ─────────────────────────────────────────────────────────

class FlowGenerateRequest(BaseModel):
    requirement: str
    workspace: Optional[str] = None
    provider: Optional[str] = None   # openai | anthropic | google | ollama | lmstudio | None=inherit
    model: Optional[str] = None
    base_url: Optional[str] = None   # for ollama / lmstudio


@router.post("/generate")
async def generate_flow(data: FlowGenerateRequest):
    """Use an LLM to design a factory flow for the given requirement using available agents."""
    from agents import registry
    from agents.agent_factory import get_factory

    # Collect available agents (respecting workspace allowed_agents filter)
    registry_agents = registry.list_agents()
    reg_ids = {a.id for a in registry_agents}
    factory = get_factory()
    factory_agents = factory.list_available_agents()

    all_agents = [a.to_dict() for a in registry_agents]
    for fa in factory_agents:
        if fa["id"] not in reg_ids:
            all_agents.append(fa)

    if data.workspace:
        from workspace import get_workspace_metadata
        metadata = get_workspace_metadata(data.workspace)
        allowed = metadata.get("allowed_agents")
        if allowed is not None:
            all_agents = [a for a in all_agents if a["id"] in allowed]

    if not all_agents:
        return {
            "type": "limitations",
            "message": "No agents are available in the current workspace. Please add agents to the workspace first.",
            "partial_flow": None,
        }

    agents_desc = "\n".join(
        f"- id={a['id']} name={a.get('name', '')} domain={a.get('domain', '')} "
        f"description={a.get('description', '') or 'n/a'} tools={a.get('tools', [])}"
        for a in all_agents
    )

    system_prompt = (
        "You are an AI system architect. Given a user requirement, design a factory flow "
        "using ONLY the available agents listed below.\n\n"
        "A factory flow is a directed acyclic graph of nodes (agent execution steps) "
        "connected by edges (execution order).\n\n"
        "Respond with valid JSON only – no markdown, no explanation outside the JSON.\n\n"
        "Format 1 – when the requirement CAN be implemented:\n"
        "{\n"
        '  "type": "flow",\n'
        '  "name": "<short flow name>",\n'
        '  "description": "<what this flow achieves>",\n'
        '  "reasoning": "<why you chose these agents and this order>",\n'
        '  "nodes": [\n'
        '    {"id": "node-1", "agent_id": "<agent_id>", "label": "<display label>", "description": "<role of this node>"}\n'
        "  ],\n"
        '  "edges": [\n'
        '    {"id": "edge-1", "source": "node-1", "target": "node-2"}\n'
        "  ]\n"
        "}\n\n"
        "Format 2 – when the requirement CANNOT be fully implemented with available agents:\n"
        "{\n"
        '  "type": "limitations",\n'
        '  "message": "<clear explanation of what is missing and why>",\n'
        '  "partial_flow": null\n'
        "}\n\n"
        "Rules:\n"
        "- Only use agent ids from the provided list.\n"
        "- Edges must reference node ids that exist in the nodes array.\n"
        "- The graph must be acyclic.\n"
        "- Be honest: if the available agents cannot cover the requirement, use format 2.\n"
    )

    user_prompt = (
        f"Available agents:\n{agents_desc}\n\n"
        f"User requirement:\n{data.requirement}\n\n"
        "Design the factory flow. Respond with JSON only."
    )

    try:
        from common.agent_utils import build_chat_model
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = build_chat_model(
            provider=data.provider or None,
            model=data.model or None,
            base_url=data.base_url or None,
        )
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        response = await asyncio.to_thread(llm.invoke, messages)
        raw = response.content.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        result = json.loads(raw)

        # Validate basic structure
        if result.get("type") not in ("flow", "limitations"):
            raise ValueError("Unexpected response type from LLM")

        return result
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"LLM returned non-JSON response: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Flow generation failed: {e}")
