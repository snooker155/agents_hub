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
from common.workspace_scope import check_record
from tools._crud import EntityToolSpec, ToolDef, build_entity_tools, tools_by_id
from tools._json import json_err as _json_err, json_ok as _json_ok


def _coerce_json_list(v: Any) -> Any:
    """Accept a JSON-encoded string for list parameters (some models pass strings)."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return v


# ── graph shape conversion ────────────────────────────────────────────────────

#: Node fields copied through as they are: the step's state keys and config,
#: its retry and timeout, and how it picks the next step (flow/branching.py).
_PASSTHROUGH_FIELDS = ("input", "inputs", "output", "outputs", "config", "retry",
                       "timeout_seconds", "route", "route_default", "route_hint")


def _layout(nodes: List[Dict[str, Any]], edges: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, int]]:
    """Positions by depth: a column per step of the longest path into a node,
    the nodes of one column stacked, so parallel branches sit side by side."""
    ids = [str(n.get("id") or f"node-{i + 1}") for i, n in enumerate(nodes)]
    preds: Dict[str, List[str]] = {i: [] for i in ids}
    for e in edges or []:
        s, t = str(e.get("source") or ""), str(e.get("target") or "")
        if s in preds and t in preds:
            preds[t].append(s)
    depth: Dict[str, int] = {}

    def _depth(nid: str, seen: frozenset = frozenset()) -> int:
        if nid in depth:
            return depth[nid]
        if nid in seen:
            return 0
        value = 1 + max((_depth(p, seen | {nid}) for p in preds[nid]), default=-1)
        depth[nid] = value
        return value

    rows: Dict[int, int] = {}
    out: Dict[str, Dict[str, int]] = {}
    for nid in ids:
        col = _depth(nid)
        row = rows.get(col, 0)
        rows[col] = row + 1
        out[nid] = {"x": 120 + col * 240, "y": 120 + row * 150}
    return out


def _to_combined_nodes(nodes: List[Dict[str, Any]], existing: Optional[List[Dict[str, Any]]] = None,
                       edges: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Convert logical nodes to the combined dict shape flow.store persists.

    Positions: keep the position of an existing node with the same id (so a
    modify doesn't scramble the user's layout), else lay out by depth when the
    edges are given, left-to-right when not. A node's ``task`` becomes the
    step's own task (``nodeTask``, what the engine adds to its prompt).
    """
    positions = {
        n.get("id"): n.get("position")
        for n in (existing or [])
        if isinstance(n, dict) and n.get("position")
    }
    laid_out = _layout(nodes, edges) if edges is not None else {}
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
        for f in _PASSTHROUGH_FIELDS:
            if n.get(f) is not None:
                data[f] = n[f]
        task = n.get("task") or n.get("nodeTask")
        if task:
            data["nodeTask"] = str(task)
        out.append({
            "id": nid,
            "type": "flowNode",
            "position": positions.get(nid) or laid_out.get(nid) or {"x": 120 + i * 220, "y": 160},
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
        node = {
            "id": n.get("id"),
            "agent_id": data.get("agent_id") or data.get("entity_id") or "",
            "label": data.get("label") or "",
            "description": data.get("description") or "",
        }
        if data.get("nodeTask"):
            node["task"] = data["nodeTask"]
        for f in ("route", "route_default", "config", "output"):
            if data.get(f) not in (None, "", [], {}):
                node[f] = data[f]
        nodes.append(node)
    edges = [{"source": e.get("source"), "target": e.get("target")} for e in flow.get("edges", [])]
    out = {
        "id": flow.get("id"),
        "name": flow.get("name"),
        "description": flow.get("description") or "",
        "workspace": flow.get("workspace"),
        "nodes": nodes,
        "edges": edges,
    }
    if flow.get("ephemeral"):
        out["ephemeral"] = True
    return out


# ── workspace scope ───────────────────────────────────────────────────────────

def _hidden(flow: Optional[Dict[str, Any]], *, write: bool = False) -> bool:
    """Whether ``flow`` is out of this run's reach: a flow of another
    workspace is answered like a missing one (``check_record``). A flow with
    no workspace is shared by every workspace for reading (``list_flows_tool``
    lists it everywhere) and is the default workspace's to change."""
    if flow is None:
        return False
    flow_ws = flow.get("workspace")
    if not flow_ws and not write:
        return False
    return check_record(flow_ws, what="flow") is not None


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
        all_specs = reg_list_agents()
        all_ids = {getattr(s, "id", None) for s in all_specs}
        # Another workspace's own agent (owner_workspace elsewhere, not
        # shared) does not exist from here: a flow that ran it would run that
        # workspace's agent in this one.
        foreign = {
            getattr(s, "id", None) for s in all_specs
            if getattr(s, "owner_workspace", None) and not getattr(s, "shared", False)
            and check_record(s.owner_workspace, what="agent", workspace=ws)
        }
        for n in flow.get("nodes", []):
            data = n.get("data", {}) if isinstance(n.get("data"), dict) else {}
            aid = data.get("agent_id") or n.get("agent_id")
            if aid and aid in foreign:
                errors.append(f"agent '{aid}' is not registered")
            elif aid and aid in all_ids and aid not in allowed_ids:
                warnings.append(
                    f"agent '{aid}' is not enabled in workspace '{ws}' "
                    "(add it to the workspace's allowed agents before running)"
                )

    return errors, sorted(set(warnings))


# ── input schemas ─────────────────────────────────────────────────────────────

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


class GetFlowInput(BaseModel):
    flow_id: str = Field(..., min_length=1, description="ID of the flow to retrieve (from list_flows_tool)")


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
    keep: Optional[bool] = Field(
        None,
        description=(
            "True keeps a temporary flow built by run_new_flow_tool: it joins the "
            "flow list and is no longer deleted after a week. Only when the user asks."
        ),
    )

    @field_validator("nodes", "edges", mode="before")
    @classmethod
    def coerce_lists(cls, v):
        return _coerce_json_list(v)


class DeleteFlowInput(BaseModel):
    flow_id: str = Field(..., min_length=1, description="ID of the flow to delete")


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


# ── handlers ──────────────────────────────────────────────────────────────────
#
# One handler per tool: the body the old @tool-decorated function ran inside
# its try block. build_entity_tools (tools/_crud.py) supplies the decorator,
# the args_schema wiring and the outer try/except.

def _create_flow(
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
    from flow import store as flow_store
    from datetime import datetime, timezone

    ws = normalize_workspace_name(workspace) or resolve_active_workspace()
    now = datetime.now(timezone.utc).isoformat()
    flow: Dict[str, Any] = {
        "id": str(uuid4()),
        "name": name.strip(),
        "description": description or "",
        "nodes": _to_combined_nodes(nodes or [], edges=edges or []),
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


def _raw_flow(flow_id: str):
    """The stored record of a flow, parsed or not: a broken flow still names
    its workspace, which decides whether this run may even learn it exists."""
    from flow import store as flow_store
    raw = getattr(flow_store, "_FLOWS", {}).get(flow_id)
    return raw if isinstance(raw, dict) else None


def _get_flow(flow_id: str) -> str:
    """Get a flow's full definition: name, description, nodes, and edges."""
    from flow import store as flow_store

    try:
        flow = flow_store.get_flow(flow_id)
    except flow_store.FlowParseError as e:
        if _hidden(_raw_flow(flow_id)):
            return _json_err("Flow not found", code="not_found", extra={"flow_id": flow_id})
        return _json_err(f"Flow is broken: {e}", code="invalid_flow", extra={"flow_id": flow_id})
    if flow is None or _hidden(flow):
        return _json_err("Flow not found", code="not_found", extra={"flow_id": flow_id})
    record_entity("flow", flow_id, "viewed", flow.get("name") or "")
    return _json_ok({"flow": _simplify(flow), "entry_point": flow.get("entry_point")})


def _modify_flow(
    flow_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    nodes: Optional[List[Dict[str, Any]]] = None,
    edges: Optional[List[Dict[str, Any]]] = None,
    keep: Optional[bool] = None,
) -> str:
    """Modify an existing flow. Only the provided fields change.

    `nodes`/`edges` are full replacements of the graph, not patches — use
    get_flow_tool first and resubmit the complete updated lists. The resulting
    graph is validated before saving; an invalid result leaves the stored flow
    untouched.
    """
    from flow import store as flow_store
    from datetime import datetime, timezone

    try:
        flow = flow_store.get_flow(flow_id)
    except flow_store.FlowParseError as e:
        if _hidden(_raw_flow(flow_id), write=True):
            return _json_err("Flow not found", code="not_found", extra={"flow_id": flow_id})
        return _json_err(f"Flow is broken: {e}", code="invalid_flow", extra={"flow_id": flow_id})
    if flow is None or _hidden(flow, write=True):
        return _json_err("Flow not found", code="not_found", extra={"flow_id": flow_id})

    changed: List[str] = []
    if name is not None and name.strip():
        flow["name"] = name.strip()
        changed.append("name")
    if description is not None:
        flow["description"] = description
        changed.append("description")
    if nodes is not None:
        flow["nodes"] = _to_combined_nodes(nodes, existing=flow.get("nodes"), edges=edges)
        changed.append("nodes")
    if edges is not None:
        flow["edges"] = _to_combined_edges(edges)
        changed.append("edges")
    if keep and flow.get("ephemeral"):
        flow["ephemeral"] = False
        changed.append("kept")

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


def _delete_flow(flow_id: str) -> str:
    """Delete a flow by ID. Refuses while an instance of the flow is running.

    Deletion is permanent — confirm with the user before calling this.
    """
    from flow import store as flow_store

    try:
        flow = flow_store.get_flow(flow_id)
    except flow_store.FlowParseError:
        flow = None  # broken flows can still be deleted
    # A broken flow still names its workspace in the stored record.
    raw = flow if flow is not None else _raw_flow(flow_id)
    if _hidden(raw, write=True):
        return _json_err("Flow not found", code="not_found", extra={"flow_id": flow_id})
    if flow is not None and flow.get("running"):
        return _json_err(
            f"Flow '{flow_id}' has a running instance; stop it before deleting.",
            code="conflict",
        )
    if not flow_store.delete_flow(flow_id):
        return _json_err("Flow not found", code="not_found", extra={"flow_id": flow_id})
    return _json_ok({"message": f"Flow '{flow_id}' deleted successfully", "flow_id": flow_id})


def _validate_flow(
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
    ws = normalize_workspace_name(workspace) or resolve_active_workspace()

    if flow_id:
        from flow import store as flow_store
        try:
            flow = flow_store.get_flow(flow_id)
        except flow_store.FlowParseError as e:
            return _json_ok({"valid": False, "errors": [str(e)], "warnings": []})
        if flow is None or _hidden(flow):
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


# ── build and run in one call ─────────────────────────────────────────────────
#
# run_new_flow_tool is the agent's way to orchestrate work it planned itself:
# the graph is assembled for this request, validated, checked against what the
# caller may delegate to, and started, without first becoming an entry in the
# flow list. It is stored (the run loads its flow by id and the run page shows
# it) as a temporary flow, which no list shows and which is deleted a week after
# its last change unless the person keeps it (flow.store.is_ephemeral).

#: Nodes that start other runs (a team, a loop, a flow, an n8n workflow). Their
#: members are not checked against the caller's delegation allowlist, so an
#: agent with an allowlist may not put one in a flow it builds.
_NESTING_ENTITIES = frozenset({"run_flow", "run_team", "run_loop", "run_n8n"})

_NODE_SHAPES = (
    "Each node is one of: an agent step {'id', 'agent_id', 'label', 'task'} where task is "
    "what this step does; a map step {'id', 'entity_id': 'map', 'label', 'config': "
    "{'agent_id', 'task', 'over'?, 'max_parallel'?, 'allow_failures'?}, 'output': ['key']} "
    "that runs config.agent_id once per item of a list (the previous step's output read as a "
    "JSON list or one item per line, or state key 'over'), all items in parallel, and writes "
    "the results as a list to its output key; a switch {'id', 'entity_id': 'state_switch', "
    "'config': {'key', 'cases': [{'op', 'value', 'target'}], 'default'}} that follows the "
    "first case whose condition on state key 'key' holds (op: equals, contains, matches, gt, "
    "lt, count_gt, empty, ...). An agent step with two or more next steps may add "
    "'route': 'one' (or 'many') to choose the next step itself; 'route_default' names the "
    "step taken when it does not choose. An agent step may write its answer to state with "
    "'output': ['key'] and read keys with 'input': ['key']."
)


class RunNewFlowInput(BaseModel):
    name: str = Field(..., min_length=1, description="Short name of what this run does")
    goal: str = Field(
        ..., min_length=1,
        description="The request the flow works on: every first step reads it.",
    )
    nodes: List[Dict[str, Any]] = Field(..., description="The steps. " + _NODE_SHAPES)
    edges: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=("Order: [{'source': 'a', 'target': 'b'}]. Acyclic. Steps with no edge "
                     "between them run at the same time."),
    )
    max_parallel: Optional[int] = Field(
        None, description="How many steps may run at once (default 4)")
    on_error: Optional[str] = Field(
        None, description="fail_fast (default), continue, or isolate_branch")
    save: bool = Field(
        False,
        description=("True keeps the flow in the flow list for reuse. Default False: a "
                     "temporary flow, deleted a week after the run unless the user keeps it."),
    )
    create_task: bool = Field(
        False, description="True only when the user asks to track this run as a task.")
    user_approved: bool = Field(
        False,
        description=(
            "Must be True to start. Show the user the plan (the steps and the estimate the "
            "first call returns) and set it only after they said yes. In auto mode the "
            "call runs without it."
        ),
    )

    @field_validator("nodes", "edges", mode="before")
    @classmethod
    def coerce_lists(cls, v):
        return _coerce_json_list(v)


def _referenced_agents(flow: Dict[str, Any]) -> List[str]:
    """Every agent the flow runs: its agent steps and its map steps' agents."""
    out: List[str] = []
    for n in flow.get("nodes", []):
        data = n.get("data", {}) if isinstance(n.get("data"), dict) else {}
        aid = data.get("agent_id")
        if not aid and data.get("entity_id") == "map":
            aid = (data.get("config") or {}).get("agent_id")
        if aid and str(aid) not in out:
            out.append(str(aid))
    return out


def _delegation_errors(flow: Dict[str, Any], ws: Optional[str]) -> List[str]:
    """What the caller may not run through this flow: an agent it may not
    delegate to (its ``delegates`` allowlist, the self-delegation rule), an
    agent not available in the workspace, and, for a caller with an
    allowlist, a step that starts other runs whose members it cannot see."""
    from agents.registry import get_agent as reg_get_agent
    from tools.langchain_tools import _agent_available, _caller_delegates, _delegation_blocked

    errors: List[str] = []
    for aid in _referenced_agents(flow):
        spec = reg_get_agent(aid)
        if spec is None:
            continue  # the preflight already names an unknown agent
        if not _agent_available(spec, ws):
            errors.append(f"agent '{aid}' is not available in workspace '{ws}'")
            continue
        blocked = _delegation_blocked(aid)
        if blocked:
            errors.append(blocked)
    if _caller_delegates() is not None:
        for n in flow.get("nodes", []):
            data = n.get("data", {}) if isinstance(n.get("data"), dict) else {}
            if data.get("entity_id") in _NESTING_ENTITIES:
                errors.append(
                    f"step '{n.get('id')}' starts another run ({data['entity_id']}), which an "
                    "agent with a delegation allowlist may not put in a flow it builds")
    return errors


def _estimate(flow: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The cost of one run, with the map steps priced per item."""
    try:
        from flow.estimate import estimate_flow_cost
        estimate = estimate_flow_cost(flow)
    except Exception:  # noqa: BLE001 - a run without an estimate is still a run
        return None
    maps = [n.get("id") for n in flow.get("nodes", [])
            if (n.get("data") or {}).get("entity_id") == "map"]
    if maps:
        estimate["note"] = (
            "Map steps (" + ", ".join(str(m) for m in maps) + ") run their agent once per item; "
            "the number of items is known only when the run gets there, so they are not in "
            "the total.")
    return estimate


def run_new_flow(
    name: str,
    goal: str,
    nodes: List[Dict[str, Any]],
    edges: Optional[List[Dict[str, Any]]] = None,
    max_parallel: Optional[int] = None,
    on_error: Optional[str] = None,
    save: bool = False,
    create_task: bool = False,
    user_approved: bool = False,
) -> str:
    """Build a flow for this request and run it, in one call.

    Use it when the work splits into steps for several agents: some in
    parallel, some one after another, one step per item of a list the run
    finds (map), or a branch chosen by an agent or by a condition. The graph is
    checked first (unknown agents, cycles, branch targets, agents you may not
    delegate to); nothing is saved or started while it has errors.

    The first call without user_approved returns the plan and its cost
    estimate: show both to the user and call again with the same arguments and
    user_approved=True once they agree. The run goes on in the background:
    report that it started and give the link; do not poll it here.

    The flow is temporary unless save=True: it stays out of the flow list and
    is deleted a week after the run, unless the user keeps it (the run page's
    Keep button, or modify_flow_tool with keep=True).
    """
    from datetime import datetime, timezone

    from common.agent_context import current_agent_id
    from common.auto_mode import approved
    from flow import store as flow_store

    ws = resolve_active_workspace()
    now = datetime.now(timezone.utc).isoformat()
    flow: Dict[str, Any] = {
        "id": str(uuid4()),
        "name": name.strip(),
        "description": goal.strip(),
        "nodes": _to_combined_nodes(nodes or [], edges=edges or []),
        "edges": _to_combined_edges(edges or []),
        "workspace": ws,
        "task_id": None,
        "created_at": now,
        "updated_at": now,
        "ephemeral": not save,
        "created_by_agent": current_agent_id.get() or None,
    }
    if max_parallel is not None:
        flow["max_parallel"] = max_parallel
    if on_error:
        flow["on_error"] = on_error

    errors, warnings = _validate_combined(flow, ws)
    if not errors:
        errors = _delegation_errors(flow, ws)
    if errors:
        return _json_err(
            "The flow is invalid and was NOT started. Fix the problems and call again.",
            code="invalid_flow",
            extra={"errors": errors, "warnings": warnings},
        )

    estimate = _estimate(flow)
    plan = _simplify(flow)
    plan.pop("id", None)
    if not approved("run_new_flow_tool", user_approved,
                    {"name": flow["name"], "goal": goal, "steps": len(flow["nodes"])},
                    reason="Builds a flow for this request and runs it."):
        extra: Dict[str, Any] = {"plan": plan}
        if estimate:
            extra["estimate"] = estimate
        if warnings:
            extra["warnings"] = warnings
        return _json_err(
            "This run is not approved yet. Show the user the plan and the estimate, ask "
            "them to confirm, and only then call run_new_flow_tool again with the same "
            "arguments and user_approved=True.",
            code="approval_required",
            extra=extra,
        )

    flow_store.save_flow(flow)
    record_entity("flow", flow["id"], "created", flow["name"])
    from tools.langchain_tools import launch_flow_run
    payload = launch_flow_run(flow["id"], flow, ws, goal.strip(), create_task=create_task)
    payload["link"] = f"/flows/{flow['id']}?run={payload['run_id']}"
    payload["ephemeral"] = bool(flow["ephemeral"])
    payload["plan"] = plan
    if flow["ephemeral"]:
        payload["message"] = (
            f"{payload['message']} The flow is temporary: it is not in the flow list and "
            "is deleted in a week unless the user keeps it from the run page.")
    if warnings:
        payload["warnings"] = warnings
    return _json_ok(payload)


def _run_new_flow_tool(
    name: str,
    goal: str,
    nodes: List[Dict[str, Any]],
    edges: Optional[List[Dict[str, Any]]] = None,
    max_parallel: Optional[int] = None,
    on_error: Optional[str] = None,
    save: bool = False,
    create_task: bool = False,
    user_approved: bool = False,
) -> str:
    try:
        return run_new_flow(name, goal, nodes, edges=edges, max_parallel=max_parallel,
                            on_error=on_error, save=save, create_task=create_task,
                            user_approved=user_approved)
    except Exception as e:  # noqa: BLE001 - the error goes back to the caller as a message
        return _json_err(f"Failed to run the new flow: {e}")


# One docstring, on run_new_flow, read by the tool catalog as the description.
_run_new_flow_tool.__doc__ = run_new_flow.__doc__
run_new_flow_tool = tool("run_new_flow_tool", args_schema=RunNewFlowInput)(_run_new_flow_tool)


# ── tools ─────────────────────────────────────────────────────────────────────

_SPEC = EntityToolSpec(
    singular="flow",
    plural="flows",
    create=ToolDef("create_flow_tool", CreateFlowInput, _create_flow, "Failed to create flow"),
    get=ToolDef("get_flow_tool", GetFlowInput, _get_flow, "Failed to get flow"),
    modify=ToolDef("modify_flow_tool", ModifyFlowInput, _modify_flow, "Failed to modify flow"),
    delete=ToolDef("delete_flow_tool", DeleteFlowInput, _delete_flow, "Failed to delete flow"),
    validate=ToolDef("validate_flow_tool", ValidateFlowInput, _validate_flow, "Failed to validate flow"),
)

_TOOLS = tools_by_id(build_entity_tools(_SPEC))
create_flow_tool = _TOOLS["create_flow_tool"]
get_flow_tool = _TOOLS["get_flow_tool"]
modify_flow_tool = _TOOLS["modify_flow_tool"]
delete_flow_tool = _TOOLS["delete_flow_tool"]
validate_flow_tool = _TOOLS["validate_flow_tool"]


__all__ = [
    "run_new_flow_tool",
    "create_flow_tool",
    "get_flow_tool",
    "modify_flow_tool",
    "delete_flow_tool",
    "validate_flow_tool",
]
