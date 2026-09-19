"""
View-building tools for agents.

``create_view`` is the tool path (Part I §3B of the design notes) for
views that are too large or too multi-file for the inline ``<<<ui>>>`` block: a
big dataset, an image, or (from later phases) a 3D scene or an interactive HTML
app. The agent writes any asset files with the normal filesystem tools, then
binds them into a validated, persisted view and gets back a ``view_id``.

Validation errors are returned *to the agent* as the tool result so it can
self-correct — the same loop ``apply_unified_diff`` uses. The tool is created
per-agent-build bound to the agent's workspace, mirroring the filesystem tools.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, List, Optional

from pydantic import BaseModel, Field, model_validator
from langchain_core.tools import tool

from common.agent_context import current_task_id, current_view_id
from common.entity_sink import record_entity
from common.workspace_context import workspace_name_from_path
from views.models import ViewValidationError, SUPPORTED_KINDS
from views.ops import OpError, get_at
from views.store import (
    create_view as _store_create_view,
    append_ops as _append_ops,
    get_view as _get_view,
    revert_to as _revert_to,
    add_asset as _add_asset,
    view_asset_path as _view_asset_path,
    save_checkpoint as _save_checkpoint,
    checkpoint_seq as _checkpoint_seq,
    list_checkpoints as _list_checkpoints,
)


def _deep_merge(base: Any, patch: Any) -> Any:
    """Recursively merge ``patch`` into ``base`` (dicts merged, others replaced)."""
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for k, v in patch.items():
            out[k] = _deep_merge(out.get(k), v) if k in out else v
        return out
    return patch


def _loads(raw: Any, default: Any) -> Any:
    """Parse a JSON (or Python-literal) argument that the model may pass as a
    string or as an already-decoded object. Returns ``default`` for empty."""
    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    text = str(raw).strip()
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(text)
        except Exception:
            continue
    return default


class JsonArgsModel(BaseModel):
    """Base for tool arg schemas whose JSON arguments are typed as strings.

    The schemas ask for a string because nested object schemas are where tool
    calling goes wrong most often, but a model that reads ``[x, y, z]`` in a
    description will sometimes send a real list. The tool bodies parse either
    shape with :func:`_loads`; this re-encodes the decoded value so pydantic
    lets it through instead of failing the call before the body ever runs.
    """

    @model_validator(mode="before")
    @classmethod
    def _encode_json_strings(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = data
        for name, value in data.items():
            if isinstance(value, bool) or not isinstance(value, (dict, list, int, float)):
                continue
            field = cls.model_fields.get(name)
            if field is None or field.annotation is not str:
                continue
            if out is data:
                out = dict(data)
            out[name] = json.dumps(value, ensure_ascii=False)
        return out


class CreateViewInput(JsonArgsModel):
    view_kind: str = Field(..., description=(
        "The kind of view to render. One of: " + ", ".join(SUPPORTED_KINDS) + "."))
    title: str = Field(..., description="Short human title shown on the view.")
    spec: str = Field(..., description=(
        "The kind-specific spec as a JSON object (string). Examples — "
        "chart: {\"vega_lite\": {...}}; table: {\"columns\": [...], \"rows\": [...]}; "
        "diagram: {\"mermaid\": \"flowchart LR\\n A-->B\"}; markdown: {\"markdown\": \"...\"}; "
        "image: {\"src\": \"chart.png\", \"caption\": \"...\"}."))
    summary: str = Field("", description=(
        "One-line summary shown on non-visual surfaces and the card header. "
        "Always provide it."))
    data: str = Field("", description=(
        "Optional data reference as a JSON object: {\"file\": \"data.json\"} for a "
        "large dataset written into the workspace, or {\"inline\": {...}}. Omit "
        "when the spec already contains the data."))
    files: str = Field("", description=(
        "Optional JSON array of workspace-relative file paths (already written "
        "with write_file) to bind into the view as assets, e.g. "
        "[\"chart.png\", \"data.json\"]."))
    complexity: str = Field("inline", description=(
        "Default display tier: 'inline', 'expanded', or 'fullscreen'."))


def create_view_tools(workspace: Optional[str] = None) -> List[Any]:
    """Build the view tools bound to ``workspace`` (the agent's operating path)."""
    ws_name = workspace_name_from_path(workspace)
    ws_abs = Path(workspace).resolve() if workspace else None

    @tool("create_view", args_schema=CreateViewInput)
    def create_view(view_kind: str, title: str, spec: str, summary: str = "",
                    data: str = "", files: str = "", complexity: str = "inline") -> str:
        """Create a rich view (chart, table, diagram, markdown, image) from a spec.

        Use this for a view too large or multi-file for an inline block — a big
        dataset, an image, or a composed scene. Write any asset files first with
        write_file, then list them in ``files``. Returns the new ``view_id`` on
        success, or a validation error to fix and retry.
        """
        parsed_spec = _loads(spec, None)
        if not isinstance(parsed_spec, dict):
            return json.dumps({"ok": False, "error": (
                "spec must be a JSON object matching the view_kind's schema")})
        parsed_data = _loads(data, None)
        file_list = _loads(files, [])
        if not isinstance(file_list, list):
            file_list = []

        # Resolve each referenced workspace file to an absolute, contained path.
        asset_sources: dict[str, str] = {}
        for rel in file_list:
            rel = str(rel).strip()
            if not rel or ws_abs is None:
                continue
            src = (ws_abs / rel).resolve()
            if ws_abs != src and ws_abs not in src.parents:
                return json.dumps({"ok": False, "error": f"file path escapes workspace: {rel}"})
            if not src.is_file():
                return json.dumps({"ok": False, "error": f"file not found in workspace: {rel}"})
            asset_sources[Path(rel).name] = str(src)

        try:
            env = _store_create_view(
                view_kind, title, parsed_spec,
                workspace=ws_name,
                summary=summary,
                data=parsed_data if isinstance(parsed_data, dict) else None,
                complexity=complexity,
                asset_sources=asset_sources or None,
                task_id=current_task_id.get(),
            )
        except ViewValidationError as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        except Exception as exc:  # never crash the agent turn on a view failure
            return json.dumps({"ok": False, "error": f"could not create view: {exc}"}, ensure_ascii=False)

        record_entity("view", env.view_id, "created", env.title)
        return json.dumps({"ok": True, "view_id": env.view_id, "kind": env.kind,
                           "title": env.title, "assets": env.assets}, ensure_ascii=False)

    class AddAssetInput(JsonArgsModel):
        path: str = Field(..., description="Workspace-relative path of a file (image/model) to bind into the view.")
        view_id: str = Field("", description="Target view; defaults to the active Studio view.")

    @tool("view_add_asset", args_schema=AddAssetInput)
    def view_add_asset(path: str, view_id: str = "") -> str:
        """Bind a workspace file (texture image, .glb model) into a live view.

        Copies it into the view's asset dir and returns an ``asset://<name>`` ref
        to use in a spec — e.g. a scene material's ``map`` or a model ``src``.
        """
        vid = _resolve_vid(view_id)
        if not vid:
            return _no_view()
        rel = str(path).strip()
        if not rel or ws_abs is None:
            return json.dumps({"ok": False, "error": "a workspace path is required"})
        src = (ws_abs / rel).resolve()
        if ws_abs != src and ws_abs not in src.parents:
            return json.dumps({"ok": False, "error": f"file path escapes workspace: {rel}"})
        if not src.is_file():
            return json.dumps({"ok": False, "error": f"file not found in workspace: {rel}"})
        try:
            ref = _add_asset(vid, str(src), Path(rel).name)
        except KeyError:
            return json.dumps({"ok": False, "error": f"view not found: {vid}"})
        except Exception as exc:
            return json.dumps({"ok": False, "error": f"could not add asset: {exc}"}, ensure_ascii=False)
        record_entity("view", vid, "updated")
        return json.dumps({"ok": True, "view_id": vid, "asset": ref}, ensure_ascii=False)

    return [create_view, view_add_asset]


# ── live-view mutation tools (Studio) ─────────────────────────────────────────
#
# These operate on an existing view by id and don't need workspace binding (the
# view row records its workspace). They default to the active Studio view via the
# ``current_view_id`` contextvar, so the agent can "add a node" without repeating
# the id. Each call is one visible step: it applies + broadcasts a batch of ops,
# and the Studio renders the change immediately (the graph-builder experience,
# generalized to every view kind).

def _resolve_vid(view_id: str) -> str:
    return (view_id or "").strip() or (current_view_id.get() or "")


def _no_view() -> str:
    return json.dumps({"ok": False, "error": (
        "no active view — open a view in the Studio, or pass view_id")})


def _run_id() -> str:
    from common.agent_context import current_session_id
    return current_session_id.get() or ""


def _apply(view_id: str, ops: list) -> str:
    """Shared tail: apply a batch of ops and report a concise result."""
    try:
        stored = _append_ops(view_id, ops, run_id=_run_id() or None, source="agent")
    except KeyError:
        return json.dumps({"ok": False, "error": f"view not found: {view_id}"})
    except OpError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"could not apply ops: {exc}"}, ensure_ascii=False)
    record_entity("view", view_id, "updated")
    return json.dumps({"ok": True, "view_id": view_id, "applied": len(stored),
                       "last_seq": stored[-1]["seq"] if stored else None}, ensure_ascii=False)


class ApplyOpsInput(JsonArgsModel):
    ops: str = Field(..., description=(
        "A JSON array of ops. Each op is {\"op\": \"add|update|remove|clear\", "
        "\"path\": \"dotted.path\", \"value\": <any>}. Paths address the view "
        "document tree; build collections as keyed maps, e.g. "
        "{\"op\":\"add\",\"path\":\"spec.nodes.n1\",\"value\":{\"label\":\"Auth\"}}. "
        "'value' is required for add/update."))
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


class ViewGetInput(JsonArgsModel):
    path: str = Field("", description="Dotted path to read (empty = whole document).")
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


class AddControlInput(JsonArgsModel):
    control: str = Field(..., description=(
        "A JSON control object: {\"id\": \"roughness\", \"type\": "
        "\"slider|select|toggle|color|text|range\", \"label\": \"...\", "
        "\"bind\": \"spec.path.to.value\", \"min\": 0, \"max\": 1, \"value\": 0.5, "
        "\"options\": [...]}. Bind it to the spec path the control should drive."))
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


class RemoveControlInput(JsonArgsModel):
    control_id: str = Field(..., description="Id of the control to remove.")
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


class RevertInput(JsonArgsModel):
    seq: int = Field(-1, description="Revert the view to this op seq (0 = empty). Undoes later ops.")
    checkpoint: str = Field("", description="Or revert to a named checkpoint (see view_snapshot). Takes precedence when set.")
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


class SnapshotViewInput(JsonArgsModel):
    name: str = Field(..., description="Checkpoint name, e.g. 'before-texture-experiments'.")
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


class GraphNodeInput(JsonArgsModel):
    node_id: str = Field(..., description="Stable id for the node (lowercase slug).")
    label: str = Field(..., description="Short display label.")
    group: str = Field("", description="Optional cluster/group name.")
    kind: str = Field("", description="Optional node kind/type for styling.")
    subtitle: str = Field("", description="Optional one-line detail.")
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


class GraphEdgeInput(JsonArgsModel):
    source: str = Field(..., description="Id of the source node (should already exist).")
    target: str = Field(..., description="Id of the target node (should already exist).")
    label: str = Field("", description="Optional relation label.")
    edge_id: str = Field("", description="Optional stable edge id; derived from source/target if omitted.")
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


class GraphRemoveInput(JsonArgsModel):
    element_id: str = Field(..., description="Id of the node or edge to remove (a node also drops its edges).")
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


class GraphLayoutInput(JsonArgsModel):
    layout: str = Field(..., description="Cytoscape layout name: cose, breadthfirst, circle, grid, concentric, dagre.")
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


@tool("view_apply_ops", args_schema=ApplyOpsInput)
def view_apply_ops(ops: str, view_id: str = "") -> str:
    """Apply a batch of ops to a live view (add/update/remove/clear at a path).

    The general mutation primitive behind every view kind. The change is applied
    and streamed to the Studio immediately. Prefer the graph_* helpers for graphs.
    """
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    parsed = _loads(ops, None)
    if not isinstance(parsed, (list, dict)):
        return json.dumps({"ok": False, "error": "ops must be a JSON array of op objects"})
    return _apply(vid, parsed)


@tool("view_get", args_schema=ViewGetInput)
def view_get(path: str = "", view_id: str = "") -> str:
    """Read the current state of a live view (or a subtree at a dotted path).

    The agent's "eyes" on the view — call it to see existing object ids, control
    values and selection before mutating. Large subtrees are summarized.
    """
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    doc = _get_view(vid)
    if doc is None:
        return json.dumps({"ok": False, "error": f"view not found: {vid}"})
    record_entity("view", vid, "viewed")
    sub = get_at(doc, path)
    payload = json.dumps({"ok": True, "view_id": vid, "path": path or "(root)", "value": sub}, ensure_ascii=False)
    if len(payload) > 6000:
        # Too big to dump — summarize keyed collections by id lists + counts.
        summary = _summarize(sub)
        payload = json.dumps({"ok": True, "view_id": vid, "path": path or "(root)",
                              "summary": summary, "truncated": True}, ensure_ascii=False)
    return payload


def _summarize(value):
    if isinstance(value, dict):
        return {k: (f"{len(v)} items: {list(v)[:20]}" if isinstance(v, (dict, list)) else v)
                for k, v in value.items()}
    if isinstance(value, list):
        return f"{len(value)} items"
    return value


@tool("view_add_control", args_schema=AddControlInput)
def view_add_control(control: str, view_id: str = "") -> str:
    """Add an interactive control (slider/select/toggle/…) to a live view.

    Controls are authored by you, not predefined: pick the parameters a user
    would want to tweak, bind each to the spec path it drives. Rendered in the
    Studio control panel; the user's changes are visible to you via view_get.
    """
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    obj = _loads(control, None)
    if not isinstance(obj, dict):
        return json.dumps({"ok": False, "error": "control must be a JSON object"})
    cid = str(obj.get("id") or "").strip()
    if not cid:
        import uuid
        cid = "c_" + uuid.uuid4().hex[:8]
        obj["id"] = cid
    return _apply(vid, [{"op": "add", "path": f"controls.{cid}", "value": obj}])


@tool("view_remove_control", args_schema=RemoveControlInput)
def view_remove_control(control_id: str, view_id: str = "") -> str:
    """Remove a control from a live view by its id."""
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    cid = str(control_id or "").strip()
    if not cid:
        return json.dumps({"ok": False, "error": "control_id is required"})
    return _apply(vid, [{"op": "remove", "path": f"controls.{cid}"}])


@tool("view_revert", args_schema=RevertInput)
def view_revert(seq: int = -1, checkpoint: str = "", view_id: str = "") -> str:
    """Undo a live view back to an op seq (0 = empty) or a named checkpoint.

    Drops all later ops. Use view_snapshot first to name states worth returning to.
    """
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    target = int(seq)
    if checkpoint.strip():
        cp = _checkpoint_seq(vid, checkpoint.strip())
        if cp is None:
            known = list(_list_checkpoints(vid))
            return json.dumps({"ok": False, "error": (
                f"no checkpoint named {checkpoint.strip()!r}"
                + (f"; known: {', '.join(known)}" if known else " (none saved yet — see view_snapshot)"))})
        target = cp
    elif target < 0:
        return json.dumps({"ok": False, "error": "pass a seq (0 = empty) or a checkpoint name"})
    if _revert_to(vid, target):
        record_entity("view", vid, "updated")
        return json.dumps({"ok": True, "view_id": vid, "reverted_to": target,
                           **({"checkpoint": checkpoint.strip()} if checkpoint.strip() else {})})
    return json.dumps({"ok": False, "error": f"view not found: {vid}"})


@tool("view_snapshot", args_schema=SnapshotViewInput)
def view_snapshot(name: str, view_id: str = "") -> str:
    """Save a named checkpoint of a live view's current state.

    Later, view_revert(checkpoint=name) returns to it — cheap insurance before a
    risky batch of edits. Re-using a name moves the checkpoint.
    """
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    seq = _save_checkpoint(vid, name)
    if seq is None:
        return json.dumps({"ok": False, "error": f"view not found: {vid}"})
    return json.dumps({"ok": True, "view_id": vid, "checkpoint": name, "seq": seq})


@tool("graph_add_node", args_schema=GraphNodeInput)
def graph_add_node(node_id: str, label: str, group: str = "", kind: str = "",
                   subtitle: str = "", view_id: str = "") -> str:
    """Add (or update) one node on a live graph view. Renders immediately.

    Call once per node as you identify it — reuse an id to update it. Build the
    graph step by step so the user watches it take shape.
    """
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    nid = str(node_id).strip()
    if not nid:
        return json.dumps({"ok": False, "error": "node_id is required"})
    data = {"label": label}
    if group:
        data["group"] = group
    if kind:
        data["kind"] = kind
    if subtitle:
        data["subtitle"] = subtitle
    return _apply(vid, [{"op": "add", "path": f"spec.nodes.{nid}", "value": data}])


@tool("graph_add_edge", args_schema=GraphEdgeInput)
def graph_add_edge(source: str, target: str, label: str = "", edge_id: str = "",
                   view_id: str = "") -> str:
    """Add (or update) one directed edge between two nodes on a live graph view."""
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    s, t = str(source).strip(), str(target).strip()
    if not s or not t:
        return json.dumps({"ok": False, "error": "source and target are required"})
    eid = str(edge_id).strip() or f"{s}__{t}"
    return _apply(vid, [{"op": "add", "path": f"spec.edges.{eid}",
                         "value": {"source": s, "target": t, "label": label}}])


@tool("graph_remove", args_schema=GraphRemoveInput)
def graph_remove(element_id: str, view_id: str = "") -> str:
    """Remove a node or edge from a live graph view (a node drops its edges too)."""
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    eid = str(element_id).strip()
    if not eid:
        return json.dumps({"ok": False, "error": "element_id is required"})
    doc = _get_view(vid) or {}
    spec = doc.get("spec") or {}
    ops = []
    nodes = spec.get("nodes") or {}
    edges = spec.get("edges") or {}
    if isinstance(nodes, dict) and eid in nodes:
        ops.append({"op": "remove", "path": f"spec.nodes.{eid}"})
        # drop incident edges
        if isinstance(edges, dict):
            for k, e in edges.items():
                if e.get("source") == eid or e.get("target") == eid:
                    ops.append({"op": "remove", "path": f"spec.edges.{k}"})
    elif isinstance(edges, dict) and eid in edges:
        ops.append({"op": "remove", "path": f"spec.edges.{eid}"})
    else:
        return json.dumps({"ok": False, "error": f"no node or edge with id {eid!r}"})
    return _apply(vid, ops)


@tool("graph_set_layout", args_schema=GraphLayoutInput)
def graph_set_layout(layout: str, view_id: str = "") -> str:
    """Set the layout algorithm of a live graph view (cose, breadthfirst, …)."""
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    return _apply(vid, [{"op": "update", "path": "spec.layout", "value": str(layout).strip()}])


# ── 3D scene presentation (scene3d) ───────────────────────────────────────────
# Geometry is no longer authored here. A scene3d view shows what the Blender
# geometry engine produced (see tools/geometry.py); what is left in this file is
# how that result is *presented*: where the camera sits, how it is lit, and what
# the backdrop looks like.

class SceneCameraInput(JsonArgsModel):
    camera: str = Field(..., description=(
        "JSON camera: {\"position\":[3,3,5],\"target\":[0,0,0],\"fov\":50}."))
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


class SceneLightInput(JsonArgsModel):
    light: str = Field(..., description=(
        "JSON light: {\"id\":\"sun\",\"type\":\"directional|ambient|point|hemisphere\","
        "\"color\":\"#ffffff\",\"intensity\":1.0,\"position\":[5,10,5]}."))
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


@tool("scene_camera", args_schema=SceneCameraInput)
def scene_camera(camera: str, view_id: str = "") -> str:
    """Set the camera pose of a live scene3d view (position, target, fov)."""
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    cam = _loads(camera, None)
    if not isinstance(cam, dict):
        return json.dumps({"ok": False, "error": "camera must be a JSON object"})
    return _apply(vid, [{"op": "update", "path": "spec.camera", "value": cam}])


@tool("scene_light", args_schema=SceneLightInput)
def scene_light(light: str, view_id: str = "") -> str:
    """Add (or update) a light in a live scene3d view."""
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    l = _loads(light, None)
    if not isinstance(l, dict):
        return json.dumps({"ok": False, "error": "light must be a JSON object"})
    lid = str(l.get("id") or "").strip()
    if not lid:
        import uuid
        lid = "l_" + uuid.uuid4().hex[:8]
        l["id"] = lid
    return _apply(vid, [{"op": "add", "path": f"spec.lights.{lid}", "value": l}])


class SceneEnvironmentInput(JsonArgsModel):
    environment: str = Field(..., description=(
        "JSON environment patch, deep-merged: {\"background\":\"#0b1120\","
        "\"grid\":false,\"shadows\":true,\"fit\":true,\"autoRotate\":true,"
        "\"autoRotateSpeed\":1,\"shadowOpacity\":0.45,\"floor\":0}. \"fit\" frames "
        "the camera on the scene contents — use it for a loaded model whose real "
        "scale you do not know."))
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


@tool("scene_environment", args_schema=SceneEnvironmentInput)
def scene_environment(environment: str, view_id: str = "") -> str:
    """Set the scene's presentation: background, grid, contact shadows, auto-fit, turntable.

    Bind a control to e.g. spec.environment.autoRotate so the user can spin the
    model, or to spec.environment.background to change the backdrop.
    """
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    patch = _loads(environment, None)
    if not isinstance(patch, dict):
        return json.dumps({"ok": False, "error": "environment must be a JSON object"})
    doc = _get_view(vid) or {}
    current = (doc.get("spec") or {}).get("environment") or {}
    return _apply(vid, [{"op": "update", "path": "spec.environment",
                         "value": _deep_merge(current, patch)}])


# ── compute layer: timeline, simulation, math, annotations (Phase 5) ──────────

class SetTimelineInput(JsonArgsModel):
    timeline: str = Field(..., description=(
        "JSON timeline block: {\"mode\":\"stepped|live|recorded\",\"t\":0,"
        "\"speed\":1,\"loop\":true,\"range\":[0,10]}. Its presence gives the view "
        "play/pause/scrub/step transport controls."))
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


class SimConfigureInput(JsonArgsModel):
    runtime: str = Field(..., description=(
        "Client runtime name: particles, boids, nbody (2D orbits), wave (2D "
        "field), sph2d (fluid), agents (goal-seeking entities w/ obstacles), tokens."))
    params: str = Field("", description="JSON params object for the runtime (also exposed as controls).")
    entities: str = Field("", description="Optional JSON seed population {id: {...}} or count config.")
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


class MathPlotInput(JsonArgsModel):
    expr: str = Field(..., description=(
        "Expression to plot, e.g. 'a*sin(b*x)'. For mode=parametric pass the "
        "comma pair 'cos(3*t), sin(2*t)'; for surface3d an expression in x and "
        "y, e.g. 'sin(x)*cos(y)'."))
    variable: str = Field("x", description="Independent variable name (t for parametric).")
    domain: str = Field("", description="JSON [min,max] domain, e.g. [-10,10].")
    domain2: str = Field("", description="Optional JSON [min,max] second-axis domain for surface3d (defaults to domain).")
    params: str = Field("", description="JSON named params {\"a\":1,\"b\":2} (exposed as controls).")
    latex: str = Field("", description="Optional pretty LaTeX of the equation for the annotation.")
    mode: str = Field("function2d", description="function2d | parametric | surface3d.")
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


class AnnotateInput(JsonArgsModel):
    annotation: str = Field(..., description=(
        "JSON annotation: {\"id\":\"a1\",\"type\":\"label|callout|region|equation\","
        "\"text\":\"...\"}. For an equation with live values: {\"type\":\"equation\","
        "\"latex\":\"y=a\\\\sin(bx)\",\"values\":{\"a\":\"spec.params.a\","
        "\"b\":\"spec.params.b\"}} — each value is a spec path shown live. Optional "
        "\"anchor\": an object id, a region, or a time range [t0,t1]."))
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


@tool("view_set_timeline", args_schema=SetTimelineInput)
def view_set_timeline(timeline: str, view_id: str = "") -> str:
    """Set a view's timeline block, enabling play/pause/scrub/step transport.

    Use for simulations, process execution, or animating a math parameter. The
    runtime and the renderer read ``t`` from here.
    """
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    tl = _loads(timeline, None)
    if not isinstance(tl, dict):
        return json.dumps({"ok": False, "error": "timeline must be a JSON object"})
    return _apply(vid, [{"op": "update", "path": "timeline", "value": tl}])


@tool("sim_configure", args_schema=SimConfigureInput)
def sim_configure(runtime: str, params: str = "", entities: str = "", view_id: str = "") -> str:
    """Configure a simulation view's runtime, params and seed entities.

    Sets which client runtime steps the scene (particles, boids, tokens, …) and
    its parameters — which are also exposed as live controls the user can tune
    mid-run. Pair with view_set_timeline to enable playback.
    """
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    ops = [{"op": "update", "path": "spec.runtime", "value": str(runtime).strip()}]
    p = _loads(params, None)
    if isinstance(p, dict):
        ops.append({"op": "update", "path": "spec.params", "value": p})
    e = _loads(entities, None)
    if isinstance(e, dict):
        ops.append({"op": "update", "path": "spec.entities", "value": e})
    return _apply(vid, ops)


@tool("math_plot", args_schema=MathPlotInput)
def math_plot(expr: str, variable: str = "x", domain: str = "", domain2: str = "",
              params: str = "", latex: str = "", mode: str = "function2d",
              view_id: str = "") -> str:
    """Set the expression, variable, domain and params of a math (plot) view.

    Params become live controls; the plot and the equation update as they change.
    """
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    ops = [
        {"op": "update", "path": "spec.expr", "value": str(expr)},
        {"op": "update", "path": "spec.variable", "value": str(variable or "x")},
        {"op": "update", "path": "spec.mode", "value": str(mode or "function2d")},
    ]
    d = _loads(domain, None)
    if isinstance(d, list) and len(d) == 2:
        ops.append({"op": "update", "path": "spec.domain", "value": d})
    d2 = _loads(domain2, None)
    if isinstance(d2, list) and len(d2) == 2:
        ops.append({"op": "update", "path": "spec.domain2", "value": d2})
    p = _loads(params, None)
    if isinstance(p, dict):
        ops.append({"op": "update", "path": "spec.params", "value": p})
    if latex:
        ops.append({"op": "update", "path": "spec.latex", "value": str(latex)})
    return _apply(vid, ops)


@tool("view_annotate", args_schema=AnnotateInput)
def view_annotate(annotation: str, view_id: str = "") -> str:
    """Add an annotation (label, callout, region, or live equation) to a view.

    The interpretation layer: anchor explanations to objects, regions or time
    ranges. An equation annotation binds KaTeX params to spec paths so the shown
    values update live as controls/simulation change.
    """
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    a = _loads(annotation, None)
    if not isinstance(a, dict):
        return json.dumps({"ok": False, "error": "annotation must be a JSON object"})
    aid = str(a.get("id") or "").strip()
    if not aid:
        import uuid
        aid = "a_" + uuid.uuid4().hex[:8]
        a["id"] = aid
    return _apply(vid, [{"op": "add", "path": f"annotations.{aid}", "value": a}])


# ── precise server compute (Phase 6) ──────────────────────────────────────────

class ViewComputeInput(JsonArgsModel):
    runtime: str = Field(..., description=(
        "Server runtime: 'nbody' (gravitational N-body), 'wave2d' (2D wave "
        "field), 'schrodinger1d' (1D Schrödinger |ψ|²), 'nn_trace' (per-layer "
        "activations of an NN forward pass; params e.g. {\"layers\":[8,16,4]})."))
    params: str = Field("", description="JSON params for the runtime (e.g. {\"count\":300} or {\"potential\":\"barrier\"}).")
    steps: int = Field(400, description="Number of integration steps (capped by the server budget).")
    dt: float = Field(0.01, description="Time step size.")
    max_frames: int = Field(200, description="How many frames to stream/record (capped).")
    clip_name: str = Field("clip", description="Name to record the run under (replayable).")
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


@tool("view_compute", args_schema=ViewComputeInput)
def view_compute(runtime: str, params: str = "", steps: int = 400, dt: float = 0.01,
                 max_frames: int = 200, clip_name: str = "clip", view_id: str = "") -> str:
    """Run a precise server-side simulation and stream results to the view.

    The *precise* tier (numpy solvers) versus the real-time client runtimes:
    N-body integration, wave fields, Schrödinger evolution. Runs in the
    background, streaming frames to the canvas and recording a replayable clip.
    Marks the view fidelity=precise and enables recorded playback. Say that this
    is the precise version (vs a real-time approximation).
    """
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    from views.compute import SERVER_RUNTIMES
    if runtime not in SERVER_RUNTIMES:
        return json.dumps({"ok": False, "error": f"unknown runtime {runtime!r}; available: {list(SERVER_RUNTIMES)}"})
    p = _loads(params, {})
    if not isinstance(p, dict):
        return json.dumps({"ok": False, "error": "params must be a JSON object"})

    # Record the compute config on the view (fidelity + a compute block + a
    # recorded timeline) so the client knows to listen for frames / replay.
    _apply(vid, [
        {"op": "update", "path": "fidelity", "value": "precise"},
        {"op": "update", "path": "spec.compute", "value": {"runtime": runtime, "params": p, "clip": clip_name}},
        {"op": "update", "path": "timeline", "value": {"mode": "recorded", "clip": clip_name}},
    ])
    from views.compute.runner import run_compute_async
    run_compute_async(vid, runtime, p, steps=int(steps), dt=float(dt),
                      max_frames=int(max_frames), clip_name=str(clip_name) or "clip")
    return json.dumps({"ok": True, "view_id": vid, "runtime": runtime,
                       "streaming": True, "clip": clip_name,
                       "note": "precise computation started; frames streaming to the canvas"}, ensure_ascii=False)


# ── view_serve: full-stack proxy (Phase 6) ────────────────────────────────────

class ViewServeInput(JsonArgsModel):
    upstream: str = Field("", description=(
        "Loopback base URL of an already-running service to expose, e.g. "
        "'http://localhost:8123'. Only localhost/127.0.0.1 upstreams are allowed. "
        "Omit when passing 'command' to launch one."))
    command: str = Field("", description=(
        "Optional command to LAUNCH the backend (e.g. 'python app.py'), run in "
        "the view's workspace and killed when the view is deleted. Requires "
        "'port' and the views_serve_launch_enabled setting."))
    port: int = Field(0, description="The localhost port the launched command will listen on (required with 'command').")
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


@tool("view_serve", args_schema=ViewServeInput)
def view_serve(upstream: str = "", command: str = "", port: int = 0, view_id: str = "") -> str:
    """Expose a web service behind the view's scoped, origin-isolated proxy.

    Two modes: pass 'upstream' for a service you already started, or pass
    'command' + 'port' to launch the generated backend in the view's workspace
    (its lifecycle is bound to the view; output goes to .view_serve.log). The
    dashboard proxies it at /api/views/<id>/proxy/* — localhost-only, no
    cookies forwarded. Use for a real, working web-service preview.
    """
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    from views.serve import is_allowed_upstream, start_service
    up = str(upstream).strip().rstrip("/")
    launched = None

    if command.strip():
        doc = _get_view(vid)
        if doc is None:
            return json.dumps({"ok": False, "error": f"view not found: {vid}"})
        from common.paths import WORKSPACES_ROOT
        ws = doc.get("workspace")
        cwd = str(WORKSPACES_ROOT / ws) if ws else str(WORKSPACES_ROOT)
        try:
            launched = start_service(vid, command.strip(), cwd, int(port))
        except ValueError as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        up = launched["upstream"]

    if not is_allowed_upstream(up):
        return json.dumps({"ok": False, "error": "upstream must be a localhost/127.0.0.1 http(s) URL"})
    serve_block = {"upstream": up}
    if launched:
        serve_block["pid"] = launched["pid"]
        serve_block["command"] = command.strip()
    res = _apply(vid, [{"op": "update", "path": "serve", "value": serve_block}])
    if not json.loads(res).get("ok"):
        return res
    return json.dumps({"ok": True, "view_id": vid, "proxy": f"/api/views/{vid}/proxy/",
                       **({"pid": launched["pid"]} if launched else {})}, ensure_ascii=False)


class ViewServeStopInput(JsonArgsModel):
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


@tool("view_serve_stop", args_schema=ViewServeStopInput)
def view_serve_stop(view_id: str = "") -> str:
    """Stop the backend launched for this view (view_serve command mode) and
    unregister its proxy upstream."""
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    from views.serve import stop_service
    stopped = stop_service(vid)
    _apply(vid, [{"op": "remove", "path": "serve"}])
    return json.dumps({"ok": True, "view_id": vid, "stopped_process": stopped})


# ── linked views: shared timebase / selection (Phase 5) ──────────────────────

class ViewLinkInput(JsonArgsModel):
    views: str = Field("", description=(
        "JSON array of view ids to pin next to this one in the Studio, e.g. "
        "[\"vw_abc\"] — typically a chart fed by this view's frame aggregates."))
    timebase: str = Field("", description=(
        "Shared timebase name. Views naming the same timebase share one clock: "
        "this view drives it; linked charts plot its aggregates over t."))
    selection: bool = Field(True, description="Share the selection across the linked views.")
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


@tool("view_link", args_schema=ViewLinkInput)
def view_link(views: str = "", timebase: str = "", selection: bool = True, view_id: str = "") -> str:
    """Link other views to this one on a shared timebase and selection bus.

    The Studio renders the linked views beside this one; a linked chart whose
    link names the same timebase plots this view's live frame aggregates over
    time (an interpretation dashboard: simulation + throughput chart). Pass
    empty views + empty timebase to unlink.
    """
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    ids = _loads(views, [])
    if not isinstance(ids, list):
        return json.dumps({"ok": False, "error": "views must be a JSON array of view ids"})
    ids = [str(v).strip() for v in ids if str(v).strip()]
    for other in ids:
        if _get_view(other) is None:
            return json.dumps({"ok": False, "error": f"linked view not found: {other}"})
    tb = str(timebase).strip()
    if not ids and not tb:
        return _apply(vid, [{"op": "remove", "path": "link"}])
    return _apply(vid, [{"op": "update", "path": "link",
                         "value": {"views": ids, "timebase": tb, "selection": bool(selection)}}])


# ── publishing: slides / document (Phase 4) ───────────────────────────────────

class SlidesAddInput(JsonArgsModel):
    title: str = Field(..., description="Slide title.")
    body: str = Field("", description="Slide body as markdown.")
    slide_id: str = Field("", description="Optional stable slide id (auto if omitted).")
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


class DocumentSetInput(JsonArgsModel):
    markdown: str = Field(..., description="The document body as markdown.")
    title: str = Field("", description="Optional document title.")
    css: str = Field("", description="Optional page CSS for print/PDF.")
    view_id: str = Field("", description="Target view; defaults to the active Studio view.")


@tool("slides_add", args_schema=SlidesAddInput)
def slides_add(title: str, body: str = "", slide_id: str = "", view_id: str = "") -> str:
    """Append a slide (title + markdown body) to a live slides view.

    Build a deck one slide at a time; slides present in insertion order.
    """
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    doc = _get_view(vid) or {}
    existing = (doc.get("spec") or {}).get("slides") or {}
    order = len(existing) if isinstance(existing, (dict, list)) else 0
    sid = str(slide_id).strip()
    if not sid:
        import uuid
        sid = "s_" + uuid.uuid4().hex[:8]
    return _apply(vid, [{"op": "add", "path": f"spec.slides.{sid}",
                         "value": {"title": title, "body": body, "order": order}}])


@tool("document_set", args_schema=DocumentSetInput)
def document_set(markdown: str, title: str = "", css: str = "", view_id: str = "") -> str:
    """Set a document view's markdown body (and optional title / print CSS)."""
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    ops = [{"op": "update", "path": "spec.markdown", "value": str(markdown)}]
    if title:
        ops.append({"op": "update", "path": "spec.title", "value": str(title)})
    if css:
        ops.append({"op": "update", "path": "spec.css", "value": str(css)})
    return _apply(vid, ops)


# ── suggest_view advisor ──────────────────────────────────────────────────────

class SuggestViewInput(JsonArgsModel):
    data: str = Field("", description="A representative JSON data sample to visualize (a few rows/objects is enough).")
    goal: str = Field("", description="Optional one-line description of what the visualization should show.")


def _suggest(sample: Any, goal: str) -> list:
    """Rank candidate view kinds for a data sample (deterministic heuristics)."""
    g = (goal or "").lower()
    ranked: list = []

    def add(kind, why, starter):
        ranked.append({"kind": kind, "why": why, "starter_spec": starter})

    # explicit graph shape
    if isinstance(sample, dict) and ("nodes" in sample and "edges" in sample):
        add("graph", "data already has nodes and edges", {"nodes": {}, "edges": {}, "layout": "cose"})
    if any(w in g for w in ("depend", "relation", "network", "topology", "graph", "flow between")):
        add("graph", "goal describes entities and relations", {"nodes": {}, "edges": {}, "layout": "cose"})
    if any(w in g for w in ("process", "sequence", "workflow", "state machine", "pipeline")):
        add("diagram", "goal describes a process/sequence", {"mermaid": "flowchart LR\n  A-->B"})

    rows = sample if isinstance(sample, list) else None
    if rows and rows and isinstance(rows[0], dict):
        keys = list(rows[0].keys())
        numeric = [k for k in keys if isinstance(rows[0].get(k), (int, float))]
        cats = [k for k in keys if k not in numeric]
        if numeric and cats:
            add("chart", "rows have a category and a numeric measure",
                {"vega_lite": {"mark": "bar",
                               "encoding": {"x": {"field": cats[0], "type": "nominal"},
                                            "y": {"field": numeric[0], "type": "quantitative"}}}})
        add("table", "tabular rows the user can scan/sort/filter",
            {"columns": keys, "rows": []})
    elif rows is not None:
        add("table", "a list of values", {"columns": ["value"], "rows": []})

    if any(w in g for w in ("3d", "scene", "model", "geometry", "spatial")):
        add("scene3d", "goal describes 3D/spatial content", {"objects": {}, "lights": {}, "camera": {}})
    if any(w in g for w in ("equation", "formula", "math", "latex")):
        add("latex", "goal describes an equation", {"latex": "e^{i\\pi} + 1 = 0"})

    if not ranked:
        add("markdown", "no clear structure — present as rich text", {"markdown": ""})
        add("table", "fallback tabular view", {"columns": [], "rows": []})
    # de-dupe by kind, keep first (highest-priority) reason
    seen, out = set(), []
    for r in ranked:
        if r["kind"] in seen:
            continue
        seen.add(r["kind"])
        out.append(r)
    return out


@tool("suggest_view", args_schema=SuggestViewInput)
def suggest_view(data: str = "", goal: str = "") -> str:
    """Suggest the best view kind(s) for some data, with a starter spec.

    Give it a small JSON data sample and/or a one-line goal; returns ranked
    candidate kinds each with a reason and a starter spec to build from. Use it
    when unsure how to present a result.
    """
    sample = _loads(data, None)
    ranked = _suggest(sample, goal)
    return json.dumps({"ok": True, "recommended": ranked[0]["kind"] if ranked else "markdown",
                       "candidates": ranked}, ensure_ascii=False)


# Mutation tools that operate by view id (no workspace binding needed).
VIEW_MUTATION_TOOLS = [
    view_apply_ops, view_get, view_add_control, view_remove_control,
    view_revert, view_snapshot,
    graph_add_node, graph_add_edge, graph_remove, graph_set_layout,
    scene_camera, scene_light, scene_environment,
    view_set_timeline, sim_configure, math_plot, view_annotate, view_link,
    slides_add, document_set, view_compute, view_serve, view_serve_stop,
    suggest_view,
]


__all__ = ["create_view_tools", "VIEW_MUTATION_TOOLS"]
