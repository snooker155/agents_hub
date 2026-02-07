import os
import sys
import pathlib
from typing import List, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from domains.swe.factory.common.config import Paths
from domains.swe.factory.common.tasks import load_tasks
from domains.swe.factory.common.utils import load_json, save_json
import subprocess

app = FastAPI(title="Agent Factory Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

P = Paths()

@app.get("/api/tasks")
async def get_tasks():
    try:
        tasks = load_tasks()
        return [t.model_dump() for t in tasks]
    except Exception as e:
        return []

@app.get("/api/logs")
async def get_logs():
    log_path = os.path.join(P.logs, "interaction_log.json")
    if os.path.exists(log_path):
        return load_json(log_path, default=[])
    return []

@app.get("/api/artifacts")
async def get_artifacts():
    artifacts = []

    # List docs
    docs_path = pathlib.Path(P.docs)
    if docs_path.exists():
        for f in docs_path.rglob("*"):
            if f.is_file():
                artifacts.append({
                    "name": f.name,
                    "path": str(f.relative_to(P.out)),
                    "type": "doc"
                })

    # List code
    for code_path in [pathlib.Path(P.code_be), pathlib.Path(P.code_fe)]:
        if code_path.exists():
            for f in code_path.rglob("*"):
                if f.is_file():
                    artifacts.append({
                        "name": f.name,
                        "path": str(f.relative_to(P.out)),
                        "type": "code"
                    })
    return artifacts

@app.get("/api/stats")
async def get_stats():
    tasks = load_tasks()
    stats = {
        "total": len(tasks),
        "todo": sum(1 for t in tasks if t.status == "Todo"),
        "in_progress": sum(1 for t in tasks if t.status == "InProgress"),
        "review": sum(1 for t in tasks if t.status == "Review"),
        "done": sum(1 for t in tasks if t.status == "Done"),
    }
    return stats

class RunAgentRequest(BaseModel):
    agent: str  # pm, ba, sd, tl, be, fe, ops, qa, or 'graph'
    action: str | None = None
    description: str | None = None

@app.post("/api/run-agent")
async def run_agent(req: RunAgentRequest):
    cmd = [sys.executable]
    if req.agent == "graph":
        cmd.append("run_graph.py")
        if req.description:
            cmd.extend(["--desc", req.description])
    else:
        cmd.append("run_agent.py")
        cmd.append(req.agent)
        if req.action:
            cmd.append(req.action)
        if req.description:
            cmd.extend(["--desc", req.description])

    try:
        # Run in background
        log_file = open(os.path.join(P.logs, "agent_execution.log"), "a")
        subprocess.Popen(cmd, cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")),
                         stdout=log_file, stderr=log_file)
        return {"status": "started", "command": " ".join(cmd)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class UserInputRequest(BaseModel):
    answers: Dict[str, str]

@app.post("/api/user-input")
async def provide_input(req: UserInputRequest):
    input_path = os.path.join(P.logs, "pending_input.json")
    save_json(input_path, req.answers)
    return {"status": "saved"}

@app.get("/api/waiting-for-input")
async def check_waiting():
    waiting_path = os.path.join(P.logs, "waiting_for_input.json")
    if os.path.exists(waiting_path):
        return load_json(waiting_path)
    return {"questions": []}

@app.get("/api/graph")
async def get_graph():
    # Return static graph structure based on run_graph.py
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

@app.get("/api/graph/active-node")
async def get_active_node():
    status_path = os.path.join(P.logs, "graph_status.json")
    if os.path.exists(status_path):
        return load_json(status_path)
    return {"active_node": None}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
