import sys
from pathlib import Path as PathlibPath

# Ensure project root is on sys.path when running this file as a script
project_root = PathlibPath(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os
import json
import yaml
from pathlib import Path
from uuid import UUID
import threading
from uuid import uuid4

from common import tasks_service
from orchestrator.agents import registry, run_manager
from tasks import AgentState, CreatedBy, TaskStatus
from orchestrator.workspace import create_workspace_folder, list_workspace_folders, get_workspace_metadata, update_workspace_metadata
from orchestrator.agent import run_decomposing_agent
from tasks.storage import MemoryStore
from tasks.models import SharedMemory
from agents.factory import get_factory
from tools.registry import get_all_tools, get_tools_by_category

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
    description: str = ""
    domain: str = "general"
    agent_url: str
    capacity: int = 1
    capabilities: List[str] = ["remote"]

class AgentCreateCustom(BaseModel):
    id: str
    name: str
    description: str = ""
    domain: str = "general"
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

class RunAgentRequest(BaseModel):
    agent: str  # pm, ba, sd, tl, be, fe, ops, qa, or 'graph'
    action: str | None = None
    description: str | None = None
    workspace: Optional[str] = None

class UserInputRequest(BaseModel):
    answers: Dict[str, str]
    workspace: str

class MemoryCreate(BaseModel):
    name: str
    description: str = ""
    type: str = "text"

class MemoryFileAdd(BaseModel):
    name: str
    content: str

class YamlManifest(BaseModel):
    yaml: str

class OrchestratorSettings(BaseModel):
    enabled: bool = False

def get_orchestrator_settings_path():
    path = Path("orchestrator/state/orchestrator_settings.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps({"enabled": False}))
    return path

def task_to_dict(task):
    data = task.model_dump() if hasattr(task, "model_dump") else task.dict()
    data["id"] = str(data["id"])
    if data.get("parent_id"):
        data["parent_id"] = str(data["parent_id"])
    return data

# Enable CORS for frontend development
# CORS configuration (safe defaults for local dev; override with ALLOW_ORIGINS)
_env_allowed = os.getenv("ALLOW_ORIGINS", "").strip()
if _env_allowed == "*":
    # Allow any origin (use only for local dev / proxies)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_origin_regex=r".*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    _default_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://0.0.0.0:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    _origins = [o.strip() for o in _env_allowed.split(",") if o.strip()] or _default_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

@app.get("/")
async def root():
    return {"message": "Orchestrator Dashboard API is running"}

@app.get("/api/orchestrator/settings")
async def get_orchestrator_settings():
    path = get_orchestrator_settings_path()
    return json.loads(path.read_text())

@app.post("/api/orchestrator/settings")
async def update_orchestrator_settings(settings: OrchestratorSettings):
    path = get_orchestrator_settings_path()
    path.write_text(json.dumps(settings.model_dump()))
    return settings

@app.get("/api/stats")
async def get_stats(workspace: Optional[str] = None):
    all_tasks = tasks_service.list_tasks()
    if workspace:
        tasks = [t for t in all_tasks if (t.workspace or "").strip() == workspace]
    else:
        tasks = all_tasks

    runs = run_manager._load_runs()
    agents = registry.list_agents()

    total_tasks = len(tasks)
    completed_tasks = 0
    for t in tasks:
        # handle both enum and string status
        status = str(getattr(t, "status", ""))
        if "done" in status.lower() or "completed" in status.lower():
            completed_tasks += 1

    # Active runs
    active_runs = [r for r in runs if r.get("status") == "running"]

    # Agent usage distribution
    agent_usage = {}
    for r in runs:
        aid = r.get("agent_id")
        agent_usage[aid] = agent_usage.get(aid, 0) + 1

    # Domain usage distribution
    domain_usage = {}
    agent_map = {a.id: a for a in agents}
    for r in runs:
        aid = r.get("agent_id")
        agent = agent_map.get(aid)
        domain = agent.domain if agent else "unknown"
        domain_usage[domain] = domain_usage.get(domain, 0) + 1

    # Resource availability (capacity vs active)
    total_capacity = sum(getattr(a, "capacity", 1) for a in agents)
    active_count = len(active_runs)

    # Recent runs
    recent_runs = sorted(runs, key=lambda r: r.get("started_at", ""), reverse=True)[:10]

    return {
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "completion_rate": round((completed_tasks / total_tasks * 100), 2) if total_tasks > 0 else 0,
        "active_runs": active_count,
        "total_capacity": total_capacity,
        "available_slots": max(0, total_capacity - active_count),
        "agent_usage": agent_usage,
        "domain_usage": domain_usage,
        "recent_runs": recent_runs,
        "total_agents": len(agents)
    }

@app.get("/api/tasks")
async def list_tasks(workspace: Optional[str] = None):
    all_tasks = tasks_service.list_tasks()
    
    if workspace:
        tasks = [t for t in all_tasks if (t.workspace or "").strip() == workspace]
    else:
        tasks = all_tasks

    # Synchronize agent state with task status for all tasks
    for t in tasks:
        if t.assigned_agent_run_id:
            status = run_manager.get_status(str(t.id))
            if status:
                run_status = status.get("status")
                # If agent run is completed, mark task as done
                if run_status in ("completed", "done", "finished"):
                    if t.status != TaskStatus.done:
                        tasks_service.update_task(t.id, status=TaskStatus.done)
                        t.status = TaskStatus.done
                # If agent run failed, mark task as blocked
                elif run_status in ("failed", "error"):
                    if t.status not in (TaskStatus.blocked, TaskStatus.stopped):
                        tasks_service.update_task(t.id, status=TaskStatus.blocked, blocked_reason=f"Agent execution {run_status}")
                        t.status = TaskStatus.blocked
    
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

    # Trigger orchestrator if enabled
    settings_path = get_orchestrator_settings_path()
    orch_settings = json.loads(settings_path.read_text())
    if orch_settings.get("enabled"):
        try:
            # For Orchestrator, we use 'orchestrator' as the agent ID
            agent_id = "orchestrator"
            run_id = run_manager.start_run(str(t.id), agent_id, None)
            tasks_service.assign_agent(t.id, agent_id, None, run_id=run_id)
            tasks_service.set_agent_state(t.id, AgentState.running, run_id=run_id)
        except Exception as e:
            print(f"Failed to auto-trigger orchestrator: {e}")

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

    # Synchronize task status with agent state if agent is assigned
    if t.assigned_agent_run_id:
        status = run_manager.get_status(str(task_id))
        if status:
            run_status = status.get("status")
            # If agent run is completed, mark task as done
            if run_status in ("completed", "done", "finished"):
                if t.status != TaskStatus.done:
                    t = tasks_service.update_task(task_id, status=TaskStatus.done) or t
            # If agent run failed, mark task as blocked
            elif run_status in ("failed", "error"):
                if t.status not in (TaskStatus.blocked, TaskStatus.stopped):
                    t = tasks_service.update_task(task_id, status=TaskStatus.blocked, blocked_reason=f"Agent execution {run_status}") or t

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
    """List all available agents from registry and factory definitions."""
    # Get agents from existing registry
    registry_agents = registry.list_agents()
    reg_ids = {a.id for a in registry_agents}
    
    # Get agent definitions from factory
    factory = get_factory()
    factory_agents = factory.list_available_agents()
    
    # Combine into single array for backward compatibility with frontend
    # Registry agents come first, then factory agents (if not already in registry)
    all_agents = [a.to_dict() for a in registry_agents]
    for fa in factory_agents:
        if fa["id"] not in reg_ids:
            all_agents.append(fa)
    
    return all_agents

@app.get("/api/tools")
async def list_tools():
    """List all available tools. Returns factory, swe, and all for backward compatibility."""
    # Get tools from centralized registry
    all_registry_tools = get_all_tools()
    
    # Convert registry tools to dict format expected by frontend
    def tool_spec_to_dict(spec):
        return {
            "name": spec.id,
            "description": spec.description,
            "args": {p["name"]: p["type"] for p in spec.parameters}
        }
    
    registry_tools_dicts = [tool_spec_to_dict(t) for t in all_registry_tools]
    
    # For backward compatibility, return in the format the frontend expects
    return {
        "factory": registry_tools_dicts,  # All tools from registry
        "swe": registry_tools_dicts,      # Same tools (for compatibility)
        "all": registry_tools_dicts,      # Combined list
    }

@app.get("/api/runs")
async def list_runs():
    runs = run_manager._load_runs()
    # Sort by started_at desc
    runs.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    return runs

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
        description=data.description,
        domain=data.domain,
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

    from orchestrator.agents.remote_runner import check_health
    return check_health(spec.agent_url)

@app.post("/api/agents/apply")
async def apply_agent_manifest(data: YamlManifest):
    try:
        manifest = yaml.safe_load(data.yaml)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

    if not manifest or manifest.get("kind") != "Agent":
        raise HTTPException(status_code=400, detail="Manifest must have 'kind: Agent'")

    metadata = manifest.get("metadata", {})
    spec_data = manifest.get("spec", {})

    agent_id = metadata.get("name")
    if not agent_id:
        raise HTTPException(status_code=400, detail="Manifest must have metadata.name")

    # Build AgentSpec
    spec = registry.AgentSpec(
        id=agent_id,
        name=spec_data.get("displayName", agent_id),
        description=spec_data.get("description", ""),
        domain=spec_data.get("domain", "general"),
        type=spec_data.get("type", "langchain"),
        entrypoint=spec_data.get("entrypoint", "swe_agent.agent:build_agent"),
        capacity=spec_data.get("capacity", 1),
        is_remote=spec_data.get("isRemote", False),
        agent_url=spec_data.get("agentUrl"),
        capabilities=spec_data.get("capabilities", []),
        default_params=spec_data.get("defaultParams", {})
    )

    try:
        registry.add_agent(spec)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return spec.to_dict()

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
        description=data.description,
        domain=data.domain,
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

    # Check if agent is allowed in workspace
    if t.workspace:
        metadata = get_workspace_metadata(t.workspace)
        allowed = metadata.get("allowed_agents", [])
        # Always allow orchestrator to be assigned (it's the manager)
        if assign.agent_id not in allowed and assign.agent_id not in ["orchestrator", "decomposer"]:
            raise HTTPException(status_code=403, detail=f"Agent '{assign.agent_id}' is not authorized for workspace '{t.workspace}'")

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
    task = tasks_service.get_task(task_id)
    
    # Synchronize task status with agent state
    if task and status:
        run_status = status.get("status")
        # If agent run is completed or done, mark task as done
        if run_status in ("completed", "done", "finished"):
            if task.status != TaskStatus.done:
                tasks_service.update_task(task_id, status=TaskStatus.done)
        # If agent run failed, mark task as blocked
        elif run_status in ("failed", "error"):
            if task.status not in (TaskStatus.blocked, TaskStatus.stopped):
                tasks_service.update_task(task_id, status=TaskStatus.blocked, blocked_reason=f"Agent execution {run_status}")
    
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
                # Also update task status to done when agent completes
                tasks_service.update_task(task_id, status=TaskStatus.done)
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

    metadata = get_workspace_metadata(root.name)

    # files separately via endpoint, here basic info
    return {
        "name": root.name,
        "path": str(root),
        "tasks": tasks_info,
        "metadata": metadata
    }

class WorkspaceAgentAction(BaseModel):
    agent_id: str

@app.post("/api/workspaces/{name}/agents")
async def add_agent_to_workspace(name: str, action: WorkspaceAgentAction):
    metadata = get_workspace_metadata(name)
    allowed = metadata.get("allowed_agents", [])
    if action.agent_id not in allowed:
        allowed.append(action.agent_id)
        update_workspace_metadata(name, {"allowed_agents": allowed})
    return {"allowed_agents": allowed}

@app.delete("/api/workspaces/{name}/agents/{agent_id}")
async def remove_agent_from_workspace(name: str, agent_id: str):
    metadata = get_workspace_metadata(name)
    allowed = metadata.get("allowed_agents", [])
    if agent_id in allowed:
        allowed.remove(agent_id)
        update_workspace_metadata(name, {"allowed_agents": allowed})
    return {"allowed_agents": allowed}


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

# ---------------- Factory Integration ----------------

@app.get("/api/factory/graph")
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

@app.get("/api/factory/graph/active-node")
async def get_active_node(workspace: str):
    root = create_workspace_folder(workspace)
    status_path = root / "logs" / "graph_status.json"
    if status_path.exists():
        try:
            return json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"active_node": None}

@app.get("/api/factory/waiting-for-input")
async def check_waiting(workspace: str):
    root = create_workspace_folder(workspace)
    waiting_path = root / "logs" / "waiting_for_input.json"
    if waiting_path.exists():
        try:
            return json.loads(waiting_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"questions": []}

@app.post("/api/factory/user-input")
async def provide_input(req: UserInputRequest):
    root = create_workspace_folder(req.workspace)
    input_path = root / "logs" / "pending_input.json"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text(json.dumps(req.answers, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "saved"}

@app.post("/api/factory/run-agent")
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

@app.get("/api/factory/logs")
async def get_factory_logs(workspace: str):
    root = create_workspace_folder(workspace)
    log_path = root / "logs" / "interaction_log.json"
    if log_path.exists():
        try:
            return json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
