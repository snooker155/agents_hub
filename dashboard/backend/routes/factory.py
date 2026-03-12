"""
Factory integration API routes.
"""
from fastapi import APIRouter, HTTPException
import json

from common import tasks_service
from agents import run_manager
from common.workspace import create_workspace_folder
from tasks import AgentState
from models import RunAgentRequest, UserInputRequest


router = APIRouter(prefix="/api/factory", tags=["factory"])


@router.get("/graph")
async def get_factory_graph():
    # Return static graph structure
    nodes = [
        {"id": "pm_intake", "label": "PM Intake"},
        {"id": "ba_generate", "label": "BA Generate BRD"},
        {"id": "sd_generate", "label": "SD Generate Spec"},
        {"id": "tl_choose", "label": "TL Choose Stack"},
        {"id": "tl_split", "label": "TL Split Tasks"},
        {"id": "tl_scaffold", "label": "TL Scaffold Env"},
        {"id": "devs", "label": "Dev Execution"},
        {"id": "tl_review", "label": "TL Review"},
        {"id": "END", "label": "End"}
    ]
    edges = [
        {"source": "pm_intake", "target": "ba_generate"},
        {"source": "ba_generate", "target": "sd_generate"},
        {"source": "sd_generate", "target": "tl_choose"},
        {"source": "tl_choose", "target": "tl_split"},
        {"source": "tl_split", "target": "tl_scaffold"},
        {"source": "tl_scaffold", "target": "devs"},
        {"source": "devs", "target": "tl_review"},
        {"source": "tl_review", "target": "END"}
    ]
    return {"nodes": nodes, "edges": edges}


@router.get("/graph/active-node")
async def get_active_node(workspace: str):
    root = create_workspace_folder(workspace)
    status_path = root / "logs" / "graph_status.json"
    if status_path.exists():
        try:
            return json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"active_node": None}


@router.get("/waiting-for-input")
async def check_waiting(workspace: str):
    root = create_workspace_folder(workspace)
    waiting_path = root / "logs" / "waiting_for_input.json"
    if waiting_path.exists():
        try:
            return json.loads(waiting_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"questions": []}


@router.post("/user-input")
async def provide_input(req: UserInputRequest):
    root = create_workspace_folder(req.workspace)
    input_path = root / "logs" / "pending_input.json"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text(json.dumps(req.answers, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "saved"}


@router.post("/run-agent")
async def run_factory_agent_api(req: RunAgentRequest):
    # Resolve workspace
    ws_name = req.workspace
    if not ws_name:
        ws_name = create_workspace_folder().name

    # Create a task for visibility
    t = tasks_service.create_task(
        title=f"Factory {req.agent} run",
        description=req.description or f"Running {req.agent} in factory mode",
        workspace=ws_name
    )

    agent_id = f"factory-{req.agent}" if req.agent != "graph" else "factory-graph"
    params = {
        "action": req.action,
        "description": req.description,
        "workspace": ws_name
    }

    try:
        run_id = run_manager.start_run(str(t.id), agent_id, params)
        tasks_service.assign_agent(t.id, agent_id, params, run_id=run_id)
        tasks_service.set_agent_state(t.id, AgentState.running, run_id=run_id)

        return {
            "status": "started",
            "task_id": str(t.id),
            "run_id": run_id,
            "workspace": ws_name
        }
    except Exception as e:
        tasks_service.set_agent_state(t.id, AgentState.failed)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs")
async def get_factory_logs(workspace: str):
    root = create_workspace_folder(workspace)
    log_path = root / "logs" / "interaction_log.json"
    if log_path.exists():
        try:
            return json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []
