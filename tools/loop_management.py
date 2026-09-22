"""
Loop management LangChain tools: create, inspect, modify, delete and validate
iteration loops (the flow-plus-exit-criterion objects persisted by
``loops.store``).

A loop is the case a flow cannot express: a DAG has no back edge, so a flow runs
its nodes once. A loop wraps an existing flow and re-runs it, feeding each pass
the previous pass's output and an evaluator's feedback, until the exit criterion
is met. Two fields carry the design and these tools treat them as such:

* ``flow_id`` — the flow being repeated. It must already exist; a loop never
  creates one (use ``create_flow_tool`` first).
* ``exit_criterion`` — prose handed to the evaluator verbatim. This is the
  contract of the loop, so an empty one is refused rather than defaulted.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

from common.entity_sink import record_entity
from common.workspace_context import (
    normalize_workspace_name,
    resolve_active_workspace,
)
from tools._crud import EntityToolSpec, ToolDef, build_entity_tools, tools_by_id
from tools._json import json_err as _json_err, json_ok as _json_ok


def _coerce_json(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return v


def _simplify(loop) -> Dict[str, Any]:
    d = loop.to_dict()
    return {
        "loop_id": d["loop_id"], "name": d["name"], "description": d["description"],
        "workspace": d["workspace"], "flow_id": d["flow_id"],
        "exit_criterion": d["exit_criterion"],
        "convergence": {
            "max_iterations": d["max_iterations"],
            "min_iterations": d["min_iterations"],
            "target_score": d["target_score"],
            "patience": d["patience"],
            "cost_ceiling": d["cost_ceiling"],
            "max_wall_seconds": d["max_wall_seconds"],
        },
        "evaluator": {
            "mode": d["evaluator_mode"],
            "agent_id": d["evaluator_agent_id"],
            "provider": d["evaluator_provider"],
            "model": d["evaluator_model"],
        },
    }


_CONVERGENCE_FIELDS = ("max_iterations", "min_iterations", "target_score",
                       "patience", "cost_ceiling", "max_wall_seconds")


def _apply_convergence(payload: Dict[str, Any],
                       convergence: Optional[Dict[str, Any]]) -> List[str]:
    unknown: List[str] = []
    for key, value in (convergence or {}).items():
        if key in _CONVERGENCE_FIELDS:
            payload[key] = value
        else:
            unknown.append(key)
    return unknown


def _validate(payload: Dict[str, Any], workspace: Optional[str]) -> Tuple[List[str], List[str]]:
    """Preflight a loop payload. Returns (errors, warnings)."""
    from loops.models import (
        EVALUATOR_MODES, MAX_ITERATIONS_CAP, MAX_WALL_SECONDS_CAP,
    )

    errors: List[str] = []
    warnings: List[str] = []

    if not str(payload.get("name") or "").strip():
        errors.append("name is required")

    if not str(payload.get("exit_criterion") or "").strip():
        errors.append(
            "exit_criterion is required — it is the contract of the loop, handed "
            "to the evaluator verbatim ('the article reads as publishable and "
            "every claim carries a source')"
        )

    flow_id = str(payload.get("flow_id") or "")
    flow = None
    if not flow_id:
        errors.append("flow_id is required — a loop repeats an existing flow")
    else:
        try:
            from flow import store as flow_store
            try:
                flow = flow_store.get_flow(flow_id)
            except flow_store.FlowParseError as e:
                errors.append(f"flow '{flow_id}' is broken: {e}")
            else:
                if flow is None:
                    errors.append(
                        f"flow '{flow_id}' does not exist — create it with "
                        "create_flow_tool, or pick one from list_flows_tool"
                    )
        except Exception as e:  # noqa: BLE001
            warnings.append(f"could not verify flow '{flow_id}': {e}")

    ws = normalize_workspace_name(workspace)
    if flow and ws:
        flow_ws = normalize_workspace_name(flow.get("workspace"))
        if flow_ws and flow_ws != ws:
            warnings.append(
                f"flow '{flow_id}' belongs to workspace '{flow_ws}' but the loop is "
                f"in '{ws}'"
            )

    mode = str(payload.get("evaluator_mode") or "final_agent")
    if mode not in EVALUATOR_MODES:
        errors.append(
            f"unknown evaluator_mode '{mode}' — use one of: {', '.join(EVALUATOR_MODES)}"
        )
    if mode == "agent":
        agent_id = str(payload.get("evaluator_agent_id") or "")
        if not agent_id:
            errors.append("evaluator_mode 'agent' needs evaluator_agent_id")
        else:
            from agents.registry import get_agent as reg_get_agent
            if not reg_get_agent(agent_id):
                errors.append(f"evaluator agent '{agent_id}' is not registered")

    def _num(key: str, default: Any) -> Optional[float]:
        raw = payload.get(key, default)
        if raw in (None, ""):
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            errors.append(f"{key} must be a number")
            return None

    max_iterations = _num("max_iterations", 5)
    min_iterations = _num("min_iterations", 1)
    if max_iterations is not None:
        if max_iterations < 1:
            errors.append("max_iterations must be at least 1")
        elif max_iterations > MAX_ITERATIONS_CAP:
            errors.append(f"max_iterations may not exceed {MAX_ITERATIONS_CAP}")
    if (min_iterations is not None and max_iterations is not None
            and min_iterations > max_iterations):
        errors.append("min_iterations cannot exceed max_iterations")

    target = _num("target_score", None)
    if target is not None and not (0 <= target <= 100):
        errors.append("target_score must be between 0 and 100")

    wall = _num("max_wall_seconds", 3600.0)
    if wall is not None and wall > MAX_WALL_SECONDS_CAP:
        errors.append(f"max_wall_seconds may not exceed {MAX_WALL_SECONDS_CAP}")

    if payload.get("cost_ceiling") in (None, ""):
        warnings.append(
            "no cost_ceiling — a loop is N full flow runs, so an unbounded one is "
            "the most expensive mistake available here"
        )

    return errors, sorted(set(warnings))


# ── input schemas ─────────────────────────────────────────────────────────────

class ListLoopsInput(BaseModel):
    workspace: Optional[str] = Field(
        None, description="Workspace to list; defaults to the active workspace"
    )


class CreateLoopInput(BaseModel):
    name: str = Field(..., min_length=1, description="Short loop name")
    flow_id: str = Field(
        ..., min_length=1,
        description="ID of the flow to repeat (from list_flows_tool). It must already exist."
    )
    exit_criterion: str = Field(
        ..., min_length=1,
        description=(
            "Prose the evaluator judges each pass against, verbatim: "
            "'the article reads as publishable and every claim carries a source'."
        ),
    )
    description: str = Field("", description="What the loop is for")
    convergence: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "When to stop: {'max_iterations': 5, 'min_iterations': 1, "
            "'target_score': 80, 'patience': 2, 'cost_ceiling': 2.0, "
            "'max_wall_seconds': 3600}. patience = consecutive passes that fail to "
            "beat the best score before giving up (0 disables)."
        ),
    )
    evaluator_mode: str = Field(
        "final_agent",
        description=(
            "'final_agent' — the agent on the flow's terminal node reviews its own "
            "team's output. 'agent' — a named reviewer (needs evaluator_agent_id). "
            "'model' — a plain model call with no tools; cheapest."
        ),
    )
    evaluator_agent_id: Optional[str] = Field(
        None, description="The reviewing agent, when evaluator_mode is 'agent'"
    )
    evaluator_provider: Optional[str] = Field(None, description="Provider for the evaluator call")
    evaluator_model: Optional[str] = Field(None, description="Model for the evaluator call")
    workspace: Optional[str] = Field(
        None, description="Workspace to attach the loop to; defaults to the active workspace"
    )

    @field_validator("convergence", mode="before")
    @classmethod
    def coerce(cls, v):
        return _coerce_json(v)


class GetLoopInput(BaseModel):
    loop_id: str = Field(..., min_length=1, description="ID of the loop (from list_loops_tool)")


class ModifyLoopInput(BaseModel):
    loop_id: str = Field(..., min_length=1, description="ID of the loop to modify")
    name: Optional[str] = Field(None, description="New name")
    description: Optional[str] = Field(None, description="New description")
    flow_id: Optional[str] = Field(None, description="Repeat a different flow (must exist)")
    exit_criterion: Optional[str] = Field(None, description="New exit criterion (replaces the old one)")
    convergence: Optional[Dict[str, Any]] = Field(
        None, description="Convergence settings to change; only the keys given are touched"
    )
    evaluator_mode: Optional[str] = Field(None, description="'final_agent', 'agent' or 'model'")
    evaluator_agent_id: Optional[str] = Field(None, description="The reviewing agent, for mode 'agent'")
    evaluator_provider: Optional[str] = Field(None, description="Provider for the evaluator call")
    evaluator_model: Optional[str] = Field(None, description="Model for the evaluator call")

    @field_validator("convergence", mode="before")
    @classmethod
    def coerce(cls, v):
        return _coerce_json(v)


class DeleteLoopInput(BaseModel):
    loop_id: str = Field(..., min_length=1, description="ID of the loop to delete")


class ValidateLoopInput(BaseModel):
    loop_id: Optional[str] = Field(
        None, description="Validate a stored loop by ID. Omit to preflight a proposed design."
    )
    name: Optional[str] = Field(None, description="Proposed name, when no loop_id is given")
    flow_id: Optional[str] = Field(None, description="Proposed flow to repeat")
    exit_criterion: Optional[str] = Field(None, description="Proposed exit criterion")
    convergence: Optional[Dict[str, Any]] = Field(None, description="Proposed convergence settings")
    evaluator_mode: Optional[str] = Field(None, description="Proposed evaluator mode")
    evaluator_agent_id: Optional[str] = Field(None, description="Proposed evaluator agent")
    workspace: Optional[str] = Field(
        None, description="Workspace to check the flow against; defaults to the active one"
    )

    @field_validator("convergence", mode="before")
    @classmethod
    def coerce(cls, v):
        return _coerce_json(v)


# ── handlers ──────────────────────────────────────────────────────────────────

def _list_loops(workspace: Optional[str] = None) -> str:
    """List the iteration loops in a workspace, with the flow each one repeats."""
    from loops import store

    ws = normalize_workspace_name(workspace) or resolve_active_workspace()
    loops = store.list_loops(ws)
    return _json_ok({
        "workspace": ws,
        "count": len(loops),
        "loops": [
            {"loop_id": l.loop_id, "name": l.name, "description": l.description,
             "flow_id": l.flow_id, "exit_criterion": l.exit_criterion,
             "max_iterations": l.max_iterations,
             "evaluator_mode": l.evaluator_mode}
            for l in loops
        ],
    })


def _create_loop(
    name: str,
    flow_id: str,
    exit_criterion: str,
    description: str = "",
    convergence: Optional[Dict[str, Any]] = None,
    evaluator_mode: str = "final_agent",
    evaluator_agent_id: Optional[str] = None,
    evaluator_provider: Optional[str] = None,
    evaluator_model: Optional[str] = None,
    workspace: Optional[str] = None,
) -> str:
    """Create an iteration loop: an existing flow, repeated until a criterion is met.

    The flow must already exist (list_flows_tool / create_flow_tool) and the exit
    criterion must be real prose — it is what the evaluator judges every pass
    against. The loop is validated before saving; on error nothing is written.
    Returns the created loop and its `loop_id`.
    """
    from loops import store
    from loops.models import Loop

    ws = normalize_workspace_name(workspace) or resolve_active_workspace()
    payload: Dict[str, Any] = {
        "name": name.strip(), "description": description or "",
        "workspace": ws, "flow_id": flow_id,
        "exit_criterion": exit_criterion,
        "evaluator_mode": evaluator_mode,
        "evaluator_agent_id": evaluator_agent_id or None,
        "evaluator_provider": evaluator_provider or None,
        "evaluator_model": evaluator_model or None,
    }
    unknown = _apply_convergence(payload, convergence)

    errors, warnings = _validate(payload, ws)
    if errors:
        return _json_err(
            "Loop is invalid and was NOT created. Fix the problems and try again.",
            code="invalid_loop", extra={"errors": errors, "warnings": warnings},
        )

    loop = store.save_loop(Loop.from_dict(payload))
    record_entity("loop", loop.loop_id, "created", loop.name)
    out: Dict[str, Any] = {
        "message": f"Loop '{loop.name}' created successfully",
        "loop_id": loop.loop_id, "loop": _simplify(loop),
    }
    if warnings:
        out["warnings"] = warnings
    if unknown:
        out["ignored_settings"] = unknown
    return _json_ok(out)


def _get_loop(loop_id: str) -> str:
    """Get a loop's full definition: the flow it repeats, its exit criterion,
    convergence settings and evaluator."""
    from loops import store

    loop = store.get_loop(loop_id)
    if not loop:
        return _json_err("Loop not found", code="not_found", extra={"loop_id": loop_id})
    record_entity("loop", loop_id, "viewed", loop.name)
    return _json_ok({"loop": _simplify(loop)})


def _modify_loop(
    loop_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    flow_id: Optional[str] = None,
    exit_criterion: Optional[str] = None,
    convergence: Optional[Dict[str, Any]] = None,
    evaluator_mode: Optional[str] = None,
    evaluator_agent_id: Optional[str] = None,
    evaluator_provider: Optional[str] = None,
    evaluator_model: Optional[str] = None,
) -> str:
    """Change an existing loop. Only the fields you pass are touched.

    `convergence` merges into what is stored, so you can raise the iteration cap
    without restating the target score. The result is validated before saving:
    on error nothing is written.
    """
    from loops import store
    from loops.models import Loop, utc_iso

    existing = store.get_loop(loop_id)
    if not existing:
        return _json_err("Loop not found", code="not_found", extra={"loop_id": loop_id})

    payload = existing.to_dict()
    if name is not None:
        payload["name"] = name.strip()
    if description is not None:
        payload["description"] = description
    if flow_id is not None:
        payload["flow_id"] = flow_id
    if exit_criterion is not None:
        payload["exit_criterion"] = exit_criterion
    if evaluator_mode is not None:
        payload["evaluator_mode"] = evaluator_mode
    if evaluator_agent_id is not None:
        payload["evaluator_agent_id"] = evaluator_agent_id or None
    if evaluator_provider is not None:
        payload["evaluator_provider"] = evaluator_provider or None
    if evaluator_model is not None:
        payload["evaluator_model"] = evaluator_model or None
    unknown = _apply_convergence(payload, convergence)

    errors, warnings = _validate(payload, payload.get("workspace"))
    if errors:
        return _json_err(
            "Loop is invalid and was NOT changed. Fix the problems and try again.",
            code="invalid_loop", extra={"errors": errors, "warnings": warnings},
        )

    payload["updated_at"] = utc_iso()
    loop = store.save_loop(Loop.from_dict(payload))
    record_entity("loop", loop.loop_id, "updated", loop.name)
    out: Dict[str, Any] = {
        "message": f"Loop '{loop.name}' updated successfully",
        "loop_id": loop.loop_id, "loop": _simplify(loop),
    }
    if warnings:
        out["warnings"] = warnings
    if unknown:
        out["ignored_settings"] = unknown
    return _json_ok(out)


def _delete_loop(loop_id: str) -> str:
    """Delete a loop by ID. Refuses while one of its runs is live.

    The flow the loop wrapped is left alone. Deletion is permanent — confirm
    with the user before calling this.
    """
    from loops import store

    loop = store.get_loop(loop_id)
    if not loop:
        return _json_err("Loop not found", code="not_found", extra={"loop_id": loop_id})
    live = [r for r in store.list_runs(loop_id, limit=5)
            if r.status in ("running", "stopping")]
    if live:
        return _json_err(
            f"Loop '{loop_id}' has a live run; stop it before deleting.",
            code="conflict", extra={"loop_run_id": live[0].loop_run_id},
        )
    if not store.delete_loop(loop_id):
        return _json_err("Loop not found", code="not_found", extra={"loop_id": loop_id})
    return _json_ok({
        "message": f"Loop '{loop.name}' deleted successfully", "loop_id": loop_id,
    })


def _validate_loop(
    loop_id: Optional[str] = None,
    name: Optional[str] = None,
    flow_id: Optional[str] = None,
    exit_criterion: Optional[str] = None,
    convergence: Optional[Dict[str, Any]] = None,
    evaluator_mode: Optional[str] = None,
    evaluator_agent_id: Optional[str] = None,
    workspace: Optional[str] = None,
) -> str:
    """Validate a loop without saving anything.

    Pass `loop_id` to check a stored loop, or a proposed flow_id +
    exit_criterion to preflight a design. Checks: the flow exists, the criterion
    is present, the evaluator mode is known and its agent registered, and every
    ceiling is inside the hard caps. Returns `valid` plus `errors` (blocking)
    and `warnings` (advisory).
    """
    ws = normalize_workspace_name(workspace) or resolve_active_workspace()

    if loop_id:
        from loops import store
        loop = store.get_loop(loop_id)
        if not loop:
            return _json_err("Loop not found", code="not_found", extra={"loop_id": loop_id})
        payload = loop.to_dict()
        ws = normalize_workspace_name(payload.get("workspace")) or ws
    elif flow_id or exit_criterion:
        payload = {
            "name": name or "proposed", "flow_id": flow_id or "",
            "exit_criterion": exit_criterion or "",
            "evaluator_mode": evaluator_mode or "final_agent",
            "evaluator_agent_id": evaluator_agent_id or None,
        }
        _apply_convergence(payload, convergence)
    else:
        return _json_err(
            "Provide either loop_id or flow_id/exit_criterion to validate", code="invalid"
        )

    errors, warnings = _validate(payload, ws)
    return _json_ok({"valid": not errors, "errors": errors, "warnings": warnings})


# ── tools ─────────────────────────────────────────────────────────────────────

_SPEC = EntityToolSpec(
    singular="loop",
    plural="loops",
    list=ToolDef("list_loops_tool", ListLoopsInput, _list_loops, "Failed to list loops"),
    create=ToolDef("create_loop_tool", CreateLoopInput, _create_loop, "Failed to create loop"),
    get=ToolDef("get_loop_tool", GetLoopInput, _get_loop, "Failed to get loop"),
    modify=ToolDef("modify_loop_tool", ModifyLoopInput, _modify_loop, "Failed to modify loop"),
    delete=ToolDef("delete_loop_tool", DeleteLoopInput, _delete_loop, "Failed to delete loop"),
    validate=ToolDef("validate_loop_tool", ValidateLoopInput, _validate_loop, "Failed to validate loop"),
)

_TOOLS = tools_by_id(build_entity_tools(_SPEC))
list_loops_tool = _TOOLS["list_loops_tool"]
create_loop_tool = _TOOLS["create_loop_tool"]
get_loop_tool = _TOOLS["get_loop_tool"]
modify_loop_tool = _TOOLS["modify_loop_tool"]
delete_loop_tool = _TOOLS["delete_loop_tool"]
validate_loop_tool = _TOOLS["validate_loop_tool"]

LOOP_MANAGEMENT_TOOLS = [
    list_loops_tool,
    create_loop_tool,
    get_loop_tool,
    modify_loop_tool,
    delete_loop_tool,
    validate_loop_tool,
]

__all__ = [
    "list_loops_tool",
    "create_loop_tool",
    "get_loop_tool",
    "modify_loop_tool",
    "delete_loop_tool",
    "validate_loop_tool",
    "LOOP_MANAGEMENT_TOOLS",
]
