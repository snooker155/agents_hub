from __future__ import annotations
from typing import Any, Dict, Optional, List

from .models import AgentResult, ToolResult
from .agent import build_agent, run_agent_once, run_task as _runtime_run_task, _collect_steps
from tasks import TaskStore, Task


def _load_task_by_id(task_id: str, tasks_file: Optional[str] = None) -> Optional[Task]:
    store = TaskStore(tasks_file)
    return store.get(task_id)


## Reuse step collection logic from swe_agent.agent to avoid duplication


def run_task(
    task_id: str,
    *,
    tasks_file: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    verbose: bool = False,
    workspace_override: Optional[str] = None,
) -> AgentResult:
    """Run the SWE agent for a single task (by id).
    
    The agent will work within the task's workspace unless workspace_override is provided.
    """
    task = _load_task_by_id(task_id, tasks_file=tasks_file)
    if not task:
        return AgentResult(ok=False, status="not_found", task_id=task_id, error=f"Task {task_id} not found in {tasks_file or 'default storage'}")

    # Determine workspace: use override if provided, otherwise use task's workspace
    workspace = workspace_override or task.workspace
    if not workspace:
        return AgentResult(ok=False, status="error", task_id=task_id, error="Task has no workspace and no --workspace override provided")

    # Resolve simple workspace names under local workspaces/ root
    try:
        from pathlib import Path as _Path
        wp = _Path(workspace)
        if not wp.is_absolute():
            workspace = str((_Path.cwd() / "workspaces" / wp.name).resolve())
    except Exception:
        pass

    print(f"Running task {task_id} in workspace: {workspace}")
    goal = (
        "Реши задачу целиком, используя доступные инструменты для работы с кодом.\n\n"
        f"ID: {task_id}\n"
        f"Название: {task.title}\n"
        f"Описание:\n{task.description}\n\n"
        "Действуй пошагово: читай файлы, меняй код через apply_unified_diff или write_file.\n"
    )

    try:
        # Use the runtime's workspace-aware runner. It returns an AgentResult.
        res = _runtime_run_task(goal, workspace=workspace, verbose=verbose)
        # If runtime returned AgentResult directly, forward it; otherwise, build one.
        if isinstance(res, AgentResult):
            return res
        # Fallback handling if runtime returned a dict-like result
        output = res.get("output", "") if isinstance(res, dict) else str(res)
        steps = _collect_steps(res.get("intermediate_steps")) if isinstance(res, dict) else []
        return AgentResult(ok=True, status="done", task_id=task_id, agent_output=str(output), steps=steps)
    except Exception as e:
        return AgentResult(ok=False, status="error", task_id=task_id, error=str(e))


def run_text(
    text: str,
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    verbose: bool = False,
) -> AgentResult:
    try:
        result = run_agent_once(
            text,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            verbose=verbose,
        )
        output = result.get("output", "") if isinstance(result, dict) else str(result)
        steps = _collect_steps(result.get("intermediate_steps")) if isinstance(result, dict) else []
        return AgentResult(ok=True, status="done", agent_output=str(output), steps=steps)
    except Exception as e:
        return AgentResult(ok=False, status="error", error=str(e))
