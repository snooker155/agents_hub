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
from agents import registry, run_manager, worker_runner
from tasks import AgentState, CreatedBy, TaskStatus
from common.workspace import create_workspace_folder, resolve_project_root, project_folder_name
from common.workspace import get_workspace_metadata
from agents.agent_factory import create_agent
from models import TaskCreate, TaskWorkspaceUpdate, AgentAssign, DecomposeRequest, TaskUpdate
from common.session_service import add_event_to_session


router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def task_to_dict(task):
    """Convert task object to dictionary."""
    data = task.model_dump() if hasattr(task, "model_dump") else task.dict()
    data["id"] = str(data["id"])
    if data.get("parent_id"):
        data["parent_id"] = str(data["parent_id"])
    # agent_state is a @property not a field, so model_dump() omits it — add explicitly
    try:
        data["agent_state"] = task.agent_state.value
    except Exception:
        data.setdefault("agent_state", "none")
    return data


@router.get("")
async def list_tasks(workspace: Optional[str] = None):
    all_tasks = tasks_service.list_tasks()
    
    if workspace:
        tasks = [t for t in all_tasks if (t.workspace or "").strip() == workspace]
    else:
        tasks = all_tasks

    return [task_to_dict(t) for t in tasks]


@router.post("")
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

    # Resolve parent_id
    parent_uuid = None
    if task.parent_id:
        try:
            from uuid import UUID as _UUID
            parent_uuid = _UUID(task.parent_id)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid parent_id: {task.parent_id}")

    # Resolve project folder name:
    # - Use explicit `project` field if provided
    # - Otherwise derive from the linked Project record (via project_id)
    project_name = (task.project or "").strip() or None
    if not project_name and task.project_id:
        try:
            from projects.storage import ProjectStore as _PS
            _pstore = _PS(path=Path(__file__).resolve().parents[3] / "projects" / "projects.json")
            _proj = _pstore.get(task.project_id)
            if _proj:
                project_name = project_folder_name(_proj.name)
        except Exception:
            pass

    # Ensure the project subfolder exists inside the workspace
    if project_name and ws_name:
        try:
            resolve_project_root(ws_name, project_name)
        except Exception:
            pass

    t = tasks_service.create_task(
        title=task.title,
        description=task.description,
        workspace=ws_name,
        project=project_name,
        should_decompose=task.should_decompose,
        parent_id=parent_uuid,
    )

    # Apply optional fields not in create signature
    extra = {}
    if task.priority:
        extra["priority"] = task.priority
    if task.project_id:
        extra["project_id"] = task.project_id
    if extra:
        tasks_service.update_task(t.id, **extra)
        t = tasks_service.get_task(t.id) or t

    return task_to_dict(t)


@router.get("/{task_id}")
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


@router.patch("/{task_id}")
async def update_task(task_id: UUID, update: TaskUpdate):
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")

    fields = update.model_dump(exclude_unset=True)
    if "status" in fields:
        try:
            fields["status"] = TaskStatus(fields["status"])
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {fields['status']}")
    if "priority" in fields and fields["priority"] not in ("low", "medium", "high", "critical"):
        raise HTTPException(status_code=400, detail=f"Invalid priority: {fields['priority']}")

    # If task is being moved back to an unassigned state, clear the assignment
    # and delete any pre-start run record (awaiting_approval or node-queued assigned).
    new_status = fields.get("status")
    if new_status in (TaskStatus.todo, TaskStatus.ready):
        from tasks import AgentState
        if t.agent_state == AgentState.pending_approval:
            tasks_service.clear_agent(task_id)
            run_manager.delete_awaiting_approval_run(str(task_id))
        elif t.agent_state == AgentState.assigned:
            tasks_service.clear_agent(task_id)
            run_manager.delete_assigned_run(str(task_id))

    updated = tasks_service.update_task(task_id, **fields)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update task")

    return task_to_dict(updated)


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


# Internal workspace paths that should never appear in the Files tab
_INTERNAL_PREFIXES = (".logs/", ".progress.json", ".task_result")

@router.get("/{task_id}/workspace-files")
async def list_workspace_files(task_id: UUID):
    from datetime import datetime, timezone
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    name = (t.workspace or '').strip()
    if not name:
        return {"files": []}

    run_id = getattr(t, "assigned_agent_run_id", None)
    if not run_id:
        return {"files": []}

    run = run_manager.get_run_by_id(str(run_id))
    run_status = (run or {}).get("status", "")

    # For completed runs, read the file list already stored in the result sidecar
    if run_status in ("completed", "failed", "done"):
        stored = tasks_service.get_task_result_files(task_id)
        return {"files": stored}

    # For still-running/pending runs, do a live filesystem scan
    raw_start = (run or {}).get("started_at")
    if not raw_start:
        return {"files": []}
    try:
        run_started_at = datetime.fromisoformat(raw_start)
        if run_started_at.tzinfo is None:
            run_started_at = run_started_at.replace(tzinfo=timezone.utc)
    except Exception:
        return {"files": []}

    project_name = (getattr(t, "project", None) or "").strip() or None
    root = resolve_project_root(name, project_name)
    try:
        files = []
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(root).as_posix()
            except Exception:
                continue
            if any(rel == prefix.rstrip("/") or rel.startswith(prefix) for prefix in _INTERNAL_PREFIXES):
                continue
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
                if mtime >= run_started_at:
                    files.append(rel)
            except Exception:
                pass
        files.sort()
        return {"files": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/{task_id}/assign")
async def assign_agent(task_id: UUID, assign: AgentAssign):
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    if t.status == TaskStatus.stopped:
        raise HTTPException(status_code=400, detail="Task is stopped and cannot be assigned")

    spec = registry.get_agent(assign.agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Check if agent is allowed in workspace
    if t.workspace:
        metadata = get_workspace_metadata(t.workspace)
        allowed = metadata.get("allowed_agents")  # None means unrestricted
        # Always allow orchestrator/decomposer; when allowed list is set, enforce it
        if allowed is not None and assign.agent_id not in allowed and assign.agent_id not in ["orchestrator", "decomposer"]:
            raise HTTPException(status_code=403, detail=f"Agent '{assign.agent_id}' is not authorized for workspace '{t.workspace}'")

    # Policy: only user-created tasks may be assigned to the dedicated decomposer agent
    if assign.agent_id == "decomposer" and getattr(t, "created_by", None) != CreatedBy.user:
        raise HTTPException(status_code=400, detail="Decomposer agent can only be assigned to user-created tasks")

    try:
        ws_name = str(getattr(t, "workspace", "") or "default")
        execution_mode = get_workspace_metadata(ws_name).get("orchestrator", {}).get("execution_mode", "subprocess")

        if execution_mode == "node":
            # Node mode: register an "assigned" run record so agent_state resolves to
            # AgentState.assigned (not pending_approval). Task stays "ready to assign"
            # until a worker node picks it up and transitions the run to "running".
            session_id = getattr(t, "session_id", None) or None
            if not session_id:
                try:
                    from common.session_service import get_or_create_task_session
                    session_id = get_or_create_task_session(title=t.title, workspace=t.workspace)
                    tasks_service.update_task(task_id, session_id=session_id)
                except Exception:
                    session_id = None
            run_id = str(uuid4())
            run_manager.upsert_run({
                "run_id": run_id,
                "task_id": str(task_id),
                "agent_id": assign.agent_id,
                "status": "assigned",
                "session_type": "task",
                "session_id": session_id,
                "created_at": run_manager.utc_now_iso(),
                "started_at": None,
                "finished_at": None,
                "pid": None,
                "exit_code": None,
                "error": None,
            })
            tasks_service.assign_agent(task_id, assign.agent_id, assign.params, run_id=run_id)
            new_status = TaskStatus.reviewing if assign.agent_id == "code_reviewer" else TaskStatus.ready
            tasks_service.update_task(task_id, status=new_status)
        else:
            # start_run creates/reuses a session and returns (run_id, session_id)
            run_id, session_id = worker_runner.start_run(str(task_id), assign.agent_id, assign.params)
            tasks_service.assign_agent(task_id, assign.agent_id, assign.params, run_id=run_id)
            new_status = TaskStatus.reviewing if assign.agent_id == "code_reviewer" else TaskStatus.in_progress
            tasks_service.update_task(task_id, status=new_status)
        if session_id:
            try:
                add_event_to_session(session_id, {
                    "type": "agent_assigned",
                    "agent_id": assign.agent_id,
                    "timestamp": run_manager.utc_now_iso(),
                    "description": f"Assignment of {assign.agent_id} is done by user",
                })
            except Exception:
                pass
        updated = tasks_service.get_task(task_id)
        return {
            "task": task_to_dict(updated),
            "run_id": run_id,
            "pending_approval": False,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{task_id}/approve-assignment")
async def approve_assignment(task_id: UUID):
    """Approve a pending assignment and start the agent run."""
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    if t.status == TaskStatus.stopped:
        raise HTTPException(status_code=400, detail="Task is stopped and cannot be assigned")
    if t.agent_state != AgentState.pending_approval:
        raise HTTPException(status_code=400, detail="Task is not awaiting assignment approval")
    if not t.assigned_agent_type:
        raise HTTPException(status_code=400, detail="No agent assigned to this task")

    try:
        ws_name = str(getattr(t, "workspace", "") or "default")
        execution_mode = get_workspace_metadata(ws_name).get("orchestrator", {}).get("execution_mode", "subprocess")

        if execution_mode == "node":
            session_id = getattr(t, "session_id", None) or None
            if not session_id:
                try:
                    from common.session_service import get_or_create_task_session
                    session_id = get_or_create_task_session(title=t.title, workspace=t.workspace)
                    tasks_service.update_task(task_id, session_id=session_id)
                except Exception:
                    session_id = None
            run_id = str(uuid4())
            run_manager.upsert_run({
                "run_id": run_id,
                "task_id": str(task_id),
                "agent_id": t.assigned_agent_type,
                "status": "assigned",
                "session_type": "task",
                "session_id": session_id,
                "created_at": run_manager.utc_now_iso(),
                "started_at": None,
                "finished_at": None,
                "pid": None,
                "exit_code": None,
                "error": None,
            })
            tasks_service.assign_agent(task_id, t.assigned_agent_type, t.assigned_agent_params, run_id=run_id)
            tasks_service.update_task(task_id, status=TaskStatus.ready)
        else:
            # start_run creates/reuses a session and returns (run_id, session_id)
            run_id, session_id = worker_runner.start_run(str(task_id), t.assigned_agent_type, t.assigned_agent_params)
            tasks_service.assign_agent(task_id, t.assigned_agent_type, t.assigned_agent_params, run_id=run_id)
            tasks_service.update_task(task_id, status=TaskStatus.in_progress)
        if session_id:
            try:
                add_event_to_session(session_id, {
                    "type": "approved",
                    "agent_id": t.assigned_agent_type,
                    "timestamp": run_manager.utc_now_iso(),
                    "description": f"Assignment of {t.assigned_agent_type} approved by user",
                })
            except Exception:
                pass

        # Post-approval orchestrator hooks based on workspace settings.
        try:
            ws_name = str(getattr(t, "workspace", "") or "default")
            orch_settings = get_workspace_metadata(ws_name).get("orchestrator", {})
            wait_for_completion = orch_settings.get("wait_for_completion", False)
            followup_mode = orch_settings.get("followup_mode", "single")

            if wait_for_completion:
                # Spawn a monitor orchestrator that polls until the worker finishes.
                monitor_desc = (
                    f"[MONITOR] Task ID: {task_id}\n"
                    f"Task: {t.title}\n\n"
                    f"Agent '{t.assigned_agent_type}' was approved by the user and is now running. "
                    f"Skip Steps 1-3. Go directly to Step 4: use wait_for_agent_tool with task id {task_id}, "
                    f"then get_agent_status_tool, and repeat until done. Then summarise."
                )
                worker_runner.start_run(
                    str(task_id),
                    "orchestrator",
                    {"description": monitor_desc},
                )
            elif followup_mode == "continuous" and session_id:
                # Register a continuation so the orchestrator re-fires after the
                # worker finishes — same mechanism used by live+continuous mode.
                from common.session_service import register_continuation
                register_continuation(
                    session_id=session_id,
                    task_id=str(task_id),
                    workspace=ws_name,
                )
        except Exception:
            pass

        updated = tasks_service.get_task(task_id)
        return {"task": task_to_dict(updated), "run_id": run_id, "pending_approval": False,}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{task_id}/reject-assignment")
async def reject_assignment(task_id: UUID):
    """Reject a pending assignment, clearing the agent assignment."""
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    if t.agent_state != AgentState.pending_approval:
        raise HTTPException(status_code=400, detail="Task is not awaiting assignment approval")

    # Record rejection event in session if one exists
    session_id = getattr(t, "session_id", None)
    if session_id:
        try:
            add_event_to_session(session_id, {
                "type": "rejected",
                "agent_id": t.assigned_agent_type,
                "timestamp": run_manager.utc_now_iso(),
                "description": f"Assignment of {t.assigned_agent_type} rejected by user",
            })
        except Exception:
            pass

    # Determine the status to restore: go back to the pre-assignment status if
    # it was a finished state, otherwise fall back to todo.
    _FINISHED_STATUSES = {TaskStatus.resolved, TaskStatus.reviewing, TaskStatus.reviewed, TaskStatus.done}
    restore_status = t.pre_assignment_status if t.pre_assignment_status in _FINISHED_STATUSES else TaskStatus.todo

    # Clear pending assignment, delete the awaiting_approval run record, and restore status.
    tasks_service.clear_agent(task_id)
    run_manager.delete_awaiting_approval_run(str(task_id))
    latest = tasks_service.get_task(task_id)
    if latest and latest.status != TaskStatus.stopped:
        tasks_service.update_task(task_id, status=restore_status)
    updated = tasks_service.get_task(task_id)
    return {"task": task_to_dict(updated)}


@router.post("/{task_id}/stop-agent")
async def stop_agent(task_id: UUID):
    stopped = run_manager.stop_run(str(task_id))
    # If there is no active run (e.g. pending manual assignment), treat stop as
    # task-level stop to prevent further orchestrator assignment attempts.
    if not stopped:
        tasks_service.clear_agent(task_id)
        tasks_service.stop_task(task_id)
    return {"stopped": True}


@router.get("/{task_id}/agent-status")
async def get_agent_status(task_id: UUID):
    task = tasks_service.get_task(task_id)
    run_id = str(getattr(task, "assigned_agent_run_id", None) or "") if task else ""
    status = run_manager.get_status(str(task_id), run_id=run_id or None)
    # print(f"Fetched status for task {task_id}: {status}, current agent state: {task.agent_state if task else 'N/A'}")
    
    # Synchronize task status with agent state
    if task and status:
        run_status = status.get("status")
        if run_status in ("completed", "done", "finished"):
            if task.status != TaskStatus.done:
                tasks_service.update_task(task_id, status=TaskStatus.done)
        elif run_status in ("stopped", "stop"):
            pass
        elif run_status in ("failed", "error"):
            if task.status not in (TaskStatus.blocked, TaskStatus.stopped):
                tasks_service.update_task(task_id, status=TaskStatus.blocked, blocked_reason=f"Agent execution {run_status}")
    
    return status


@router.get("/{task_id}/execution-log")
async def get_task_execution_log(task_id: UUID):
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"entries": tasks_service.get_task_execution_log(task_id)}


@router.get("/{task_id}/activity-log")
async def get_task_activity_log(task_id: UUID):
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    log = tasks_service.get_task_activity_log(task_id)
    return {"activity_log": log}


@router.get("/{task_id}/result")
async def get_task_result(task_id: UUID):
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    results = tasks_service.get_task_results(task_id)
    # Backward-compat: expose latest result/files at top level
    latest = results[-1] if results else {}
    return {
        "results": results,
        "result": latest.get("result"),
        "files": latest.get("files") or [],
    }


@router.put("/{task_id}/result")
async def set_task_result(task_id: UUID, payload: dict):
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    result_text = payload.get("result", "")
    tasks_service.set_task_result(task_id, str(result_text))
    return {"result": result_text}


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

                    result = agent.run(prompt, run_id)
                    if result.ok:
                        fh.write(str(result.agent_output) + "\n")
                    else:
                        fh.write(f"error: {result.error}\n")
                except Exception as e:  # capture errors
                    fh.write(f"error: {e}\n")
                fh.flush()
        finally:
            try:
                # Decomposition does not complete the parent task
                pass
            except Exception:
                pass

    threading.Thread(target=_worker, daemon=True).start()
    return {"run_id": run_id}
