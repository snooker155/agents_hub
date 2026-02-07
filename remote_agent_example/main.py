from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
import uuid
import time
import threading

app = FastAPI(title="Example Remote Agent")

# In-memory storage for runs
runs: Dict[str, Dict[str, Any]] = {}

class RunRequest(BaseModel):
    task_id: str
    instruction: str
    workspace: str
    params: Optional[Dict[str, Any]] = None

class RunResponse(BaseModel):
    run_id: str

class StatusResponse(BaseModel):
    status: str
    output: Optional[str] = None
    error: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None

@app.get("/health")
async def health():
    return {"status": "up", "version": "1.0.0", "agent_type": "research-specialist"}

def execute_agent(run_id: str, instruction: str):
    runs[run_id]["status"] = "running"

    # Simulate work
    time.sleep(10)

    # Simulate a "Research" result
    runs[run_id]["status"] = "completed"
    runs[run_id]["output"] = f"Research completed for: {instruction}. Findings: This is a simulated response from a remote agent."
    runs[run_id]["usage"] = {"tokens": 150, "cost": 0.0003}

@app.post("/run", response_model=RunResponse)
async def run_agent(req: RunRequest, background_tasks: BackgroundTasks):
    run_id = str(uuid.uuid4())
    runs[run_id] = {
        "task_id": req.task_id,
        "instruction": req.instruction,
        "status": "queued",
        "output": None,
        "error": None,
        "usage": None
    }

    background_tasks.add_task(execute_agent, run_id, req.instruction)
    return {"run_id": run_id}

@app.get("/status/{run_id}", response_model=StatusResponse)
async def get_status(run_id: str):
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="Run not found")
    return runs[run_id]

@app.post("/stop/{run_id}")
async def stop_run(run_id: str):
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="Run not found")

    runs[run_id]["status"] = "failed"
    runs[run_id]["error"] = "Stopped by user"
    return {"status": "stopped"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
