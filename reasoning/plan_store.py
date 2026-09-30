"""
Persistent plan tools.

Where the ephemeral ``plan`` tool (see ``reasoning/plan.py``) just echoes a plan
back into the agent's context for the current turn, these tools persist plans
to a reserved ``.plans`` system folder inside the active workspace. A
planning-capable agent can save a structured plan once, then update the status
of individual steps as it executes them — across multiple runs — so the plan
and its progress survive between sessions and are visible to other agents and
the dashboard.

Plans are stored as Markdown at ``.agents_hub/workspaces/<ws>/.plans/<plan_id>.md``
(frontmatter metadata + a checklist body); the tools work on the parsed record
dict, so the on-disk format is transparent to callers.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from langchain_core.tools import tool

from common.workspace_context import resolve_active_workspace
from workspace import (
    create_plan as ws_create_plan,
    get_plan as ws_get_plan,
    list_plans as ws_list_plans,
    update_plan as ws_update_plan,
    update_plan_step as ws_update_plan_step,
    delete_plan as ws_delete_plan,
)


def _json_ok(payload: Dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2)


def _json_err(message: str, *, code: str = "bad_request") -> str:
    return json.dumps({"ok": False, "error": message, "code": code}, ensure_ascii=False, indent=2)


def _require_workspace(explicit: Optional[str]) -> Optional[str]:
    """Resolve the workspace to operate in, or None when none is active."""
    return resolve_active_workspace(explicit)


_NO_WORKSPACE = (
    "No active workspace. Plans are stored per workspace; run inside a "
    "workspace or pass an explicit 'workspace'."
)


class SavePlanInput(BaseModel):
    title: str = Field(..., min_length=1, description="Short title for the plan")
    steps: List[str] = Field(
        ...,
        min_length=1,
        description="Ordered list of step descriptions. Each becomes a trackable step.",
    )
    description: str = Field("", description="Optional context or goal for the plan")
    plan_id: Optional[str] = Field(
        None,
        description="Optional explicit id (slugified). Defaults to a slug of the title.",
    )
    workspace: Optional[str] = Field(None, description="Override the active workspace")


@tool("save_plan", args_schema=SavePlanInput)
def save_plan(
    title: str,
    steps: List[str],
    description: str = "",
    plan_id: Optional[str] = None,
    workspace: Optional[str] = None,
) -> str:
    """Persist a new execution plan to the workspace plan store.

    Use this once you've decided how to approach a multi-step task: it saves the
    plan and its ordered steps so you can track progress across runs. Each step
    starts as 'todo'. Returns JSON with the stored plan (including step ids you
    pass to update_plan_status).
    """
    ws = _require_workspace(workspace)
    if not ws:
        return _json_err(_NO_WORKSPACE, code="no_workspace")
    try:
        record = ws_create_plan(
            ws, title, plan_id=plan_id, description=description, steps=steps
        )
        return _json_ok({"plan": record})
    except ValueError as e:
        return _json_err(str(e), code="conflict")
    except Exception as e:
        return _json_err(f"Failed to save plan: {e}")


class GetPlanInput(BaseModel):
    plan_id: str = Field(..., description="Id of the plan to fetch")
    workspace: Optional[str] = Field(None, description="Override the active workspace")


@tool("get_plan", args_schema=GetPlanInput)
def get_plan(plan_id: str, workspace: Optional[str] = None) -> str:
    """Fetch a stored plan and the current status of each step. Returns JSON."""
    ws = _require_workspace(workspace)
    if not ws:
        return _json_err(_NO_WORKSPACE, code="no_workspace")
    record = ws_get_plan(ws, plan_id)
    if record is None:
        return _json_err(f"Plan '{plan_id}' not found", code="not_found")
    return _json_ok({"plan": record})


class ListPlansInput(BaseModel):
    workspace: Optional[str] = Field(None, description="Override the active workspace")


@tool("list_plans", args_schema=ListPlansInput)
def list_plans(workspace: Optional[str] = None) -> str:
    """List the plans stored in the workspace, with step-completion counts. Returns JSON."""
    ws = _require_workspace(workspace)
    if not ws:
        return _json_err(_NO_WORKSPACE, code="no_workspace")
    records = ws_list_plans(ws)
    summary: List[Dict[str, Any]] = []
    for r in records:
        steps = r.get("steps", []) or []
        done = sum(1 for s in steps if s.get("status") == "done")
        summary.append({
            "id": r.get("id"),
            "title": r.get("title"),
            "status": r.get("status"),
            "steps_total": len(steps),
            "steps_done": done,
            "updated_at": r.get("updated_at"),
        })
    return _json_ok({"workspace": ws, "count": len(summary), "plans": summary})


class UpdatePlanStatusInput(BaseModel):
    plan_id: str = Field(..., description="Id of the plan to update")
    step_id: Optional[int] = Field(
        None,
        description="Step id to update (from save_plan/get_plan). Omit to update the whole plan's status.",
    )
    status: str = Field(
        ...,
        description=(
            "New status. For a step: todo | in_progress | done | blocked | skipped. "
            "For the whole plan (no step_id): active | completed | abandoned."
        ),
    )
    notes: Optional[str] = Field(None, description="Optional note recorded on the step")
    workspace: Optional[str] = Field(None, description="Override the active workspace")


@tool("update_plan_status", args_schema=UpdatePlanStatusInput)
def update_plan_status(
    plan_id: str,
    status: str,
    step_id: Optional[int] = None,
    notes: Optional[str] = None,
    workspace: Optional[str] = None,
) -> str:
    """Update execution status as you make progress.

    Pass a step_id to mark an individual step (e.g. 'in_progress' then 'done'),
    or omit step_id to set the overall plan status ('completed' when finished).
    Returns JSON with the updated plan.
    """
    ws = _require_workspace(workspace)
    if not ws:
        return _json_err(_NO_WORKSPACE, code="no_workspace")
    try:
        if step_id is not None:
            record = ws_update_plan_step(ws, plan_id, step_id, status=status, notes=notes)
            if record is None:
                return _json_err(
                    f"Plan '{plan_id}' or step {step_id} not found", code="not_found"
                )
        else:
            record = ws_update_plan(ws, plan_id, status=status)
            if record is None:
                return _json_err(f"Plan '{plan_id}' not found", code="not_found")
        return _json_ok({"plan": record})
    except ValueError as e:
        return _json_err(str(e))
    except Exception as e:
        return _json_err(f"Failed to update plan: {e}")


class DeletePlanInput(BaseModel):
    plan_id: str = Field(..., description="Id of the plan to delete")
    workspace: Optional[str] = Field(None, description="Override the active workspace")


@tool("delete_plan", args_schema=DeletePlanInput)
def delete_plan(plan_id: str, workspace: Optional[str] = None) -> str:
    """Delete a stored plan once it is no longer needed. Returns JSON."""
    ws = _require_workspace(workspace)
    if not ws:
        return _json_err(_NO_WORKSPACE, code="no_workspace")
    if ws_delete_plan(ws, plan_id):
        return _json_ok({"deleted": plan_id})
    return _json_err(f"Plan '{plan_id}' not found", code="not_found")


__all__ = [
    "save_plan",
    "get_plan",
    "list_plans",
    "update_plan_status",
    "delete_plan",
]
