"""
Task-related API routes.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from typing import Optional
from uuid import UUID
import json
import threading
import mimetypes
from uuid import uuid4
from pathlib import Path

from tasks import service as tasks_service
from agents import registry
from managers import run_manager
from agents import agent_launcher
from tasks import AgentState, CreatedBy, TaskStatus
from workspace import create_workspace_folder, resolve_project_root, project_folder_name, resolve_task_project_name
from workspace import get_workspace_metadata
from agents.agent_factory import create_agent
from tasks.assign import assign_agent_to_task, AssignError
from tasks.serialize import task_to_dict
from models import TaskCreate, TaskWorkspaceUpdate, AgentAssign, DecomposeRequest, TaskUpdate, TaskAnswer
from common.session_service import add_event_to_session
from common.paths import PROJECTS_FILE


router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _resolve_task_ref(ref: str) -> UUID:
    """Resolve a task reference — UUID string or Jira-style key — to a UUID."""
    s = str(ref).strip()
    try:
        return UUID(s)
    except ValueError:
        from tasks.keys import looks_like_key
        if looks_like_key(s):
            t = tasks_service.find_task_by_key(s)
            if t:
                return t.id
        raise HTTPException(status_code=400, detail=f"Unknown task reference: {ref}")




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
    # Resolve or create workspace. Workspaces always live under
    # .agents_hub/workspaces/; any supplied value is reduced to its folder name.
    ws_path: Optional[str] = None
    requested_ws = task.workspace_name or task.workspace
    if requested_ws:
        try:
            ws_path = str(create_workspace_folder(Path(requested_ws).name))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to resolve workspace '{requested_ws}': {e}")
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
            _pstore = _PS(path=PROJECTS_FILE)
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

    # Resolve dependency references (UUIDs or task keys like "DEMO-12")
    depends_uuids = None
    if task.depends:
        depends_uuids = [_resolve_task_ref(d) for d in task.depends]

    # Set project_id before creation so the Jira-style key uses the project prefix
    try:
        t = tasks_service.create_task(
            title=task.title,
            description=task.description,
            workspace=ws_name,
            project=project_name,
            project_id=task.project_id or None,
            should_decompose=task.should_decompose,
            parent_id=parent_uuid,
            depends=depends_uuids,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Apply optional fields not in create signature
    extra = {}
    if task.priority:
        extra["priority"] = task.priority
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
    if "depends" in fields and fields["depends"] is not None:
        fields["depends"] = [_resolve_task_ref(d) for d in fields["depends"]]

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

    try:
        updated = tasks_service.update_task(task_id, **fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update task")

    # Dependency changes may have re-blocked/released the task after the update
    return task_to_dict(tasks_service.get_task(task_id) or updated)


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


@router.post("/{task_id}/pause")
async def pause_container(task_id: UUID):
    """Pause a container task: stop the active subtask run and freeze dispatch."""
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    if not tasks_service.get_subtasks(task_id):
        raise HTTPException(status_code=400, detail="Task has no subtasks — pause applies to container tasks")
    result = tasks_service.pause_container(task_id)
    return {"task": task_to_dict(tasks_service.get_task(task_id)), **result}


@router.post("/{task_id}/resume")
async def resume_container(task_id: UUID):
    """Resume a paused container: re-queue paused subtasks and restart dispatch."""
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    if not tasks_service.get_subtasks(task_id):
        raise HTTPException(status_code=400, detail="Task has no subtasks — resume applies to container tasks")
    result = tasks_service.resume_container(task_id)
    return {"task": task_to_dict(tasks_service.get_task(task_id)), **result}


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


def _resolve_task_file(task_id: UUID, path: str) -> Path:
    """Resolve a workspace-relative file path within a task's project root.

    Task workspace files are listed relative to ``resolve_project_root`` (the
    workspace root, or its ``{project}`` subfolder when the task has a project),
    so file preview/raw access must resolve against the same root.
    """
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    name = (t.workspace or "").strip()
    if not name:
        raise HTTPException(status_code=404, detail="Task has no workspace")

    project_name = resolve_task_project_name(t)
    root = resolve_project_root(name, project_name).resolve()

    rel_path = (path or "").strip()
    if not rel_path:
        raise HTTPException(status_code=400, detail="Query parameter 'path' is required")

    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return candidate


@router.get("/{task_id}/file-content")
async def get_task_file_content(task_id: UUID, path: str):
    """Return a preview of a file produced/modified by a task."""
    from routes.workspaces import _extract_pdf_preview

    candidate = _resolve_task_file(task_id, path)
    is_pdf = candidate.suffix.lower() == ".pdf"
    max_bytes = 5_000_000 if is_pdf else 256_000
    try:
        size = candidate.stat().st_size
        if size > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File is too large to preview ({size} bytes). Limit is {max_bytes} bytes.",
            )
        if is_pdf:
            content = _extract_pdf_preview(candidate)
        else:
            content = candidate.read_text(encoding="utf-8", errors="replace")
        return {
            "path": (path or "").strip(),
            "size": size,
            "content": content,
            "is_pdf": is_pdf,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{task_id}/file-raw")
async def get_task_file_raw(task_id: UUID, path: str):
    """Serve a task file's raw bytes (e.g. for in-browser PDF rendering)."""
    candidate = _resolve_task_file(task_id, path)
    media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    return FileResponse(
        str(candidate),
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{candidate.name}"'},
    )


@router.post("/{task_id}/assign")
async def assign_agent(task_id: UUID, assign: AgentAssign):
    """Assign an agent to a task and start it.

    The policy and the launch live in :func:`tasks.assign.assign_agent_to_task`,
    so the terminal client assigns on exactly the same terms; this maps its
    refusals onto HTTP.
    """
    try:
        return assign_agent_to_task(
            task_id,
            assign.agent_id,
            assign.params,
            task_to_dict=task_to_dict,
        )
    except AssignError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
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
                    session_id = get_or_create_task_session(title=t.title, workspace=t.workspace, task_id=str(task_id))
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
            run_id, session_id = agent_launcher.start_run(str(task_id), t.assigned_agent_type, t.assigned_agent_params)
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
                agent_launcher.start_run(
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
                    run_id=run_id,
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


@router.post("/{task_id}/answer")
async def answer_task(task_id: UUID, payload: TaskAnswer):
    """Answer a task that is paused in the awaiting_input state and resume it.

    The agent that asked is re-run with a resume instruction that carries the
    question and the user's answer; ``build_task_instruction`` additionally folds
    in the prior run output (the question), so the agent continues with full
    context. Clears the pending question and moves the task back to in_progress.
    """
    t = tasks_service.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    if t.status != TaskStatus.awaiting_input:
        raise HTTPException(status_code=400, detail="Task is not awaiting input")

    pending = getattr(t, "pending_question", None) or {}
    agent_id = pending.get("agent_id") or t.assigned_agent_type
    if not agent_id:
        raise HTTPException(status_code=400, detail="No agent recorded for this task to resume")
    if not registry.get_agent(agent_id):
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    question = str(pending.get("question") or "").strip()
    answer = (payload.answer or "").strip()
    if not answer:
        raise HTTPException(status_code=400, detail="Answer must not be empty")

    resume_desc = (
        f'You previously paused this task to ask the user:\n"{question}"\n\n'
        f'The user answered:\n"{answer}"\n\n'
        "Continue the task using this answer. Do not ask the same question again."
    )
    params = {"description": resume_desc}

    # An imported agent that declares a resume endpoint is *continued* rather
    # than re-run. The difference matters for anything holding state between the
    # question and the answer — a graph suspended on a checkpointer comes back
    # to where it stopped, while a fresh run with the answer in its prompt is a
    # different execution that merely reads the same way. Every other agent
    # keeps nothing between runs, so replaying the conversation is the resume,
    # and that path is untouched.
    spec = registry.get_agent(agent_id)
    paused_run = str(pending.get("run_id") or "")
    if spec is not None and spec.is_remote() and (spec.remote or {}).get("resume_path") and paused_run:
        params = {
            "description": f'The user answered: "{answer}"',
            "resume": {
                "run_id": paused_run,
                "value": answer,
                "key": str(pending.get("key") or ""),
            },
        }

    try:
        run_id, session_id = agent_launcher.start_run(str(task_id), agent_id, params)
        tasks_service.assign_agent(task_id, agent_id, params, run_id=run_id)
        tasks_service.update_task(task_id, status=TaskStatus.in_progress, pending_question=None)
        # The task resumes under a fresh run; re-point any run-bound session
        # continuation at it so it still fires when the resumed run finishes.
        try:
            from common.session_service import rebind_continuations_to_run
            rebind_continuations_to_run(str(task_id), run_id)
        except Exception:
            pass
        if session_id:
            try:
                add_event_to_session(session_id, {
                    "type": "user_answer",
                    "agent_id": agent_id,
                    "timestamp": run_manager.utc_now_iso(),
                    "description": f"User answered: {answer}",
                })
            except Exception:
                pass
        updated = tasks_service.get_task(task_id)
        return {"task": task_to_dict(updated), "run_id": run_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    # Derive the task's runs from its session (canonical run list) rather than
    # the assigned_agent_run_id pointer, which is cleared once a run completes.
    runs = tasks_service.get_task_runs(task_id)
    entries = []
    for r in runs:
        tokens = (r.get("process") or {}).get("token_usage") or {}
        entries.append({
            "run_id": r.get("run_id"),
            "agent_id": r.get("agent_id"),
            "status": r.get("status"),
            "channel": r.get("channel"),
            "started_at": r.get("started_at"),
            "finished_at": r.get("finished_at"),
            "model": r.get("model"),
            "error": r.get("error"),
            "inbound_tokens": int(tokens.get("inbound_tokens") or 0),
            "outbound_tokens": int(tokens.get("outbound_tokens") or 0),
            "total_tokens": int(tokens.get("total_tokens") or 0),
        })
    return {"entries": entries}


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
                        "Do not create other high-level tasks. When a subtask must wait for others, "
                        "pass their IDs in the depends parameter of add_subtask so execution order is enforced.\n\n"
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
