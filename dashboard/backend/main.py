from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from typing import List, Dict, Any
from core.models.task import Task
from core.models.incident import Incident

app = FastAPI(title="Unified Autonomous Network Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Domain Routers
tasks_router = APIRouter(prefix="/api/tasks", tags=["tasks"])
agents_router = APIRouter(prefix="/api/agents", tags=["agents"])
simulation_router = APIRouter(prefix="/api/simulation", tags=["simulation"])

@tasks_router.get("/", response_model=List[Task])
async def get_tasks():
    # In a real system, this would aggregate from domains/swe/orchestrator
    return []

@agents_router.get("/")
async def get_agents():
    # Aggregates info from Operational and Evolutionary domains
    return {
        "telecom": [
            {"id": "ctrl-1", "type": "CognitiveController", "status": "active"}
        ],
        "swe": [
            {"id": "fact-1", "type": "DevFactory", "status": "idle"}
        ]
    }

@simulation_router.get("/state")
async def get_sim_state():
    # Proxies to domains/telecom/simulator
    return {"status": "running", "nodes": 10, "traffic": "high"}

app.include_router(tasks_router)
app.include_router(agents_router)
app.include_router(simulation_router)

@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
