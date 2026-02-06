from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os
import json
from pathlib import Path
from uuid import UUID
import threading
from uuid import uuid4

from domains.swe.orchestrator.orchestrator import tasks_service
from domains.swe.orchestrator.orchestrator.agents import registry, run_manager
from domains.swe.orchestrator.tasks import AgentState, CreatedBy
from domains.swe.orchestrator.orchestrator.workspace import create_workspace_folder, list_workspace_folders
from domains.swe.orchestrator.orchestrator.agent import run_decomposing_agent
from domains.swe.orchestrator.tasks.storage import MemoryStore
from domains.swe.orchestrator.tasks.models import SharedMemory

app = FastAPI(title="Orchestrator Dashboard API")

class TaskCreate(BaseModel):
    title: str
    description: str = ""
    workspace_name: Optional[str] = None
    workspace: Optional[str] = None
    should_decompose: bool = False

class AgentClone(BaseModel):
    original_id: str
    new_id: str
    new_name: str

class AgentConnect(BaseModel):
    id: str
    name: str
    agent_url: str
    capacity: int = 1
    capabilities: List[str] = ["remote"]

class AgentCreateCustom(BaseModel):
    id: str
    name: str
    system_prompt: str
    tools: List[str] = ["read_file", "write_file", "list_files"]
    capacity: int = 1

class AgentMemoryUpdate(BaseModel):
    memory_type: str
    memory_data: Any = None

class AgentAssign(BaseModel):
    agent_id: str
    params: Optional[Dict[str, Any]] = None

class DecomposeRequest(BaseModel):
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    verbose: bool = True

class WorkspaceCreate(BaseModel):
    name: Optional[str] = None

class MemoryCreate(BaseModel):
    name: str
    description: str = ""
    type: str = "text"

class MemoryFileAdd(BaseModel):
    name: str
    content: str

def task_to_dict(task):
    data = task.model_dump() if hasattr(task, "model_dump") else task.dict()
    data["id"] = str(data["id"])
    if data.get("parent_id"):
        data["parent_id"] = str(data["parent_id"])
    return data

# Enable CORS for frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "Orchestrator Dashboard API is running"}

@app.get("/api/tasks")
async def list_tasks():
    tasks = tasks_service.list_tasks()
    return [task_to_dict(t) for t in tasks]

@app.post("/api/tasks")
async def create_task(task: TaskCreate):
    # Resolve or create workspace
    ws_path: Optional[str] = None
    if task.workspace_name:
        try:
            # If it's an absolute path, use it. If not, create under workspaces root.
            p = Path(task.workspace_name)
            if p.is_absolute():
                ws_path = str(p.resolve())
            else:
                ws_path = str(create_workspace_folder(task.workspace_name))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to resolve workspace '{task.workspace_name}': {e}")
    elif task.workspace:
        try:
            ws_path = str(Path(task.workspace).expanduser().resolve())
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid workspace path '{task.workspace}': {e}")
    else:
        # Auto-create if none provided
        try:
            ws_path = str(create_workspace_folder())
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to auto-create workspace: {e}")

    # Store only the workspace folder name
    ws_name = Path(ws_path).name if ws_path else None
    t = tasks_service.create_task(
        title=task.title,
        description=task.description,
        workspace=ws_name,
        should_decompose=task.should_decompose
    )
    return task_to_dict(t)

class TaskWorkspaceUpdate(BaseModel):
    workspace_name: Optional[str] = None
    workspace: Optional[str] = None

@app.post("/api/tasks/{task_id}/workspace")
async def set_task_workspace(task_id: UUID, payload: TaskWorkspaceUpdate):
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")

    # Resolve name
    name: Optional[str] = None
    if payload.workspace_name:
        # ensure folder exists
        try:
            p = create_workspace_folder(payload.workspace_name)
            name = p.name
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to create workspace '{payload.workspace_name}': {e}")
    elif payload.workspace:
        try:
            name = Path(payload.workspace).name
            # create if missing
            _ = create_workspace_folder(name)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid workspace '{payload.workspace}': {e}")
    else:
        # auto
        try:
            p = create_workspace_folder()
            name = p.name
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to auto-create workspace: {e}")

    updated = tasks_service.update_task(task_id, workspace=name)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update task")
    return task_to_dict(updated)

@app.get("/api/tasks/{task_id}/workspace-files")
async def list_workspace_files(task_id: UUID, glob: Optional[str] = "**/*"):
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    name = (t.workspace or '').strip()
    if not name:
        return {"files": []}
    # Resolve absolute path under workspaces root
    root = create_workspace_folder(name)  # does not overwrite, ensures exists
    try:
        # Collect relative file paths
        pattern = glob or "**/*"
        files = []
        for p in root.rglob("*"):
            if p.is_file():
                try:
                    rel = p.relative_to(root).as_posix()
                    files.append(rel)
                except Exception:
                    pass
        return {"files": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tasks/{task_id}")
async def get_task(task_id: UUID):
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")

    # Also fetch subtasks
    all_tasks = tasks_service.list_tasks()
    subtasks = [task_to_dict(st) for st in all_tasks if st.parent_id == task_id]

    result = task_to_dict(t)
    result["subtasks"] = subtasks
    return result

@app.post("/api/tasks/{task_id}/stop")
async def stop_task(task_id: UUID):
    t = tasks_service.stop_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return task_to_dict(t)

@app.get("/api/agents")
async def list_agents():
    agents = registry.list_agents()
    return [a.to_dict() for a in agents]

@app.get("/api/agents/{agent_id}")
async def get_agent_details(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    return spec.to_dict()

@app.get("/api/agents/{agent_id}/history")
async def get_agent_history(agent_id: str):
    runs = run_manager._load_runs()
    agent_runs = [r for r in runs if r.get("agent_id") == agent_id]
    # Sort by started_at desc
    agent_runs.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    return agent_runs

@app.post("/api/agents/clone")
async def clone_agent(clone: AgentClone):
    original = registry.get_agent(clone.original_id)
    if not original:
        raise HTTPException(status_code=404, detail="Original agent not found")

    new_spec = registry.AgentSpec(
        id=clone.new_id,
        name=clone.new_name,
        type=original.type,
        entrypoint=original.entrypoint,
        default_params=original.default_params,
        capabilities=original.capabilities,
        capacity=original.capacity,
        is_remote=original.is_remote,
        agent_url=original.agent_url,
        original_id=original.id
    )
    try:
        registry.add_agent(new_spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return new_spec.to_dict()

@app.post("/api/agents/connect")
async def connect_agent(data: AgentConnect):
    spec = registry.AgentSpec(
        id=data.id,
        name=data.name,
        type="http",
        entrypoint="remote", # dummy for remote
        capacity=data.capacity,
        is_remote=True,
        agent_url=data.agent_url,
        capabilities=data.capabilities
    )
    try:
        registry.add_agent(spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return spec.to_dict()

@app.post("/api/agents/{agent_id}/disconnect")
async def disconnect_agent(agent_id: str):
    found = registry.remove_agent(agent_id)
    if not found:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"message": f"Agent {agent_id} disconnected"}

@app.post("/api/agents/{agent_id}/memory")
async def update_agent_memory(agent_id: str, data: AgentMemoryUpdate):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    new_spec = registry.AgentSpec(
        id=spec.id,
        name=spec.name,
        type=spec.type,
        entrypoint=spec.entrypoint,
        default_params=spec.default_params,
        capabilities=spec.capabilities,
        capacity=spec.capacity,
        is_remote=spec.is_remote,
        agent_url=spec.agent_url,
        original_id=spec.original_id,
        memory_type=data.memory_type,
        memory_data=data.memory_data
    )
    registry.add_agent(new_spec)
    return new_spec.to_dict()

@app.delete("/api/agents/{agent_id}/memory")
async def erase_agent_memory(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    new_spec = registry.AgentSpec(
        id=spec.id,
        name=spec.name,
        type=spec.type,
        entrypoint=spec.entrypoint,
        default_params=spec.default_params,
        capabilities=spec.capabilities,
        capacity=spec.capacity,
        is_remote=spec.is_remote,
        agent_url=spec.agent_url,
        original_id=spec.original_id,
        memory_type="none",
        memory_data=None
    )
    registry.add_agent(new_spec)
    return new_spec.to_dict()

@app.get("/api/agents/{agent_id}/health")
async def health_agent(agent_id: str):
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not spec.is_remote or not spec.agent_url:
        return {"status": "up", "type": "local"}

    from domains.swe.orchestrator.orchestrator.agents.remote_runner import check_health
    return check_health(spec.agent_url)

@app.post("/api/agents/create")
async def create_custom_agent(data: AgentCreateCustom):
    # Base it on swe-fs but with custom system prompt and tools
    base = registry.get_agent("swe-fs")
    if not base:
        # Fallback if swe-fs is missing
        entrypoint = "swe_agent.agent:build_agent"
    else:
        entrypoint = base.entrypoint

    spec = registry.AgentSpec(
        id=data.id,
        name=data.name,
        type="langchain",
        entrypoint=entrypoint,
        default_params={"system_prompt": data.system_prompt, "tools": data.tools},
        capabilities=data.tools,
        capacity=data.capacity
    )
    try:
        registry.add_agent(spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return spec.to_dict()

@app.post("/api/tasks/{task_id}/assign")
async def assign_agent(task_id: UUID, assign: AgentAssign):
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")

    spec = registry.get_agent(assign.agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Policy: only user-created tasks may be assigned a decomposer-capable agent
    try:
        caps = list(getattr(spec, "capabilities", []) or [])
    except Exception:
        caps = []
    if "decompose" in caps and getattr(t, "created_by", None) != CreatedBy.user:
        raise HTTPException(status_code=400, detail="Decomposer agent can only be assigned to user-created tasks")

    try:
        run_id = run_manager.start_run(str(task_id), assign.agent_id, assign.params)
        tasks_service.assign_agent(task_id, assign.agent_id, assign.params, run_id=run_id)
        tasks_service.set_agent_state(task_id, AgentState.running, run_id=run_id)

        updated = tasks_service.get_task(task_id)
        return {
            "task": task_to_dict(updated),
            "run_id": run_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tasks/{task_id}/stop-agent")
async def stop_agent(task_id: UUID):
    stopped = run_manager.stop_run(str(task_id))
    tasks_service.set_agent_state(task_id, AgentState.stopped)
    return {"stopped": stopped}

@app.get("/api/tasks/{task_id}/agent-status")
async def get_agent_status(task_id: UUID):
    status = run_manager.get_status(str(task_id))
    return status

@app.get("/api/tasks/{task_id}/progress")
async def get_task_progress(task_id: UUID):
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")

    # Try to find .progress.json in workspace
    if t.workspace:
        try:
            root = create_workspace_folder(str(t.workspace))
            prog_file = root / ".progress.json"
            if prog_file.exists():
                return json.loads(prog_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    return {"steps": []}

@app.get("/api/logs/{run_id}")
async def get_logs(run_id: str):
    # Prefer explicit log_file path from the run state if available
    try:
        run = getattr(run_manager, "get_run_by_id", None)
        run_rec = run(run_id) if callable(run) else None
    except Exception:
        run_rec = None

    if isinstance(run_rec, dict):
        log_path = run_rec.get("log_file")
        if isinstance(log_path, str) and Path(log_path).exists():
            return {"logs": Path(log_path).read_text(encoding="utf-8")}

    # Fallback search: state logs and workspaces
    log_name = f"agent_run_{run_id}.log"

    state_logs = run_manager.STATE_DIR / "logs" / log_name
    if state_logs.exists():
        return {"logs": state_logs.read_text(encoding="utf-8")}

    for t in tasks_service.list_tasks():
        if t.workspace:
            try:
                # t.workspace stores only the NAME; resolve to absolute
                root = create_workspace_folder(str(t.workspace))
                ws_logs = root / ".logs" / log_name
                if ws_logs.exists():
                    return {"logs": ws_logs.read_text(encoding="utf-8")}
            except Exception:
                continue

    raise HTTPException(status_code=404, detail="Log file not found")


@app.post("/api/tasks/{task_id}/decompose")
async def decompose_task(task_id: UUID, payload: DecomposeRequest | None = None):
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")

    # Only user-created tasks can be decomposed via GUI
    if getattr(t, "created_by", None) != CreatedBy.user:
        raise HTTPException(status_code=400, detail="Decomposer can only run on user-created tasks")

    # Prepare run id and log file in the task's workspace
    run_id = str(uuid4())
    try:
        root = create_workspace_folder(t.workspace or None)
    except Exception:
        root = create_workspace_folder()
    log_dir = root / ".logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"agent_run_{run_id}.log"

    # Mark assignment/state for visibility in UI
    try:
        tasks_service.assign_agent(task_id, "decomposer", None, run_id=run_id)
        tasks_service.set_agent_state(task_id, AgentState.running, run_id=run_id)
    except Exception:
        pass

    # Background worker to run decomposer and write logs
    def _worker():
        try:
            with log_file.open("w", encoding="utf-8") as fh:
                fh.write("[decomposer] start\n")
                fh.flush()
                try:
                    res = run_decomposing_agent(
                        str(t.id),
                        t.title,
                        t.description or "",
                        model=(payload.model if payload else None),
                        temperature=(payload.temperature if payload else None),
                        max_tokens=(payload.max_tokens if payload else None),
                        verbose=(payload.verbose if payload else True),
                    )
                    if isinstance(res, dict):
                        out = res.get("output")
                        if out:
                            fh.write(str(out) + "\n")
                        created = res.get("created") or []
                        if created:
                            fh.write(f"created_subtasks={len(created)}\n")
                    else:
                        fh.write("agent finished\n")
                except Exception as e:  # capture errors
                    fh.write(f"error: {e}\n")
                fh.flush()
        finally:
            try:
                tasks_service.set_agent_state(task_id, AgentState.completed)
            except Exception:
                pass

    threading.Thread(target=_worker, daemon=True).start()
    return {"run_id": run_id}


# ---------------- Workspaces ----------------

@app.get("/api/workspaces")
async def list_workspaces():
    roots = list_workspace_folders()
    all_tasks = tasks_service.list_tasks()
    items = []
    for p in roots:
        name = p.name
        ws_tasks = [t for t in all_tasks if (t.workspace or "").strip() == name]
        items.append({
            "name": name,
            "path": str(p),
            "tasks_count": len(ws_tasks),
        })
    return items


@app.post("/api/workspaces")
async def create_workspace(payload: WorkspaceCreate):
    try:
        p = create_workspace_folder(payload.name)
        return {"name": p.name, "path": str(p)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


def _calc_task_progress(task, all_tasks):
    # Only count direct subtasks of this task
    subs = [st for st in all_tasks if st.parent_id == task.id]
    if not subs:
        return 100 if getattr(task, "status", None) == "done" or getattr(task, "status", None).value == "done" else 0
    done = 0
    for st in subs:
        st_status = st.status.value if hasattr(st.status, "value") else str(st.status)
        if st_status == "done":
            done += 1
    return int(round((done / len(subs)) * 100)) if subs else 0


@app.get("/api/workspaces/{name}")
async def get_workspace(name: str):
    root = create_workspace_folder(name)
    all_tasks = tasks_service.list_tasks()
    ws_tasks = [t for t in all_tasks if (t.workspace or "").strip() == root.name and not t.parent_id]
    tasks_info = []
    for t in ws_tasks:
        tasks_info.append({
            **task_to_dict(t),
            "progress": _calc_task_progress(t, all_tasks),
        })
    # files separately via endpoint, here basic info
    return {
        "name": root.name,
        "path": str(root),
        "tasks": tasks_info,
    }


@app.get("/api/shared-memory")
async def list_shared_memory():
    store = MemoryStore()
    memories = store.load()
    return [m.model_dump() if hasattr(m, "model_dump") else m.dict() for m in memories]

@app.post("/api/shared-memory")
async def create_shared_memory(data: MemoryCreate):
    store = MemoryStore()
    mem = SharedMemory(name=data.name, description=data.description, type=data.type)
    store.add(mem)
    return mem.model_dump() if hasattr(mem, "model_dump") else mem.dict()

@app.get("/api/shared-memory/{memory_id}")
async def get_shared_memory(memory_id: UUID):
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")
    return mem.model_dump() if hasattr(mem, "model_dump") else mem.dict()

@app.delete("/api/shared-memory/{memory_id}")
async def delete_shared_memory(memory_id: UUID):
    store = MemoryStore()
    found = store.delete(memory_id)
    if not found:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"message": "Memory deleted"}

@app.post("/api/shared-memory/{memory_id}/files")
async def add_memory_file(memory_id: UUID, data: MemoryFileAdd):
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")

    mem.files.append({"name": data.name, "content": data.content})
    mem.touch()

    # Update in store
    memories = store.load()
    for i, m in enumerate(memories):
        if m.id == mem.id:
            memories[i] = mem
            break
    store.save(memories)
    return mem.model_dump() if hasattr(mem, "model_dump") else mem.dict()

@app.get("/api/workspaces/{name}/files")
async def list_workspace_files_by_name(name: str, glob: Optional[str] = "**/*"):
    root = create_workspace_folder(name)
    pattern = glob or "**/*"
    files = []
    try:
        for p in root.rglob("*"):
            if p.is_file():
                try:
                    files.append(p.relative_to(root).as_posix())
                except Exception:
                    pass
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"files": files}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
