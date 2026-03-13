"""
Task-related API routes.
"""
from fastapi import APIRouter, HTTPException
from typing import Optional
from uuid import UUID
import json
import threading
from uuid import uuid4
from pathlib import Path

from common import tasks_service
from agents import registry, run_manager
from tasks import AgentState, CreatedBy, TaskStatus
from common.workspace import create_workspace_folder
from agents.factory import create_agent
from models import TaskCreate, TaskWorkspaceUpdate, AgentAssign, DecomposeRequest


router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def task_to_dict(task):
    """Convert task object to dictionary."""
    data = task.model_dump() if hasattr(task, "model_dump") else task.dict()
    data["id"] = str(data["id"])
    if data.get("parent_id"):
        data["parent_id"] = str(data["parent_id"])
    return data


@router.get("")
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


@router.post("")
async def create_task(task: TaskCreate):
    # Import here to avoid circular imports with helper function
    from main import get_orchestrator_settings_path
    
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


@router.get("/{task_id}")
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


@router.delete("/{task_id}")
async def delete_task(task_id: UUID, cascade: bool = True):
    deleted = tasks_service.delete_task(task_id, cascade=cascade)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task_id": str(task_id), "deleted": deleted, "cascade": cascade}


@router.post("/{task_id}/stop")
async def stop_task(task_id: UUID):
    t = tasks_service.stop_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return task_to_dict(t)


@router.post("/{task_id}/workspace")
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


@router.get("/{task_id}/workspace-files")
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


@router.post("/{task_id}/assign")
async def assign_agent(task_id: UUID, assign: AgentAssign):
    from common.workspace import get_workspace_metadata
    
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

    # Policy: only user-created tasks may be assigned to the dedicated decomposer agent
    if assign.agent_id == "decomposer" and getattr(t, "created_by", None) != CreatedBy.user:
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


@router.post("/{task_id}/stop-agent")
async def stop_agent(task_id: UUID):
    stopped = run_manager.stop_run(str(task_id))
    tasks_service.set_agent_state(task_id, AgentState.stopped)
    return {"stopped": stopped}


@router.get("/{task_id}/agent-status")
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


@router.get("/{task_id}/progress")
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


@router.post("/{task_id}/decompose")
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
                    # Use factory instead of legacy run_decomposing_agent
                    agent = create_agent(
                        "decomposer",
                        workspace=str(root),
                        model=(payload.model if payload and payload.model else None),
                        temperature=(payload.temperature if payload and payload.temperature is not None else None),
                        max_tokens=(payload.max_tokens if payload and payload.max_tokens else None),
                        verbose=(payload.verbose if payload and payload.verbose is not None else True)
                    )

                    prompt = (
                        "Decompose the following high-level task into concrete, small, and verifiable subtasks. "
                        f"Create subtasks ONLY using the add_subtask tool with parent_id={task_id}. "
                        "Do not create other high-level tasks. Establish sequence between subtasks if needed.\n\n"
                        f"Task Data:\nID: {task_id}\nTITLE: {t.title}\nDESCRIPTION: {t.description or ''}\n\n"
                        "Provide a brief summary of the created subtasks at the end."
                    )

                    result = agent.run(prompt)
                    if result.ok:
                        fh.write(str(result.agent_output) + "\n")
                    else:
                        fh.write(f"error: {result.error}\n")
                except Exception as e:  # capture errors
                    fh.write(f"error: {e}\n")
                fh.flush()
        finally:
            try:
                tasks_service.set_agent_state(task_id, AgentState.completed)
                # Decomposition does not complete the parent task
            except Exception:
                pass

    threading.Thread(target=_worker, daemon=True).start()
    return {"run_id": run_id}
