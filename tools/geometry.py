"""
Geometry tools: the agent's only way to reach the Blender engine.

The agent never writes Blender Python. It calls these tools, they validate the
arguments against ``connectors.blender.protocol`` and hand a named command to a
daemon. That boundary is the security model, and it is also what makes the
build *repeatable*: a command that cannot be expressed here cannot happen, so
the log of commands is a complete description of the object.

Each mutating command does three things beyond running:

1. **Records a revision.** The command goes into the object's build log
   (``connectors.blender.history``), which is authoritative — the Blender
   process is a cache that may be evicted or killed, and a replay of the log
   rebuilds the object exactly.
2. **Exports the mesh.** A fresh ``.glb`` lands in the view's asset dir, so the
   Studio shows the object as it is right now. One file per revision, which is
   also how reverting can show an earlier state without rebuilding it.
3. **Updates the view.** A view op points ``spec.objects.<id>`` at the new
   asset and carries the command that produced it, so the op log doubles as the
   build history the Studio can replay.

Mesh health is reported after every mutation, cheaply (manifoldness, degenerate
and loose geometry); the expensive checks (self-intersection) run on request and
before an export the user is meant to keep.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import Field
from langchain_core.tools import tool

from common.agent_context import current_view_id
from common.entity_sink import record_entity
from common.workspace_context import workspace_name_from_path
from connectors.blender import history, pool
from connectors.blender.daemon import CommandError, DaemonError
from views.store import append_ops as _append_ops, get_view as _get_view, view_dir as _view_dir

# Shared with the view tools rather than duplicated: same argument sloppiness to
# absorb (models pass JSON as strings), same "no active view" answer.
from tools.views import JsonArgsModel, _loads, _resolve_vid, _no_view, _run_id

#: How many exported revisions of one object to keep on disk. Enough to step
#: back through a build in the Studio, not enough to fill a workspace.
KEEP_REVISIONS = 12


def _err(message: str, kind: str = "engine") -> str:
    return json.dumps({"ok": False, "error": message, "kind": kind}, ensure_ascii=False)


def _vec(raw: Any, default: Optional[List[float]] = None) -> Optional[List[float]]:
    value = _loads(raw, None)
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return [float(value)] * 3
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return [float(v) for v in value]
    raise ValueError(f"expected [x, y, z], got {raw!r}")


def _selection(raw: Any) -> Dict[str, Any]:
    value = _loads(raw, None)
    if not value:
        return {"by": "last_created"}
    if isinstance(value, str):
        return {"by": value}
    if isinstance(value, dict):
        return value
    raise ValueError(f"selection must be an object like {{\"by\": \"last_created\"}}, got {raw!r}")


def _object(object_id: str, view_id: str = "") -> "tuple[str, str]":
    """The object a command acts on: the one named, or the one the view was
    working on last. Returns (object_id, error) the way :func:`_call` does.

    A build is a chain — mesh_new hull, inset its top, extrude that — and a
    model five commands deep routinely drops the name it has repeated all
    along. Falling back to the most recently touched object continues the chain
    the way {"by":"last_created"} continues a selection. Every reply names the
    object it acted on, so a wrong guess shows up in the next answer instead of
    silently editing the wrong mesh.
    """
    if object_id:
        return object_id, ""
    vid = _resolve_vid(view_id)
    if not vid:
        return "", _no_view()
    recent = history.recent(vid)
    if not recent:
        return "", _err("no object to work on yet: name one with object_id, or "
                        "start one with mesh_new", "protocol")
    return recent[0], ""


# ── running a command ────────────────────────────────────────────────────────

def _call(cmd: str, args: Dict[str, Any], view_id: str = "", *,
          object_id: str = "", ensure: bool = True):
    """Run one engine command for the active view. Returns (vid, result) or a
    JSON error string the tool can return as-is."""
    vid = _resolve_vid(view_id)
    if not vid:
        return None, _no_view()
    doc = _get_view(vid)
    if doc is None:
        return None, _err(f"view not found: {vid}", "protocol")
    if doc.get("kind") != "scene3d":
        return None, _err(f"this is a {doc.get('kind')} view; geometry needs a scene3d view", "protocol")
    try:
        daemon = pool.acquire(vid)
        if object_id and ensure:
            history.ensure(daemon, vid, object_id)
        return vid, daemon.call(cmd, args)
    except CommandError as exc:
        return None, _err(str(exc), exc.kind)
    except DaemonError as exc:
        return None, _err(str(exc), "engine")
    except Exception as exc:                       # never take the agent turn down
        return None, _err(f"{type(exc).__name__}: {exc}", "engine")


def _mutated(vid: str, object_id: str, cmd: str, args: Dict[str, Any],
             result: Dict[str, Any]) -> str:
    """Record, export and publish one successful mutation.

    The revision an object is stamped with is its **step in the build log**, not
    the engine's internal counter. The log is what survives eviction and what
    mesh_revert cuts, so a revision that means "step 7 of this object" stays
    true across a replay, while the engine's counter restarts with the process
    and counts other objects' commands too.
    """
    revision = history.append(vid, object_id, cmd, args, int(result.get("revision") or 0))
    ref, export_error = _export_revision(vid, object_id, revision)
    ops: List[Dict[str, Any]] = []
    if ref:
        value = {"src": ref, "revision": revision, "cmd": cmd,
                 "stats": result.get("stats") or {}}
        ops.append({"op": "update", "path": f"spec.objects.{object_id}", "value": value})
        # A first object in an empty scene is invisible without framing: nobody
        # knows what scale the engine produced, least of all the default camera.
        doc = _get_view(vid) or {}
        if not (doc.get("spec") or {}).get("environment"):
            ops.append({"op": "update", "path": "spec.environment",
                        "value": {"fit": True, "shadows": True, "grid": True}})
    if ops:
        try:
            _append_ops(vid, ops, run_id=_run_id() or None, source="agent")
        except Exception as exc:
            export_error = export_error or f"could not update the view: {exc}"
    record_entity("view", vid, "updated")
    return _reply(vid, object_id, result, export=ref, warning=export_error, revision=revision)


def _export_revision(vid: str, object_id: str, revision: int):
    """Export the current mesh into the view's assets. Returns (asset_ref, error)."""
    base = _view_dir(vid)
    if base is None:
        return "", "view directory is missing"
    rel = f"mesh/{object_id}_r{revision}.glb"
    target = base / rel
    try:
        pool.call(vid, "mesh_export", {"object_id": object_id, "path": str(target),
                                       "format": "glb"})
    except (CommandError, DaemonError) as exc:
        return "", f"the mesh could not be exported for display: {exc}"
    _prune(base / "mesh", object_id)
    return f"asset://{rel}", ""


def _prune(mesh_dir: Path, object_id: str) -> None:
    """Keep the newest revisions of one object; drop the rest."""
    if not mesh_dir.is_dir():
        return
    files = sorted(mesh_dir.glob(f"{object_id}_r*.glb"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in files[KEEP_REVISIONS:]:
        try:
            stale.unlink()
        except OSError:
            pass


def _reply(vid: str, object_id: str, result: Dict[str, Any], *,
           export: str = "", warning: str = "", ids: bool = False,
           revision: Optional[int] = None) -> str:
    """The compact answer an agent gets back.

    Deliberately small. The engine reports every created element id and every
    validation sample; dumping that into the conversation costs context on every
    command and tells the model nothing it cannot ask for. Counts, the health
    verdict, and what to do about it.
    """
    out: Dict[str, Any] = {"ok": True, "view_id": vid}
    if object_id:
        out["object_id"] = object_id
    if revision is not None:
        out["revision"] = revision
    for key in ("created_counts", "counts", "group", "groups", "images",
                "path", "bytes", "format", "stored_as"):
        if result.get(key) is not None:
            out[key] = result[key]
    stats = result.get("stats")
    if stats:
        out["stats"] = {k: stats[k] for k in ("verts", "edges", "faces", "tris", "dimensions")
                        if k in stats}
    validation = result.get("validation")
    if validation:
        out["mesh"] = _health(validation)
    if ids and result.get("selection"):
        out["selection"] = result["selection"]
    if ids and result.get("created"):
        out["created"] = result["created"]
    if export:
        out["shown_as"] = export
    if warning:
        out["warning"] = warning
    return json.dumps(out, ensure_ascii=False)


def _health(validation: Dict[str, Any]) -> Any:
    """Validation as a verdict, not a table."""
    if validation.get("ok"):
        return "clean" if validation.get("watertight") else "clean, not watertight (open surface)"
    problems = {}
    for key in validation.get("problems") or []:
        entry = validation.get(key)
        if isinstance(entry, dict):
            problems[key] = {"count": entry.get("count"), "ids": (entry.get("ids") or [])[:8]}
    problems["watertight"] = validation.get("watertight")
    return problems


# ── tools ────────────────────────────────────────────────────────────────────

_SELECTION_HELP = (
    "Which geometry to act on, as JSON. {\"by\":\"last_created\"} (default) is what "
    "the previous command made — chain inset then extrude with it and you never "
    "have to name anything. Others: {\"by\":\"ids\",\"faces\":[12,13]}, "
    "{\"by\":\"group\",\"name\":\"roof\"}, {\"by\":\"normal_axis\",\"axis\":\"+z\","
    "\"tolerance\":0.25} (faces pointing that way), {\"by\":\"bbox\",\"min\":[null,null,0.4],"
    "\"max\":[null,null,null]} (nulls are unbounded), {\"by\":\"boundary\"}, "
    "{\"by\":\"non_manifold\"}, {\"by\":\"all\"}.")

_VIEW_HELP = "Target scene3d view; defaults to the active Studio view."


class MeshNewInput(JsonArgsModel):
    object_id: str = Field(..., description="Name for the object, e.g. 'hull'. Reuse with replace=true to start over.")
    primitive: str = Field("cube", description="cube, plane, circle, cylinder, cone, uv_sphere, ico_sphere or torus.")
    size: float = Field(1.0, description="Overall size of the primitive.")
    dimensions: str = Field("", description="Optional [x,y,z] the result is scaled to, e.g. [4.2,1.8,1.3]. Author in real dimensions; it saves a transform.")
    location: str = Field("", description="Optional [x,y,z] object origin.")
    segments: int = Field(32, description="Resolution for round primitives (3-128).")
    radius: float = Field(0, description="Radius for circle/cylinder/cone/sphere/torus (0 = derive from size).")
    depth: float = Field(0, description="Height for cylinder/cone (0 = derive from size).")
    replace: bool = Field(False, description="Rebuild an object that already exists, discarding its geometry.")
    view_id: str = Field("", description=_VIEW_HELP)


@tool("mesh_new", args_schema=MeshNewInput)
def mesh_new(object_id: str, primitive: str = "cube", size: float = 1.0,
             dimensions: str = "", location: str = "", segments: int = 32,
             radius: float = 0, depth: float = 0, replace: bool = False,
             view_id: str = "") -> str:
    """Start a new mesh object from a primitive. Renders in the Studio immediately.

    This is always step one: real modeling is a primitive plus a sequence of
    operations, not a pile of coordinates. Give it roughly the right dimensions
    and shape it from there.
    """
    try:
        args: Dict[str, Any] = {"object_id": object_id, "primitive": primitive,
                                "size": size, "segments": segments, "replace": replace}
        dims = _vec(dimensions)
        if dims:
            args["dimensions"] = dims
        loc = _vec(location)
        if loc:
            args["location"] = loc
        if radius:
            args["radius"] = radius
        if depth:
            args["depth"] = depth
    except ValueError as exc:
        return _err(str(exc), "protocol")
    vid, result = _call("mesh_new", args, view_id, object_id=object_id, ensure=False)
    if vid is None:
        return result
    history.forget(vid, object_id)          # a new object starts a new build log
    return _mutated(vid, object_id, "mesh_new", args, result)


class MeshSelectInput(JsonArgsModel):
    object_id: str = Field("", description="The object to select in. Defaults to the one this view worked on last.")
    selection: str = Field("", description=_SELECTION_HELP)
    store_as: str = Field("", description="Save this selection as a named group, e.g. 'roof'. Groups are stored in the mesh, so they survive later operations that replace the geometry they named.")
    view_id: str = Field("", description=_VIEW_HELP)


@tool("mesh_select", args_schema=MeshSelectInput)
def mesh_select(object_id: str = "", selection: str = "", store_as: str = "",
                view_id: str = "") -> str:
    """Resolve a selection and report what it matched, optionally naming it.

    Use it to check a selection before acting on it: it returns counts and ids
    and changes nothing. Naming a group is how a region stays addressable across
    the rest of the build.
    """
    object_id, error = _object(object_id, view_id)
    if error:
        return error
    try:
        args = {"object_id": object_id, "selection": _selection(selection),
                "store_as": store_as}
    except ValueError as exc:
        return _err(str(exc), "protocol")
    vid, result = _call("mesh_select", args, view_id, object_id=object_id)
    if vid is None:
        return result
    if store_as:
        history.append(vid, object_id, "mesh_select", args, 0)
    return _reply(vid, object_id, result, ids=True)


class MeshExtrudeInput(JsonArgsModel):
    object_id: str = Field("", description="The object to extrude in. Defaults to the one this view worked on last.")
    selection: str = Field("", description=_SELECTION_HELP)
    along_normal: float = Field(0.0, description="Distance along the selection's own normal. The usual way to extrude — positive is outward.")
    translate: str = Field("", description="Optional extra [x,y,z] offset, added to the normal offset.")
    individual: bool = Field(False, description="Extrude each face separately instead of as one connected region.")
    view_id: str = Field("", description=_VIEW_HELP)


@tool("mesh_extrude", args_schema=MeshExtrudeInput)
def mesh_extrude(object_id: str = "", selection: str = "", along_normal: float = 0.0,
                 translate: str = "", individual: bool = False, view_id: str = "") -> str:
    """Pull faces out into new geometry — the main shaping operation.

    A tower is a cube with its top face extruded; a chimney is an inset on that
    top face, extruded again. The new faces become the default selection, so the
    next command can just say {"by":"last_created"}.
    """
    object_id, error = _object(object_id, view_id)
    if error:
        return error
    try:
        args = {"object_id": object_id, "selection": _selection(selection),
                "along_normal": along_normal, "individual": individual,
                "translate": _vec(translate, [0.0, 0.0, 0.0])}
    except ValueError as exc:
        return _err(str(exc), "protocol")
    vid, result = _call("mesh_extrude", args, view_id, object_id=object_id)
    if vid is None:
        return result
    return _mutated(vid, object_id, "mesh_extrude", args, result)


class MeshInsetInput(JsonArgsModel):
    object_id: str = Field("", description="The object to inset in. Defaults to the one this view worked on last.")
    selection: str = Field("", description=_SELECTION_HELP)
    thickness: float = Field(0.1, description="How far in from the face border.")
    depth: float = Field(0.0, description="Push the inner face in (negative) or out (positive) at the same time.")
    individual: bool = Field(False, description="Inset each face separately.")
    view_id: str = Field("", description=_VIEW_HELP)


@tool("mesh_inset", args_schema=MeshInsetInput)
def mesh_inset(object_id: str = "", selection: str = "", thickness: float = 0.1,
               depth: float = 0.0, individual: bool = False, view_id: str = "") -> str:
    """Shrink a face inward, leaving a border — the setup for most details.

    Inset then extrude gives panels, recesses, windows, sockets and raised
    blocks. The inner face becomes the default selection.
    """
    object_id, error = _object(object_id, view_id)
    if error:
        return error
    try:
        args = {"object_id": object_id, "selection": _selection(selection),
                "thickness": thickness, "depth": depth, "individual": individual}
    except ValueError as exc:
        return _err(str(exc), "protocol")
    vid, result = _call("mesh_inset", args, view_id, object_id=object_id)
    if vid is None:
        return result
    return _mutated(vid, object_id, "mesh_inset", args, result)


class MeshBevelInput(JsonArgsModel):
    object_id: str = Field("", description="The object to bevel in. Defaults to the one this view worked on last.")
    selection: str = Field("", description=_SELECTION_HELP + " {\"by\":\"all\"} chamfers every edge, which is what makes a shape stop looking like a raw box.")
    offset: float = Field(0.05, description="Width of the chamfer. Keep it small relative to the smallest feature.")
    segments: int = Field(2, description="Roundness, 1-16. 1 is a flat chamfer, 3+ reads as a fillet.")
    profile: float = Field(0.5, description="0 = concave, 0.5 = round, 1 = convex.")
    affect: str = Field("edges", description="'edges' or 'verts'.")
    view_id: str = Field("", description=_VIEW_HELP)


@tool("mesh_bevel", args_schema=MeshBevelInput)
def mesh_bevel(object_id: str = "", selection: str = "", offset: float = 0.05,
               segments: int = 2, profile: float = 0.5, affect: str = "edges",
               view_id: str = "") -> str:
    """Chamfer or round edges. Do it last: it multiplies the geometry every
    later operation has to work with, and an offset larger than a nearby feature
    will fold the mesh into itself (mesh_validate will say so)."""
    object_id, error = _object(object_id, view_id)
    if error:
        return error
    try:
        args = {"object_id": object_id, "selection": _selection(selection),
                "offset": offset, "segments": segments, "profile": profile,
                "affect": affect}
    except ValueError as exc:
        return _err(str(exc), "protocol")
    vid, result = _call("mesh_bevel", args, view_id, object_id=object_id)
    if vid is None:
        return result
    return _mutated(vid, object_id, "mesh_bevel", args, result)


class MeshTransformInput(JsonArgsModel):
    object_id: str = Field("", description="The object to transform in. Defaults to the one this view worked on last.")
    selection: str = Field("", description=_SELECTION_HELP)
    translate: str = Field("", description="[x,y,z] to move by.")
    rotate: str = Field("", description="[x,y,z] euler rotation in degrees.")
    scale: str = Field("", description="[x,y,z] or a single number.")
    pivot: str = Field("center", description="'center' (of the selection), 'origin', or 'point'.")
    point: str = Field("", description="[x,y,z] pivot when pivot='point'.")
    view_id: str = Field("", description=_VIEW_HELP)


@tool("mesh_transform", args_schema=MeshTransformInput)
def mesh_transform(object_id: str = "", selection: str = "", translate: str = "",
                   rotate: str = "", scale: str = "", pivot: str = "center",
                   point: str = "", view_id: str = "") -> str:
    """Move, rotate or scale part of a mesh — a group of vertices, a face, a region.

    Applied in that order (scale, rotate, move) about the chosen pivot. This is
    how a named group of nodes gets reshaped after the fact.
    """
    object_id, error = _object(object_id, view_id)
    if error:
        return error
    try:
        args: Dict[str, Any] = {
            "object_id": object_id, "selection": _selection(selection),
            "translate": _vec(translate, [0.0, 0.0, 0.0]),
            "rotate": _vec(rotate, [0.0, 0.0, 0.0]),
            "scale": _vec(scale, [1.0, 1.0, 1.0]),
            "pivot": pivot,
        }
        if pivot == "point":
            args["point"] = _vec(point, [0.0, 0.0, 0.0])
    except ValueError as exc:
        return _err(str(exc), "protocol")
    vid, result = _call("mesh_transform", args, view_id, object_id=object_id)
    if vid is None:
        return result
    return _mutated(vid, object_id, "mesh_transform", args, result)


class MeshDeleteInput(JsonArgsModel):
    object_id: str = Field("", description="The object to delete in. Defaults to the one this view worked on last.")
    selection: str = Field(..., description=_SELECTION_HELP)
    mode: str = Field("faces", description="verts, edges, faces, faces_only (keep the border) or faces_keep_boundary.")
    view_id: str = Field("", description=_VIEW_HELP)


@tool("mesh_delete", args_schema=MeshDeleteInput)
def mesh_delete(object_id: str, selection: str, mode: str = "faces",
                view_id: str = "") -> str:
    """Remove geometry. Deleting faces opens the mesh — check mesh_validate
    afterwards if the result is meant to stay watertight."""
    object_id, error = _object(object_id, view_id)
    if error:
        return error
    try:
        args = {"object_id": object_id, "selection": _selection(selection), "mode": mode}
    except ValueError as exc:
        return _err(str(exc), "protocol")
    vid, result = _call("mesh_delete", args, view_id, object_id=object_id)
    if vid is None:
        return result
    return _mutated(vid, object_id, "mesh_delete", args, result)


class MeshSubdivideInput(JsonArgsModel):
    object_id: str = Field("", description="The object to subdivide in. Defaults to the one this view worked on last.")
    selection: str = Field("", description=_SELECTION_HELP)
    cuts: int = Field(1, description="Cuts per edge, 1-6. Each cut roughly quadruples the faces in the region.")
    smooth: float = Field(0.0, description="0 keeps the shape, 1 rounds it toward a smooth surface.")
    view_id: str = Field("", description=_VIEW_HELP)


@tool("mesh_subdivide", args_schema=MeshSubdivideInput)
def mesh_subdivide(object_id: str = "", selection: str = "", cuts: int = 1,
                   smooth: float = 0.0, view_id: str = "") -> str:
    """Add resolution where more detail is needed. Subdivide the region you are
    about to shape, not the whole object."""
    object_id, error = _object(object_id, view_id)
    if error:
        return error
    try:
        args = {"object_id": object_id, "selection": _selection(selection),
                "cuts": cuts, "smooth": smooth}
    except ValueError as exc:
        return _err(str(exc), "protocol")
    vid, result = _call("mesh_subdivide", args, view_id, object_id=object_id)
    if vid is None:
        return result
    return _mutated(vid, object_id, "mesh_subdivide", args, result)


class MeshMergeInput(JsonArgsModel):
    object_id: str = Field("", description="The object to clean up. Defaults to the one this view worked on last.")
    selection: str = Field("", description=_SELECTION_HELP + " Defaults to the whole mesh for this command.")
    distance: float = Field(0.0001, description="Vertices closer than this are welded together.")
    view_id: str = Field("", description=_VIEW_HELP)


@tool("mesh_merge", args_schema=MeshMergeInput)
def mesh_merge(object_id: str = "", selection: str = "", distance: float = 0.0001,
               view_id: str = "") -> str:
    """Weld duplicate vertices — the standard fix when mesh_validate reports
    duplicates or a surface that should be closed but is not."""
    object_id, error = _object(object_id, view_id)
    if error:
        return error
    try:
        args = {"object_id": object_id,
                "selection": _selection(selection or '{"by":"all"}'),
                "distance": distance}
    except ValueError as exc:
        return _err(str(exc), "protocol")
    vid, result = _call("mesh_merge", args, view_id, object_id=object_id)
    if vid is None:
        return result
    return _mutated(vid, object_id, "mesh_merge", args, result)


class MeshNormalsInput(JsonArgsModel):
    object_id: str = Field("", description="The object to fix. Defaults to the one this view worked on last.")
    inside: bool = Field(False, description="Point the normals inward instead of outward.")
    view_id: str = Field("", description=_VIEW_HELP)


@tool("mesh_normals", args_schema=MeshNormalsInput)
def mesh_normals(object_id: str = "", inside: bool = False, view_id: str = "") -> str:
    """Recalculate face normals so the surface is consistently oriented.

    Worth running before a final export: a mesh with flipped faces renders with
    black patches and is rejected by most downstream tools.
    """
    object_id, error = _object(object_id, view_id)
    if error:
        return error
    args = {"object_id": object_id, "inside": inside}
    vid, result = _call("mesh_normals", args, view_id, object_id=object_id)
    if vid is None:
        return result
    return _mutated(vid, object_id, "mesh_normals", args, result)


class MeshGroupInput(JsonArgsModel):
    object_id: str = Field("", description="The object the group belongs to. Defaults to the one this view worked on last.")
    name: str = Field(..., description="Group name, e.g. 'roof' or 'front_panel'.")
    selection: str = Field("", description=_SELECTION_HELP)
    view_id: str = Field("", description=_VIEW_HELP)


@tool("mesh_group", args_schema=MeshGroupInput)
def mesh_group(object_id: str, name: str, selection: str = "", view_id: str = "") -> str:
    """Name a set of vertices, edges and faces so it can be addressed later.

    Membership is stored in the mesh itself, so it carries onto the geometry
    later operations create: name the roof before beveling and "the roof" still
    means the roof afterwards.
    """
    object_id, error = _object(object_id, view_id)
    if error:
        return error
    try:
        args = {"object_id": object_id, "name": name, "selection": _selection(selection)}
    except ValueError as exc:
        return _err(str(exc), "protocol")
    vid, result = _call("mesh_group", args, view_id, object_id=object_id)
    if vid is None:
        return result
    history.append(vid, object_id, "mesh_group", args, 0)
    return _reply(vid, object_id, result)


class MeshValidateInput(JsonArgsModel):
    object_id: str = Field("", description="The object to check. Defaults to the one this view worked on last.")
    full: bool = Field(False, description="Also check self-intersections (slower; run it before a final export).")
    view_id: str = Field("", description=_VIEW_HELP)


@tool("mesh_validate", args_schema=MeshValidateInput)
def mesh_validate(object_id: str = "", full: bool = False, view_id: str = "") -> str:
    """Check the mesh: manifoldness, holes, degenerate and loose geometry,
    duplicate vertices, and with full=true, self-intersections.

    Reports the ids of the offending elements, so a fix can address them
    directly: {"by":"non_manifold"} and {"by":"boundary"} select them.
    """
    object_id, error = _object(object_id, view_id)
    if error:
        return error
    vid, result = _call("mesh_validate", {"object_id": object_id, "full": full},
                        view_id, object_id=object_id)
    if vid is None:
        return result
    out = json.loads(_reply(vid, object_id, result))
    out["validation"] = result.get("validation")     # the full report, on request
    return json.dumps(out, ensure_ascii=False)


class MeshStatsInput(JsonArgsModel):
    object_id: str = Field("", description="The object to measure. Defaults to the one this view worked on last.")
    view_id: str = Field("", description=_VIEW_HELP)


@tool("mesh_stats", args_schema=MeshStatsInput)
def mesh_stats(object_id: str = "", view_id: str = "") -> str:
    """Counts, bounding box and dimensions of an object, plus its named groups."""
    object_id, error = _object(object_id, view_id)
    if error:
        return error
    vid, result = _call("mesh_stats", {"object_id": object_id}, view_id, object_id=object_id)
    if vid is None:
        return result
    return _reply(vid, object_id, result)


class MeshPreviewInput(JsonArgsModel):
    object_id: str = Field("", description="The object to render. Defaults to the one this view worked on last.")
    angles: str = Field("persp", description="Comma-separated: persp, front, back, left, right, top, bottom.")
    size: int = Field(512, description="Pixels per side (64-1600).")
    view_id: str = Field("", description=_VIEW_HELP)


@tool("mesh_preview", args_schema=MeshPreviewInput)
def mesh_preview(object_id: str = "", angles: str = "persp", size: int = 512,
                 view_id: str = "") -> str:
    """Render the object so you can look at it.

    Validation proves the mesh is sound; it says nothing about whether the shape
    is the one that was asked for. Render after the main forms are in, look at
    the result, and fix what is wrong before adding detail.
    """
    object_id, error = _object(object_id, view_id)
    if error:
        return error
    wanted = [a.strip() for a in str(angles or "persp").split(",") if a.strip()]
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    base = _view_dir(vid)
    if base is None:
        return _err(f"view not found: {vid}", "protocol")
    args = {"object_id": object_id, "path": str(base / "preview" / f"{object_id}.png"),
            "angles": wanted or ["persp"], "size": size}
    vid2, result = _call("mesh_preview", args, view_id, object_id=object_id)
    if vid2 is None:
        return result
    images = []
    for image in result.get("images") or []:
        try:
            rel = Path(image["path"]).relative_to(base).as_posix()
            images.append({"angle": image["angle"], "ref": f"asset://{rel}"})
        except ValueError:
            continue
    return json.dumps({"ok": True, "view_id": vid, "object_id": object_id,
                       "images": images}, ensure_ascii=False)


class MeshHistoryInput(JsonArgsModel):
    object_id: str = Field("", description="The object whose build log to read. Defaults to the one this view worked on last.")
    view_id: str = Field("", description=_VIEW_HELP)


@tool("mesh_history", args_schema=MeshHistoryInput)
def mesh_history(object_id: str = "", view_id: str = "") -> str:
    """The commands that built this object, in order.

    The log is the object: it is what gets replayed after the engine is evicted
    or restarted, and what mesh_revert cuts short.
    """
    object_id, error = _object(object_id, view_id)
    if error:
        return error
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    entries = history.read(vid, object_id)
    return json.dumps({
        "ok": True, "view_id": vid, "object_id": object_id, "steps": len(entries),
        "log": [{"seq": e["seq"], "cmd": e["cmd"], "revision": e.get("revision")}
                for e in entries],
    }, ensure_ascii=False)


class MeshRevertInput(JsonArgsModel):
    object_id: str = Field("", description="The object to roll back. Defaults to the one this view worked on last.")
    seq: int = Field(..., description="Keep the build log up to this step (from mesh_history); everything after it is discarded.")
    view_id: str = Field("", description=_VIEW_HELP)


@tool("mesh_revert", args_schema=MeshRevertInput)
def mesh_revert(object_id: str, seq: int, view_id: str = "") -> str:
    """Undo back to an earlier step by rebuilding from the log.

    Not a stack of inverse operations: the log is cut at ``seq`` and the object
    is replayed from scratch, which is exact regardless of what the discarded
    commands did.
    """
    object_id, error = _object(object_id, view_id)
    if error:
        return error
    vid = _resolve_vid(view_id)
    if not vid:
        return _no_view()
    entries = history.read(vid, object_id)
    if not entries:
        return _err(f"no build log for {object_id!r}", "protocol")
    if seq < 1 or seq > entries[-1]["seq"]:
        return _err(f"seq must be between 1 and {entries[-1]['seq']}", "protocol")
    kept = history.truncate(vid, object_id, seq)
    try:
        daemon = pool.acquire(vid)
        daemon.call("object_delete", {"object_id": object_id})
    except CommandError:
        pass                                  # not in the engine: a replay builds it
    except DaemonError as exc:
        return _err(str(exc), "engine")
    try:
        history.replay(daemon, vid, object_id)
        result = daemon.call("mesh_stats", {"object_id": object_id})
    except (CommandError, DaemonError) as exc:
        return _err(f"replay failed at step {len(kept)}: {exc}", "engine")
    revision = kept[-1]["seq"] if kept else 0
    ref, warning = _export_revision(vid, object_id, revision)
    if ref:
        _append_ops(vid, [{"op": "update", "path": f"spec.objects.{object_id}",
                           "value": {"src": ref, "revision": revision,
                                     "cmd": "mesh_revert", "stats": result.get("stats") or {}}}],
                    run_id=_run_id() or None, source="agent")
    return _reply(vid, object_id, result, export=ref, warning=warning, revision=revision)


def create_geometry_tools(workspace: Optional[str] = None) -> List[Any]:
    """The workspace-bound half: exporting a finished mesh out of the view."""
    ws_abs = Path(workspace).resolve() if workspace else None

    class ExportInput(JsonArgsModel):
        object_id: str = Field("", description="The object to export. Defaults to the one this view worked on last.")
        path: str = Field(..., description="Workspace-relative destination, e.g. 'models/hull.glb'.")
        format: str = Field("glb", description="glb, gltf, obj, stl or ply.")
        view_id: str = Field("", description=_VIEW_HELP)

    @tool("mesh_export", args_schema=ExportInput)
    def mesh_export(object_id: str, path: str, format: str = "glb", view_id: str = "") -> str:
        """Write a finished mesh into the workspace as a real file.

        Run mesh_validate (full=true) first: an export is what someone else
        downstream has to open, and a non-manifold or self-intersecting mesh
        fails in slicers, engines and CAD alike.
        """
        object_id, error = _object(object_id, view_id)
        if error:
            return error
        if ws_abs is None:
            return _err("this agent has no workspace to export into", "protocol")
        rel = str(path).strip()
        if not rel:
            return _err("a workspace-relative path is required", "protocol")
        dest = (ws_abs / rel).resolve()
        if ws_abs != dest and ws_abs not in dest.parents:
            return _err(f"path escapes workspace: {rel}", "protocol")
        dest.parent.mkdir(parents=True, exist_ok=True)
        vid, result = _call("mesh_export",
                            {"object_id": object_id, "path": str(dest), "format": format},
                            view_id, object_id=object_id)
        if vid is None:
            return result
        out = json.loads(_reply(vid, object_id, result))
        out["path"] = rel
        record_entity("view", vid, "updated")
        return json.dumps(out, ensure_ascii=False)

    return [mesh_export]


#: View-bound geometry tools (no workspace needed).
GEOMETRY_TOOLS = [
    mesh_new, mesh_select, mesh_extrude, mesh_inset, mesh_bevel, mesh_transform,
    mesh_delete, mesh_subdivide, mesh_merge, mesh_normals, mesh_group,
    mesh_validate, mesh_stats, mesh_preview, mesh_history, mesh_revert,
]

__all__ = ["GEOMETRY_TOOLS", "create_geometry_tools"]
