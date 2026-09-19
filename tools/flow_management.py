"""
Flow management LangChain tools: create, inspect, modify, delete, and
validate agent flows (the multi-agent pipelines persisted by ``flow.store``).

These power the ``flow_creator`` agent (and any agent granted the
``flow_management`` tool group). Node/edge inputs use the simple logical
shape an LLM designer reasons about::

    nodes: [{"id": "node-1", "agent_id": "researcher", "label": "Research",
             "description": "Gather sources"}]
    edges: [{"source": "node-1", "target": "node-2"}]

The tools convert this into the combined flow dict ``flow.store`` persists
(visual positions are auto-laid-out left-to-right) and run the same preflight
checks as the flow engine (``flow.validate``) before saving anything, so an
invalid graph is rejected with actionable errors instead of being stored.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from uuid import uuid4

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

from common.entity_sink import record_entity
from common.workspace_context import (
    filter_agents_for_workspace,
    normalize_workspace_name,
    resolve_active_workspace,
)


def _json_ok(payload: Dict[str, object]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2)


def _json_err(message: str, *, code: str = "bad_request", extra: Optional[Dict[str, Any]] = None) -> str:
    body: Dict[str, Any] = {"ok": False, "error": message, "code": code}
    if extra:
        body.update(extra)
    return json.dumps(body, ensure_ascii=False, indent=2)


def _coerce_json_list(v: Any) -> Any:
    """Accept a JSON-encoded string for list parameters (some models pass strings)."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return v


# ── graph shape conversion ────────────────────────────────────────────────────

def _to_combined_nodes(nodes: List[Dict[str, Any]], existing: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Convert logical nodes to the combined dict shape flow.store persists.

    Positions: keep the position of an existing node with the same id (so a
    modify doesn't scramble the user's layout), else lay out left-to-right.
    """
    positions = {
        n.get("id"): n.get("position")
        for n in (existing or [])
        if isinstance(n, dict) and n.get("position")
    }
    out: List[Dict[str, Any]] = []
    for i, n in enumerate(nodes):
        nid = str(n.get("id") or f"node-{i + 1}")
        agent_id = str(n.get("agent_id") or n.get("entity_id") or "")
        data: Dict[str, Any] = {
            "label": n.get("label") or agent_id or nid,
            "agent_id": agent_id,
            "description": n.get("description") or "",
        }
        if n.get("entity_id") and not n.get("agent_id"):
            data.pop("agent_id", None)
            data["entity_id"] = str(n["entity_id"])
        if n.get("category"):
            data["category"] = n["category"]
        for f in ("input", "inputs", "output", "outputs", "config"):
            if n.get(f) is not None:
                data[f] = n[f]
        out.append({
            "id": nid,
            "type": "flowNode",
            "position": positions.get(nid) or {"x": 120 + i * 220, "y": 160},
            "data": data,
        })
    return out


def _to_combined_edges(edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, e in enumerate(edges):
        s, t = e.get("source"), e.get("target")
        out.append({
            "id": e.get("id") or f"edge-{i + 1}",
            "source": s,
            "target": t,
            "type": e.get("type") or "smoothstep",
        })
    return out


def _simplify(flow: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a combined flow dict to the logical view tool outputs report."""
    nodes = []
    for n in flow.get("nodes", []):
        data = n.get("data", {}) if isinstance(n.get("data"), dict) else {}
        nodes.append({
            "id": n.get("id"),
            "agent_id": data.get("agent_id") or data.get("entity_id") or "",
            "label": data.get("label") or "",
            "description": data.get("description") or "",
        })
    edges = [{"source": e.get("source"), "target": e.get("target")} for e in flow.get("edges", [])]
    return {
        "id": flow.get("id"),
        "name": flow.get("name"),
        "description": flow.get("description") or "",
        "workspace": flow.get("workspace"),
        "nodes": nodes,
        "edges": edges,
    }


# ── validation ────────────────────────────────────────────────────────────────

def _validate_combined(flow: Dict[str, Any], workspace: Optional[str]) -> tuple[List[str], List[str]]:
    """Run the engine's preflight on a combined flow dict.

    Returns (errors, warnings). Errors block saving; warnings (e.g. an agent
    not enabled in the target workspace) are surfaced but non-blocking.
    """
    from flow.validate import FlowValidationError, resolve_entities, validate_flow

    errors: List[str] = []
    warnings: List[str] = []

    try:
        validate_flow(flow)
    except FlowValidationError as e:
        errors.extend(e.errors)

    try:
        resolve_entities(flow.get("nodes", []))
    except FlowValidationError as e:
        errors.extend(e.errors)

    ws = normalize_workspace_name(workspace)
    if ws:
        from agents.registry import list_agents as reg_list_agents
        allowed_ids = {
            getattr(s, "id", None)
            for s in filter_agents_for_workspace(reg_list_agents(), ws)
        }
        all_ids = {getattr(s, "id", None) for s in reg_list_agents()}
        for n in flow.get("nodes", []):
            data = n.get("data", {}) if isinstance(n.get("data"), dict) else {}
            aid = data.get("agent_id") or n.get("agent_id")
            if aid and aid in all_ids and aid not in allowed_ids:
                warnings.append(
                    f"agent '{aid}' is not enabled in workspace '{ws}' "
                    "(add it to the workspace's allowed agents before running)"
                )

    return errors, sorted(set(warnings))


# ── tools ─────────────────────────────────────────────────────────────────────

class CreateFlowInput(BaseModel):
    name: str = Field(..., min_length=1, description="Short flow name")
    description: str = Field("", description="What the flow achieves")
    nodes: List[Dict[str, Any]] = Field(
        ...,
        description=(
            "Execution steps: [{'id': 'node-1', 'agent_id': '<registered agent id>', "
            "'label': '<display label>', 'description': '<role of this step>'}]"
        ),
    )
    edges: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Execution order: [{'source': 'node-1', 'target': 'node-2'}]. The graph must be acyclic.",
    )
    workspace: Optional[str] = Field(
        None, description="Workspace to attach the flow to; defaults to the active workspace"
    )

    @field_validator("nodes", "edges", mode="before")
    @classmethod
    def coerce_lists(cls, v):
        return _coerce_json_list(v)


@tool("create_flow_tool", args_schema=CreateFlowInput)
def create_flow_tool(
    name: str,
    nodes: List[Dict[str, Any]],
    description: str = "",
    edges: Optional[List[Dict[str, Any]]] = None,
    workspace: Optional[str] = None,
) -> str:
    """Create a new agent flow from nodes (agent steps) and edges (execution order).

    Every node's agent_id must be a registered agent (check with
    list_agents_tool first). The graph is validated before saving — unknown
    agents, dangling edges, or cycles are rejected with the list of problems.
    Returns the created flow (including its `flow_id`) on success.
    """
    try:
        from flow import store as flow_store
        from datetime import datetime, timezone

        ws = normalize_workspace_name(workspace) or resolve_active_workspace()
        now = datetime.now(timezone.utc).isoformat()
        flow: Dict[str, Any] = {
            "id": str(uuid4()),
            "name": name.strip(),
            "description": description or "",
            "nodes": _to_combined_nodes(nodes or []),
            "edges": _to_combined_edges(edges or []),
            "workspace": ws,
            "task_id": None,
            "created_at": now,
            "updated_at": now,
        }

        errors, warnings = _validate_combined(flow, ws)
        if errors:
            return _json_err(
                "Flow is invalid and was NOT created. Fix the problems and try again.",
                code="invalid_flow",
                extra={"errors": errors, "warnings": warnings},
            )

        flow_store.save_flow(flow)
        record_entity("flow", flow["id"], "created", flow["name"])
        payload: Dict[str, Any] = {
            "message": f"Flow '{flow['name']}' created successfully",
            "flow_id": flow["id"],
            "flow": _simplify(flow),
        }
        if warnings:
            payload["warnings"] = warnings
        return _json_ok(payload)
    except Exception as e:
        return _json_err(f"Failed to create flow: {e}")


class GetFlowInput(BaseModel):
    flow_id: str = Field(..., min_length=1, description="ID of the flow to retrieve (from list_flows_tool)")


@tool("get_flow_tool", args_schema=GetFlowInput)
def get_flow_tool(flow_id: str) -> str:
    """Get a flow's full definition: name, description, nodes, and edges."""
    try:
        from flow import store as flow_store

        try:
            flow = flow_store.get_flow(flow_id)
        except flow_store.FlowParseError as e:
            return _json_err(f"Flow is broken: {e}", code="invalid_flow", extra={"flow_id": flow_id})
        if flow is None:
            return _json_err("Flow not found", code="not_found", extra={"flow_id": flow_id})
        record_entity("flow", flow_id, "viewed", flow.get("name") or "")
        return _json_ok({"flow": _simplify(flow), "entry_point": flow.get("entry_point")})
    except Exception as e:
        return _json_err(f"Failed to get flow: {e}")


class ModifyFlowInput(BaseModel):
    flow_id: str = Field(..., min_length=1, description="ID of the existing flow to modify")
    name: Optional[str] = Field(None, description="New flow name")
    description: Optional[str] = Field(None, description="New flow description")
    nodes: Optional[List[Dict[str, Any]]] = Field(
        None,
        description=(
            "Replacement node list (same shape as create_flow_tool). Replaces ALL "
            "nodes — include every node the flow should keep. Provide edges too "
            "when node ids change."
        ),
    )
    edges: Optional[List[Dict[str, Any]]] = Field(
        None, description="Replacement edge list: [{'source': ..., 'target': ...}]"
    )

    @field_validator("nodes", "edges", mode="before")
    @classmethod
    def coerce_lists(cls, v):
        return _coerce_json_list(v)


@tool("modify_flow_tool", args_schema=ModifyFlowInput)
def modify_flow_tool(
    flow_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    nodes: Optional[List[Dict[str, Any]]] = None,
    edges: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Modify an existing flow. Only the provided fields change.

    `nodes`/`edges` are full replacements of the graph, not patches — use
    get_flow_tool first and resubmit the complete updated lists. The resulting
    graph is validated before saving; an invalid result leaves the stored flow
    untouched.
    """
    try:
        from flow import store as flow_store
        from datetime import datetime, timezone

        try:
            flow = flow_store.get_flow(flow_id)
        except flow_store.FlowParseError as e:
            return _json_err(f"Flow is broken: {e}", code="invalid_flow", extra={"flow_id": flow_id})
        if flow is None:
            return _json_err("Flow not found", code="not_found", extra={"flow_id": flow_id})

        changed: List[str] = []
        if name is not None and name.strip():
            flow["name"] = name.strip()
            changed.append("name")
        if description is not None:
            flow["description"] = description
            changed.append("description")
        if nodes is not None:
            flow["nodes"] = _to_combined_nodes(nodes, existing=flow.get("nodes"))
            changed.append("nodes")
        if edges is not None:
            flow["edges"] = _to_combined_edges(edges)
            changed.append("edges")

        if not changed:
            return _json_ok({"flow": _simplify(flow), "message": f"Flow '{flow_id}' unchanged"})

        errors, warnings = _validate_combined(flow, flow.get("workspace"))
        if errors:
            return _json_err(
                "Modified flow is invalid; the stored flow was NOT changed.",
                code="invalid_flow",
                extra={"errors": errors, "warnings": warnings},
            )

        flow["updated_at"] = datetime.now(timezone.utc).isoformat()
        flow_store.save_flow(flow)
        record_entity("flow", flow_id, "updated", flow.get("name") or "")
        payload: Dict[str, Any] = {
            "message": f"Flow '{flow['name']}' modified successfully",
            "changed": changed,
            "flow": _simplify(flow),
        }
        if warnings:
            payload["warnings"] = warnings
        return _json_ok(payload)
    except Exception as e:
        return _json_err(f"Failed to modify flow: {e}")


class DeleteFlowInput(BaseModel):
    flow_id: str = Field(..., min_length=1, description="ID of the flow to delete")


@tool("delete_flow_tool", args_schema=DeleteFlowInput)
def delete_flow_tool(flow_id: str) -> str:
    """Delete a flow by ID. Refuses while an instance of the flow is running.

    Deletion is permanent — confirm with the user before calling this.
    """
    try:
        from flow import store as flow_store

        try:
            flow = flow_store.get_flow(flow_id)
        except flow_store.FlowParseError:
            flow = None  # broken flows can still be deleted
        if flow is not None and flow.get("running"):
            return _json_err(
                f"Flow '{flow_id}' has a running instance; stop it before deleting.",
                code="conflict",
            )
        if not flow_store.delete_flow(flow_id):
            return _json_err("Flow not found", code="not_found", extra={"flow_id": flow_id})
        return _json_ok({"message": f"Flow '{flow_id}' deleted successfully", "flow_id": flow_id})
    except Exception as e:
        return _json_err(f"Failed to delete flow: {e}")


class ValidateFlowInput(BaseModel):
    flow_id: Optional[str] = Field(
        None, description="Validate a stored flow by ID. Omit to validate a proposed nodes/edges graph instead."
    )
    nodes: Optional[List[Dict[str, Any]]] = Field(
        None, description="Proposed nodes to validate (same shape as create_flow_tool), when no flow_id is given"
    )
    edges: Optional[List[Dict[str, Any]]] = Field(
        None, description="Proposed edges to validate, when no flow_id is given"
    )
    workspace: Optional[str] = Field(
        None, description="Workspace to check agent availability against; defaults to the active workspace"
    )

    @field_validator("nodes", "edges", mode="before")
    @classmethod
    def coerce_lists(cls, v):
        return _coerce_json_list(v)


@tool("validate_flow_tool", args_schema=ValidateFlowInput)
def validate_flow_tool(
    flow_id: Optional[str] = None,
    nodes: Optional[List[Dict[str, Any]]] = None,
    edges: Optional[List[Dict[str, Any]]] = None,
    workspace: Optional[str] = None,
) -> str:
    """Validate a flow without saving anything.

    Pass `flow_id` to check a stored flow, or `nodes`+`edges` to preflight a
    design before creating it. Checks: node ids present and unique, every
    agent_id registered, edges reference existing nodes, graph is acyclic.
    Returns `valid` plus `errors` (blocking) and `warnings` (advisory).
    """
    try:
        ws = normalize_workspace_name(workspace) or resolve_active_workspace()

        if flow_id:
            from flow import store as flow_store
            try:
                flow = flow_store.get_flow(flow_id)
            except flow_store.FlowParseError as e:
                return _json_ok({"valid": False, "errors": [str(e)], "warnings": []})
            if flow is None:
                return _json_err("Flow not found", code="not_found", extra={"flow_id": flow_id})
            ws = normalize_workspace_name(flow.get("workspace")) or ws
        elif nodes is not None:
            flow = {
                "id": "proposed",
                "nodes": _to_combined_nodes(nodes),
                "edges": _to_combined_edges(edges or []),
            }
        else:
            return _json_err("Provide either flow_id or nodes/edges to validate", code="invalid")

        errors, warnings = _validate_combined(flow, ws)
        return _json_ok({"valid": not errors, "errors": errors, "warnings": warnings})
    except Exception as e:
        return _json_err(f"Failed to validate flow: {e}")


__all__ = [
    "create_flow_tool",
    "get_flow_tool",
    "modify_flow_tool",
    "delete_flow_tool",
    "validate_flow_tool",
]
