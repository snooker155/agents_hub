"""
The Blender-side host: a command server that speaks the geometry protocol.

Launched as ``blender --background --python host/main.py -- --socket <path>``.
Everything in this file runs inside Blender's own interpreter, which means:

- stdlib and ``bpy``/``bmesh``/``mathutils`` only, nothing from this project
  except the sibling :mod:`protocol` module (imported by path, below);
- one command at a time, on the main thread, because ``bpy`` is neither
  reentrant nor thread-safe. The socket server is deliberately serial.

Three ideas carry the whole design:

**Stable ids.** Every vertex, edge and face carries a ``gid`` in a bmesh int
layer, handed out by this host and never reused. Blender's own indices shift
under almost every operator, so "move vertex 12" would otherwise mean a
different corner three commands later. The agent addresses geometry by gid,
forever.

**Selection as data.** The agent never manipulates Blender's selection state.
Each command carries a selection spec that is resolved against the object's
current geometry at execution time (see :func:`resolve_selection`), and every
command reports what it created, so the usual chain (inset, then extrude what
the inset made) needs no selection language at all.

**Per-command atomicity.** A mutating command snapshots the mesh datablock
first and restores it if the operator raises or if the result breaks a budget.
A geometry operator that fails must leave the object exactly as it was, because
the caller's revision log assumes command boundaries are clean.
"""
import math
import os
import selectors
import socket
import sys
import time
import traceback

import bpy
import bmesh
import mathutils
from mathutils.bvhtree import BVHTree

# The protocol module sits one directory up. Import it by path rather than as a
# package: importing `connectors.blender.protocol` would drag in the project's
# __init__ chain, and none of those dependencies exist in Blender's Python.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import protocol  # noqa: E402


EPS_AREA = 1e-9
EPS_LEN = 1e-7


# ── object state ─────────────────────────────────────────────────────────────

class ObjectState:
    """Host-side bookkeeping for one mesh object.

    The geometry itself lives in the Blender datablock; what lives here is the
    part Blender has no concept of: the gid counter, named groups, and what the
    last command touched.
    """

    def __init__(self, object_id):
        self.object_id = object_id
        self.next_gid = 1
        self.groups = set()
        self.last_created = {"verts": [], "edges": [], "faces": []}
        self.last_selection = {"verts": [], "edges": [], "faces": []}

    def take_gids(self, count):
        start = self.next_gid
        self.next_gid += count
        return range(start, self.next_gid)


STATE = {
    "objects": {},        # object_id -> ObjectState
    "revision": 0,        # bumped by every mutating command
    "started_at": time.time(),
    "commands": 0,
}


def _state(object_id, create=False):
    st = STATE["objects"].get(object_id)
    if st is None:
        if not create:
            raise EngineError("no object %r; create it with mesh_new first" % object_id)
        st = ObjectState(object_id)
        STATE["objects"][object_id] = st
    return st


class EngineError(RuntimeError):
    """A command could not be carried out. Reported with ``error_kind:
    "engine"`` so the caller can tell it from a malformed request."""


# ── bmesh plumbing ───────────────────────────────────────────────────────────

def _obj(object_id):
    obj = bpy.data.objects.get(object_id)
    if obj is None or obj.type != "MESH":
        raise EngineError("no mesh object %r" % object_id)
    return obj


#: Attribute names are unique **per mesh**, not per domain: a second attribute
#: called "gid" on the edge domain does not conflict-and-rename, it is silently
#: dropped on the way into the mesh datablock. One name per domain, therefore.
GID_NAMES = ("gid_v", "gid_e", "gid_f")
GROUP_PREFIXES = ("gv_", "ge_", "gf_")


def _layers(bm):
    """The gid layers, created on first use.

    Every layer is created *before* any handle is taken. Adding a layer
    invalidates the layer references already handed out, so the tempting
    ``get(x) or new(x)`` one-liner per domain hands back a stale handle for
    every domain but the last, and writes through it go nowhere.
    """
    domains = ((bm.verts.layers.int, GID_NAMES[0]), (bm.edges.layers.int, GID_NAMES[1]),
               (bm.faces.layers.int, GID_NAMES[2]))
    for coll, name in domains:
        if coll.get(name) is None:
            coll.new(name)
    return tuple(coll.get(name) for coll, name in domains)


def _open(object_id):
    """Load an object's mesh into a bmesh with lookup tables ready."""
    obj = _obj(object_id)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    return obj, bm


def _commit(obj, bm):
    bm.normal_update()
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()


def _gid_of(elem, layer):
    return elem[layer]


def _assign_gids(state, elems, layer):
    """Hand fresh gids to elements an operator just created.

    Necessary because bmesh operators *copy custom data* onto new geometry: an
    extruded vertex arrives carrying its source vertex's gid. Taking the
    operator's own report of what it created and re-stamping it is what keeps
    gids unique without guessing which of two identical values is the original.
    """
    out = []
    for elem, gid in zip(elems, state.take_gids(len(elems))):
        elem[layer] = gid
        out.append(gid)
    return out


def _sweep_gids(state, bm, layers):
    """Backstop: give a gid to anything that still has none, or a duplicate one.

    Operators that create geometry without reporting all of it (and mesh data
    arriving from elsewhere) would otherwise leave zeros behind. Runs after
    every mutation; normally finds nothing.
    """
    vl, el, fl = layers
    for seq, layer in ((bm.verts, vl), (bm.edges, el), (bm.faces, fl)):
        seen = set()
        for elem in seq:
            gid = elem[layer]
            if gid <= 0 or gid in seen:
                gid = next(iter(state.take_gids(1)))
                elem[layer] = gid
            seen.add(gid)
        state.next_gid = max(state.next_gid, max(seen) + 1 if seen else 1)


def _collect(state, bm, layers, geom):
    """Stamp fresh gids on an operator's new geometry and return the elements.

    Elements, not gids: an operator creates geometry whose ids are not settled
    until :func:`_sweep_gids` has run, and reading them earlier is how an inset
    came to report one new edge instead of four (every new edge still held the
    zero it was born with). The caller reads gids off these elements after the
    sweep, when they are final.
    """
    vl, el, fl = layers
    verts = [g for g in geom if isinstance(g, bmesh.types.BMVert)]
    edges = [g for g in geom if isinstance(g, bmesh.types.BMEdge)]
    faces = [g for g in geom if isinstance(g, bmesh.types.BMFace)]
    _assign_gids(state, verts, vl)
    _assign_gids(state, edges, el)
    _assign_gids(state, faces, fl)
    return {"verts": verts, "edges": edges, "faces": faces}


def _elems_to_gids(elems, layers):
    vl, el, fl = layers
    return {"verts": [v[vl] for v in elems.get("verts", [])],
            "edges": [e[el] for e in elems.get("edges", [])],
            "faces": [f[fl] for f in elems.get("faces", [])]}


def _group_layers(bm, name, create=False):
    """The int layers backing one named group, per element domain.

    Groups are stored **in the mesh**, not as a list of ids on the side. A
    bevel or a subdivide replaces the very elements a saved id list points at,
    which left "move the roof" failing two commands after the roof was named.
    Blender interpolates custom data onto the geometry an operator creates, so
    membership rides along through operators that a list of ids cannot survive.
    """
    def one(coll, prefix):
        key = prefix + name
        layer = coll.get(key)
        if layer is None and create:
            layer = coll.new(key)
        return layer

    return (one(bm.verts.layers.int, GROUP_PREFIXES[0]),
            one(bm.edges.layers.int, GROUP_PREFIXES[1]),
            one(bm.faces.layers.int, GROUP_PREFIXES[2]))


def _group_names(bm):
    names = set()
    for coll, prefix in ((bm.verts.layers.int, GROUP_PREFIXES[0]),
                         (bm.edges.layers.int, GROUP_PREFIXES[1]),
                         (bm.faces.layers.int, GROUP_PREFIXES[2])):
        for key in coll.keys():
            if key.startswith(prefix):
                names.add(key[len(prefix):])
    return sorted(names)


def _write_group(bm, name, resolved):
    """Mark ``resolved`` as group ``name``, clearing any previous membership.

    Membership is captured as **indices before any layer is created**. Adding a
    custom-data layer invalidates the Python wrappers for elements that were
    fetched earlier: the same ``face in members`` test answers True before the
    layer exists and False afterwards, which silently wrote an all-zero group
    and made every group look like it had been deleted. Indices survive, since
    creating a layer does not touch geometry.
    """
    domains = ((bm.verts.layers.int, GROUP_PREFIXES[0], "verts"),
               (bm.edges.layers.int, GROUP_PREFIXES[1], "edges"),
               (bm.faces.layers.int, GROUP_PREFIXES[2], "faces"))
    wanted = {kind: {e.index for e in resolved.get(kind, [])} for _, _, kind in domains}
    for coll, prefix, kind in domains:
        key = prefix + name
        if coll.get(key) is None:
            coll.new(key)
    for coll, prefix, kind in domains:
        layer = coll.get(prefix + name)
        seq = {"verts": bm.verts, "edges": bm.edges, "faces": bm.faces}[kind]
        seq.index_update()
        members = wanted[kind]
        for elem in seq:
            elem[layer] = 1 if elem.index in members else 0


def _gid_maps(bm, layers):
    vl, el, fl = layers
    return ({v[vl]: v for v in bm.verts},
            {e[el]: e for e in bm.edges},
            {f[fl]: f for f in bm.faces})


# ── selection ────────────────────────────────────────────────────────────────

_AXIS_VECTORS = {
    "+x": mathutils.Vector((1, 0, 0)), "-x": mathutils.Vector((-1, 0, 0)),
    "+y": mathutils.Vector((0, 1, 0)), "-y": mathutils.Vector((0, -1, 0)),
    "+z": mathutils.Vector((0, 0, 1)), "-z": mathutils.Vector((0, 0, -1)),
}


def resolve_selection(state, bm, layers, sel):
    """Turn a selection spec into concrete bmesh elements.

    Returns ``{"verts": [...], "edges": [...], "faces": [...]}`` of live
    elements. Missing gids are reported rather than silently skipped: an agent
    that addresses geometry which no longer exists has made a real mistake, and
    a command that quietly does nothing is the worst possible answer.
    """
    vl, el, fl = layers
    by = sel.get("by")
    vmap, emap, fmap = _gid_maps(bm, layers)
    out = {"verts": [], "edges": [], "faces": []}

    if by == "all":
        return {"verts": list(bm.verts), "edges": list(bm.edges), "faces": list(bm.faces)}
    if by == "none":
        return out

    if by == "group":
        name = sel["name"]
        vg, eg, fg = _group_layers(bm, name)
        if vg is None and eg is None and fg is None:
            raise EngineError("no group %r; known groups: %s"
                              % (name, ", ".join(_group_names(bm)) or "(none)"))
        out["verts"] = [v for v in bm.verts if vg and v[vg]]
        out["edges"] = [e for e in bm.edges if eg and e[eg]]
        out["faces"] = [f for f in bm.faces if fg and f[fg]]
        if not any(out.values()):
            raise EngineError("group %r has no surviving elements (every element it "
                              "named was removed by a later command)" % name)
        return out

    if by in ("ids", "last_created", "last_selection"):
        if by == "ids":
            wanted = {k: sel.get(k, []) for k in ("verts", "edges", "faces")}
        elif by == "last_created":
            wanted = state.last_created
        else:
            wanted = state.last_selection
        missing = []
        for kind, table in (("verts", vmap), ("edges", emap), ("faces", fmap)):
            for gid in wanted.get(kind, []) or []:
                elem = table.get(gid)
                if elem is None:
                    missing.append("%s:%s" % (kind[:-1], gid))
                else:
                    out[kind].append(elem)
        if missing:
            raise EngineError("these elements no longer exist: %s%s"
                              % (", ".join(missing[:12]),
                                 " (+%d more)" % (len(missing) - 12) if len(missing) > 12 else ""))
        return out

    if by == "normal_axis":
        axis = _AXIS_VECTORS[sel["axis"]]
        threshold = 1.0 - float(sel.get("tolerance", 0.25))
        out["faces"] = [f for f in bm.faces if f.normal.length > 0
                        and f.normal.normalized().dot(axis) >= threshold]
        return _fill_from_faces(out)

    if by == "bbox":
        lo, hi = sel["min"], sel["max"]

        def inside(v):
            return all(lo[i] - 1e-9 <= v.co[i] <= hi[i] + 1e-9 for i in range(3))

        mode = sel.get("mode", "faces")
        if mode == "verts":
            out["verts"] = [v for v in bm.verts if inside(v)]
        elif mode == "edges":
            out["edges"] = [e for e in bm.edges if all(inside(v) for v in e.verts)]
            out["verts"] = sorted({v for e in out["edges"] for v in e.verts}, key=lambda v: v.index)
        else:
            out["faces"] = [f for f in bm.faces if all(inside(v) for v in f.verts)]
            return _fill_from_faces(out)
        return out

    if by == "boundary":
        out["edges"] = [e for e in bm.edges if len(e.link_faces) == 1]
        out["verts"] = sorted({v for e in out["edges"] for v in e.verts}, key=lambda v: v.index)
        return out

    if by == "non_manifold":
        out["edges"] = [e for e in bm.edges if len(e.link_faces) not in (1, 2)]
        out["verts"] = [v for v in bm.verts if not v.is_manifold]
        return out

    raise EngineError("unsupported selection %r" % by)


def _fill_from_faces(out):
    faces = out["faces"]
    out["verts"] = sorted({v for f in faces for v in f.verts}, key=lambda v: v.index)
    out["edges"] = sorted({e for f in faces for e in f.edges}, key=lambda e: e.index)
    return out


def _sel_gids(resolved, layers):
    vl, el, fl = layers
    return {"verts": [v[vl] for v in resolved["verts"]],
            "edges": [e[el] for e in resolved["edges"]],
            "faces": [f[fl] for f in resolved["faces"]]}


def _need_faces(resolved, cmd):
    if not resolved["faces"]:
        raise EngineError(
            "%s needs faces, and the selection resolved to none "
            "(verts=%d, edges=%d). Select faces with {\"by\":\"normal_axis\"}, "
            "{\"by\":\"ids\",\"faces\":[...]} or {\"by\":\"last_created\"}."
            % (cmd, len(resolved["verts"]), len(resolved["edges"])))
    return resolved["faces"]


# ── stats & validation ───────────────────────────────────────────────────────

def _stats(bm):
    bbox_min = [float("inf")] * 3
    bbox_max = [float("-inf")] * 3
    for v in bm.verts:
        for i in range(3):
            bbox_min[i] = min(bbox_min[i], v.co[i])
            bbox_max[i] = max(bbox_max[i], v.co[i])
    if not bm.verts:
        bbox_min = bbox_max = [0.0, 0.0, 0.0]
    tris = sum(max(0, len(f.verts) - 2) for f in bm.faces)
    return {
        "verts": len(bm.verts), "edges": len(bm.edges), "faces": len(bm.faces),
        "tris": tris,
        "bbox_min": [round(v, 6) for v in bbox_min],
        "bbox_max": [round(v, 6) for v in bbox_max],
        "dimensions": [round(bbox_max[i] - bbox_min[i], 6) for i in range(3)],
    }


def _validate(bm, layers, full=False):
    """Mesh health, in the terms a 3D pipeline actually cares about.

    Cheap checks run after every mutation. ``full`` adds self-intersection,
    which is BVH work proportional to the mesh and not worth paying per command.
    Every count comes with a sample of gids so the agent can fix the actual
    elements instead of guessing.
    """
    vl, el, fl = layers
    boundary, non_manifold_e, wire = [], [], []
    for e in bm.edges:
        n = len(e.link_faces)
        if n == 0:
            wire.append(e[el])
        elif n == 1:
            boundary.append(e[el])
        elif n > 2:
            non_manifold_e.append(e[el])

    loose_v = [v[vl] for v in bm.verts if not v.link_edges]
    non_manifold_v = [v[vl] for v in bm.verts if v.link_edges and not v.is_manifold]
    degenerate_f = [f[fl] for f in bm.faces if f.calc_area() < EPS_AREA]
    degenerate_e = [e[el] for e in bm.edges if e.calc_length() < EPS_LEN]

    dup = bmesh.ops.find_doubles(bm, verts=list(bm.verts), dist=1e-6)
    duplicate_v = [v[vl] for v in dup.get("targetmap", {}).keys()]

    report = {
        "boundary_edges": _count(boundary),
        "non_manifold_edges": _count(non_manifold_e),
        "wire_edges": _count(wire),
        "non_manifold_verts": _count(non_manifold_v),
        "loose_verts": _count(loose_v),
        "degenerate_faces": _count(degenerate_f),
        "degenerate_edges": _count(degenerate_e),
        "duplicate_verts": _count(duplicate_v),
    }
    watertight = not boundary and not non_manifold_e and not wire and bool(bm.faces)
    report["watertight"] = watertight
    report["volume"] = round(bm.calc_volume(signed=True), 8) if watertight else None

    if full:
        report["self_intersections"] = _self_intersections(bm, fl)

    problems = [k for k, v in report.items()
                if isinstance(v, dict) and v.get("count") and k != "boundary_edges"]
    report["ok"] = not problems
    report["problems"] = problems
    return report


def _count(gids):
    return {"count": len(gids), "ids": sorted(gids)[:protocol.MAX_REPORTED_IDS]}


def _self_intersections(bm, fl):
    """Faces that pass through each other.

    Pairs sharing a vertex are dropped: neighbours touch along their shared
    edge by construction, and reporting those would bury the real hits.
    """
    bm.faces.ensure_lookup_table()
    try:
        tree = BVHTree.FromBMesh(bm, epsilon=1e-6)
    except Exception as exc:
        return {"count": None, "error": str(exc)}
    hits = []
    for i, j in tree.overlap(tree):
        if i >= j:
            continue
        fa, fb = bm.faces[i], bm.faces[j]
        if set(fa.verts) & set(fb.verts):
            continue
        hits.append((fa[fl], fb[fl]))
    return {"count": len(hits), "pairs": hits[:protocol.MAX_REPORTED_IDS]}


def _check_budget(bm):
    if len(bm.verts) > protocol.LIMITS["max_verts"]:
        raise EngineError("vertex budget exceeded: %d > %d"
                          % (len(bm.verts), int(protocol.LIMITS["max_verts"])))
    if len(bm.faces) > protocol.LIMITS["max_faces"]:
        raise EngineError("face budget exceeded: %d > %d"
                          % (len(bm.faces), int(protocol.LIMITS["max_faces"])))


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_ping(args):
    return {"pong": True, "blender": bpy.app.version_string,
            "protocol": protocol.PROTOCOL_VERSION}


def cmd_state(args):
    return {
        "revision": STATE["revision"],
        "commands": STATE["commands"],
        "uptime_s": round(time.time() - STATE["started_at"], 1),
        "objects": sorted(STATE["objects"]),
    }


def cmd_mesh_new(args):
    object_id = args["object_id"]
    existing = bpy.data.objects.get(object_id)
    if existing is not None:
        if not args["replace"]:
            raise EngineError("object %r already exists; pass replace=true to rebuild it"
                              % object_id)
        _remove_object(object_id)
    if len(STATE["objects"]) >= protocol.LIMITS["max_objects"]:
        raise EngineError("object budget exceeded (%d)" % int(protocol.LIMITS["max_objects"]))

    state = ObjectState(object_id)
    mesh = bpy.data.meshes.new(object_id)
    obj = bpy.data.objects.new(object_id, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = args["location"]

    bm = bmesh.new()
    kind = args["primitive"]
    size = args["size"]
    segments = args["segments"]
    radius = args.get("radius", size / 2.0)
    depth = args.get("depth", size)
    if kind == "cube":
        bmesh.ops.create_cube(bm, size=size)
    elif kind == "plane":
        bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=size / 2.0)
    elif kind == "circle":
        bmesh.ops.create_circle(bm, cap_ends=True, radius=radius, segments=segments)
    elif kind in ("cylinder", "cone"):
        r2 = args.get("radius2", radius if kind == "cylinder" else 0.0)
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segments,
                              radius1=radius, radius2=r2, depth=depth)
    elif kind == "uv_sphere":
        bmesh.ops.create_uvsphere(bm, u_segments=segments,
                                  v_segments=max(3, segments // 2), radius=radius)
    elif kind == "ico_sphere":
        bmesh.ops.create_icosphere(bm, subdivisions=min(4, max(1, segments // 12)),
                                   radius=radius)
    else:  # torus
        bmesh.ops.create_torus(bm, major_radius=radius, minor_radius=radius / 3.0,
                               major_segments=segments, minor_segments=max(3, segments // 3))

    layers = _layers(bm)
    _sweep_gids(state, bm, layers)
    if args.get("dimensions"):
        _fit_dimensions(bm, args["dimensions"])
    _check_budget(bm)
    stats = _stats(bm)
    validation = _validate(bm, layers)
    all_gids = {"verts": [v[layers[0]] for v in bm.verts],
                "edges": [e[layers[1]] for e in bm.edges],
                "faces": [f[layers[2]] for f in bm.faces]}
    state.last_created = all_gids
    state.last_selection = all_gids
    _commit(obj, bm)
    STATE["objects"][object_id] = state
    STATE["revision"] += 1
    return {"object_id": object_id, "revision": STATE["revision"],
            "created": _truncate(all_gids),
            "created_counts": {k: len(v) for k, v in all_gids.items()},
            "stats": stats, "validation": validation}


def _fit_dimensions(bm, dims):
    """Scale a fresh primitive so its bounding box matches ``dimensions``.

    Authoring in real dimensions is how people describe objects ("the body is
    4.2 long"), and it saves the agent a scale command it often gets wrong.
    """
    lo = [min(v.co[i] for v in bm.verts) for i in range(3)]
    hi = [max(v.co[i] for v in bm.verts) for i in range(3)]
    factor = []
    for i in range(3):
        span = hi[i] - lo[i]
        factor.append(dims[i] / span if span > 1e-9 and dims[i] > 0 else 1.0)
    bmesh.ops.scale(bm, vec=mathutils.Vector(factor), verts=list(bm.verts))


def cmd_mesh_select(args):
    object_id = args["object_id"]
    state = _state(object_id)
    obj, bm = _open(object_id)
    layers = _layers(bm)
    name = args.get("store_as")
    try:
        resolved = resolve_selection(state, bm, layers, args["selection"])
        gids = _sel_gids(resolved, layers)
        state.last_selection = gids
        if name:
            _write_group(bm, name, resolved)
            state.groups.add(name)
            _commit(obj, bm)          # membership lives in the mesh, so persist it
        else:
            bm.free()
    except Exception:
        bm.free()
        raise
    return {"object_id": object_id, "selection": _truncate(gids),
            "counts": {k: len(v) for k, v in gids.items()},
            "stored_as": name or None}


def _mutate(object_id, fn, *, full_validation=False):
    """Run a geometry mutation atomically.

    The mesh datablock is copied first and swapped back if anything raises, so
    a failed operator can never leave a half-built mesh behind for the next
    command to trip over. On success the backup is dropped and the revision
    advances.
    """
    state = _state(object_id)
    obj = _obj(object_id)
    backup = obj.data.copy()
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        layers = _layers(bm)
        created_elems = fn(state, bm, layers)
        _sweep_gids(state, bm, layers)
        created = _elems_to_gids(created_elems, layers)
        _check_budget(bm)
        stats = _stats(bm)
        validation = _validate(bm, layers, full=full_validation)
        _commit(obj, bm)
    except Exception:
        bm.free()
        old = obj.data
        obj.data = backup
        bpy.data.meshes.remove(old)
        raise
    bpy.data.meshes.remove(backup)
    STATE["revision"] += 1
    state.last_created = created
    state.last_selection = created
    return {"object_id": object_id, "revision": STATE["revision"],
            "created": _truncate(created),
            "created_counts": {k: len(v) for k, v in created.items()},
            "stats": stats, "validation": validation}


def _truncate(gids):
    n = protocol.MAX_REPORTED_IDS
    return {k: v[:n] for k, v in gids.items()}


def cmd_mesh_extrude(args):
    def run(state, bm, layers):
        resolved = resolve_selection(state, bm, layers, args["selection"])
        faces = _need_faces(resolved, "mesh_extrude")
        offset = mathutils.Vector(args["translate"])
        if args["individual"]:
            res = bmesh.ops.extrude_discrete_faces(bm, faces=faces)
            new_faces = res["faces"]
            for f in new_faces:
                vec = offset + f.normal * args["along_normal"]
                bmesh.ops.translate(bm, verts=list(f.verts), vec=vec)
            geom = list(new_faces) + [v for f in new_faces for v in f.verts] \
                + [e for f in new_faces for e in f.edges]
            return _collect(state, bm, layers, geom)

        # The region path: extrude, move the new shell, then delete the faces
        # that were extruded from. Skipping that delete is the classic way to
        # end up with interior faces and a non-manifold result one command later.
        normal = mathutils.Vector((0, 0, 0))
        for f in faces:
            normal += f.normal
        if normal.length > 1e-9:
            normal.normalize()
        res = bmesh.ops.extrude_face_region(bm, geom=faces)
        geom = res["geom"]
        new_verts = [g for g in geom if isinstance(g, bmesh.types.BMVert)]
        bmesh.ops.translate(bm, verts=new_verts,
                            vec=offset + normal * args["along_normal"])
        bmesh.ops.delete(bm, geom=faces, context="FACES")
        return _collect(state, bm, layers, geom)

    return _mutate(args["object_id"], run)


def cmd_mesh_inset(args):
    def run(state, bm, layers):
        resolved = resolve_selection(state, bm, layers, args["selection"])
        faces = _need_faces(resolved, "mesh_inset")
        if args["individual"]:
            res = bmesh.ops.inset_individual(bm, faces=faces, thickness=args["thickness"],
                                             depth=args["depth"], use_even_offset=True)
        else:
            res = bmesh.ops.inset_region(bm, faces=faces, thickness=args["thickness"],
                                         depth=args["depth"], use_even_offset=True,
                                         use_boundary=True)
        # inset keeps the input faces as the *inner* faces and returns the new
        # rim. The inner faces are what an agent means next ("now extrude it"),
        # so they are what becomes last_created.
        _collect(state, bm, layers, list(res.get("faces", [])))
        return {"faces": list(faces),
                "verts": sorted({v for f in faces for v in f.verts}, key=lambda v: v.index),
                "edges": sorted({e for f in faces for e in f.edges}, key=lambda e: e.index)}

    return _mutate(args["object_id"], run)


def cmd_mesh_bevel(args):
    def run(state, bm, layers):
        resolved = resolve_selection(state, bm, layers, args["selection"])
        if args["affect"] == "verts":
            geom = resolved["verts"]
            what = "vertices"
        else:
            geom = resolved["edges"] or sorted(
                {e for f in resolved["faces"] for e in f.edges}, key=lambda e: e.index)
            what = "edges"
        if not geom:
            raise EngineError("mesh_bevel found no %s in the selection" % what)
        res = bmesh.ops.bevel(bm, geom=geom, offset=args["offset"],
                              segments=args["segments"], profile=args["profile"],
                              affect="VERTICES" if args["affect"] == "verts" else "EDGES",
                              clamp_overlap=args["clamp"], offset_type="OFFSET")
        return _collect(state, bm, layers,
                        list(res.get("faces", [])) + list(res.get("edges", []))
                        + list(res.get("verts", [])))

    return _mutate(args["object_id"], run)


def cmd_mesh_transform(args):
    def run(state, bm, layers):
        resolved = resolve_selection(state, bm, layers, args["selection"])
        verts = resolved["verts"] or sorted(
            {v for f in resolved["faces"] for v in f.verts}, key=lambda v: v.index)
        if not verts:
            raise EngineError("mesh_transform resolved to no vertices")
        if args["pivot"] == "origin":
            pivot = mathutils.Vector((0, 0, 0))
        elif args["pivot"] == "point":
            pivot = mathutils.Vector(args["point"])
        else:
            pivot = sum((v.co for v in verts), mathutils.Vector()) / len(verts)

        scale = mathutils.Vector(args["scale"])
        if tuple(scale) != (1.0, 1.0, 1.0):
            bmesh.ops.scale(bm, vec=scale, space=mathutils.Matrix.Translation(-pivot),
                            verts=verts)
        rot = args["rotate"]
        if any(abs(a) > 1e-12 for a in rot):
            mat = mathutils.Euler([math.radians(a) for a in rot], "XYZ").to_matrix()
            bmesh.ops.rotate(bm, cent=pivot, matrix=mat, verts=verts)
        move = mathutils.Vector(args["translate"])
        if move.length > 1e-12:
            bmesh.ops.translate(bm, verts=verts, vec=move)
        # A transform creates nothing; what it touched is the useful answer.
        return resolved if (resolved["faces"] or resolved["edges"]) else \
            {"verts": verts, "edges": [], "faces": []}

    return _mutate(args["object_id"], run)


_DELETE_CONTEXT = {"verts": "VERTS", "edges": "EDGES", "faces": "FACES",
                   "faces_only": "FACES_ONLY", "faces_keep_boundary": "FACES_KEEP_BOUNDARY"}


def cmd_mesh_delete(args):
    def run(state, bm, layers):
        resolved = resolve_selection(state, bm, layers, args["selection"])
        mode = args["mode"]
        geom = resolved["verts"] if mode == "verts" else (
            resolved["edges"] if mode == "edges" else resolved["faces"])
        if not geom:
            raise EngineError("mesh_delete resolved to no %s" % mode)
        bmesh.ops.delete(bm, geom=geom, context=_DELETE_CONTEXT[mode])
        return {"verts": [], "edges": [], "faces": []}

    return _mutate(args["object_id"], run)


def cmd_mesh_subdivide(args):
    def run(state, bm, layers):
        resolved = resolve_selection(state, bm, layers, args["selection"])
        edges = resolved["edges"] or sorted(
            {e for f in resolved["faces"] for e in f.edges}, key=lambda e: e.index)
        if not edges:
            raise EngineError("mesh_subdivide resolved to no edges")
        res = bmesh.ops.subdivide_edges(bm, edges=edges, cuts=args["cuts"],
                                        smooth=args["smooth"], use_grid_fill=True)
        return _collect(state, bm, layers, res.get("geom_inner", []) + res.get("geom_split", []))

    return _mutate(args["object_id"], run)


def cmd_mesh_merge(args):
    def run(state, bm, layers):
        resolved = resolve_selection(state, bm, layers, args["selection"])
        verts = resolved["verts"] or list(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=verts, dist=args["distance"])
        return {"verts": [], "edges": [], "faces": []}

    return _mutate(args["object_id"], run)


def cmd_mesh_normals(args):
    def run(state, bm, layers):
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        if args["inside"]:
            bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
        return {"verts": [], "edges": [], "faces": []}

    return _mutate(args["object_id"], run)


def cmd_mesh_group(args):
    object_id = args["object_id"]
    state = _state(object_id)
    obj, bm = _open(object_id)
    layers = _layers(bm)
    try:
        resolved = resolve_selection(state, bm, layers, args["selection"])
        gids = _sel_gids(resolved, layers)
        if not any(gids.values()):
            raise EngineError("group %r would be empty" % args["name"])
        _write_group(bm, args["name"], resolved)
        names = _group_names(bm)
        _commit(obj, bm)
    except Exception:
        bm.free()
        raise
    state.groups.add(args["name"])
    return {"object_id": object_id, "group": args["name"],
            "counts": {k: len(v) for k, v in gids.items()}, "groups": names}


def cmd_mesh_stats(args):
    object_id = args["object_id"]
    _state(object_id)
    obj, bm = _open(object_id)
    try:
        stats = _stats(bm)
        groups = _group_names(bm)
    finally:
        bm.free()
    return {"object_id": object_id, "stats": stats,
            "groups": groups, "revision": STATE["revision"]}


def cmd_mesh_validate(args):
    object_id = args["object_id"]
    _state(object_id)
    obj, bm = _open(object_id)
    layers = _layers(bm)
    try:
        report = _validate(bm, layers, full=args["full"])
        stats = _stats(bm)
    finally:
        bm.free()
    return {"object_id": object_id, "validation": report, "stats": stats}


# ── export & preview ─────────────────────────────────────────────────────────

def _select_only(obj):
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def cmd_mesh_export(args):
    object_id = args["object_id"]
    _state(object_id)
    obj = _obj(object_id)
    path = args["path"]
    fmt = args["format"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _select_only(obj)
    if fmt in ("glb", "gltf"):
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB" if fmt == "glb" else "GLTF_SEPARATE",
                                  use_selection=True, export_apply=True)
    elif fmt == "obj":
        bpy.ops.wm.obj_export(filepath=path, export_selected_objects=True)
    elif fmt == "stl":
        bpy.ops.wm.stl_export(filepath=path, export_selected_objects=True)
    elif fmt == "ply":
        bpy.ops.wm.ply_export(filepath=path, export_selected_objects=True)
    else:
        raise EngineError("unsupported export format %r" % fmt)
    if not os.path.exists(path):
        raise EngineError("export produced no file at %s" % path)
    return {"object_id": object_id, "path": path, "format": fmt,
            "bytes": os.path.getsize(path), "revision": STATE["revision"]}


_ANGLE_DIRS = {
    "front": (0.0, -1.0, 0.0), "back": (0.0, 1.0, 0.0),
    "left": (-1.0, 0.0, 0.0), "right": (1.0, 0.0, 0.0),
    "top": (0.0, 0.0, 1.0), "bottom": (0.0, 0.0, -1.0),
    "persp": (1.0, -1.0, 0.7),
}


def cmd_mesh_preview(args):
    """Render the object from a few angles.

    This is the agent's only way to *see* what it built: validation proves the
    mesh is sound, not that it looks like the thing that was asked for. Rendered
    with Workbench, which needs no materials, no lighting setup and no GPU-bound
    engine features.
    """
    object_id = args["object_id"]
    _state(object_id)
    obj = _obj(object_id)
    scene = bpy.context.scene
    size = args["size"]

    cam_data = bpy.data.cameras.new("geom_preview_cam")
    cam = bpy.data.objects.new("geom_preview_cam", cam_data)
    scene.collection.objects.link(cam)
    prev_cam = scene.camera
    scene.camera = cam

    bb = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
    center = sum(bb, mathutils.Vector()) / len(bb)
    radius = max((v - center).length for v in bb) or 1.0

    prev = (scene.render.engine, scene.render.filepath, scene.render.image_settings.file_format,
            scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage)
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.image_settings.file_format = "PNG"
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100

    base, ext = os.path.splitext(args["path"])
    ext = ext or ".png"
    os.makedirs(os.path.dirname(args["path"]), exist_ok=True)
    written = []
    try:
        for angle in args["angles"]:
            direction = mathutils.Vector(_ANGLE_DIRS[angle]).normalized()
            cam.location = center + direction * (radius * 3.2)
            cam.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
            out = "%s_%s%s" % (base, angle, ext) if len(args["angles"]) > 1 else base + ext
            scene.render.filepath = out
            bpy.ops.render.render(write_still=True)
            if os.path.exists(out):
                written.append({"angle": angle, "path": out, "bytes": os.path.getsize(out)})
    finally:
        (scene.render.engine, scene.render.filepath, scene.render.image_settings.file_format,
         scene.render.resolution_x, scene.render.resolution_y,
         scene.render.resolution_percentage) = prev
        scene.camera = prev_cam
        bpy.data.objects.remove(cam, do_unlink=True)
        bpy.data.cameras.remove(cam_data)
    if not written:
        raise EngineError("preview render produced no files")
    return {"object_id": object_id, "images": written}


def _remove_object(object_id):
    obj = bpy.data.objects.get(object_id)
    if obj is not None:
        mesh = obj.data if obj.type == "MESH" else None
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    STATE["objects"].pop(object_id, None)


def cmd_object_delete(args):
    object_id = args["object_id"]
    _state(object_id)
    _remove_object(object_id)
    STATE["revision"] += 1
    return {"object_id": object_id, "deleted": True, "revision": STATE["revision"]}


def cmd_snapshot(args):
    """Save the whole session as a .blend.

    The revision log is what makes history replayable; a snapshot is what keeps
    replay from getting linearly slower as the log grows.
    """
    path = args["path"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    return {"path": path, "bytes": os.path.getsize(path) if os.path.exists(path) else 0,
            "revision": STATE["revision"]}


HANDLERS = {
    "ping": cmd_ping,
    "state": cmd_state,
    "mesh_new": cmd_mesh_new,
    "mesh_select": cmd_mesh_select,
    "mesh_extrude": cmd_mesh_extrude,
    "mesh_inset": cmd_mesh_inset,
    "mesh_bevel": cmd_mesh_bevel,
    "mesh_transform": cmd_mesh_transform,
    "mesh_delete": cmd_mesh_delete,
    "mesh_subdivide": cmd_mesh_subdivide,
    "mesh_merge": cmd_mesh_merge,
    "mesh_normals": cmd_mesh_normals,
    "mesh_group": cmd_mesh_group,
    "mesh_stats": cmd_mesh_stats,
    "mesh_validate": cmd_mesh_validate,
    "mesh_export": cmd_mesh_export,
    "mesh_preview": cmd_mesh_preview,
    "object_delete": cmd_object_delete,
    "snapshot": cmd_snapshot,
}


# ── server ───────────────────────────────────────────────────────────────────

def handle(frame):
    req_id = frame.get("id")
    cmd = frame.get("cmd")
    started = time.time()
    try:
        args = protocol.validate(cmd, frame.get("args"))
    except protocol.ProtocolError as exc:
        return {"id": req_id, "ok": False, "error": str(exc), "error_kind": "protocol"}
    try:
        result = HANDLERS[cmd](args)
    except EngineError as exc:
        return {"id": req_id, "ok": False, "error": str(exc), "error_kind": "engine"}
    except Exception as exc:
        # An operator that blew up inside Blender: the message alone rarely says
        # which call it was, so the traceback goes to stderr (the daemon's log)
        # while the agent gets a clean one-liner.
        traceback.print_exc(file=sys.stderr)
        return {"id": req_id, "ok": False, "error": "%s: %s" % (type(exc).__name__, exc),
                "error_kind": "engine"}
    STATE["commands"] += 1
    return {"id": req_id, "ok": True, "result": result,
            "ms": int((time.time() - started) * 1000)}


def serve(sock_path, idle_timeout):
    """Accept connections and answer frames, one command at a time.

    Several clients may be connected at once — an agent mid-build and the
    connector page asking what is running — but frames are handled strictly
    serially, because ``bpy`` is not reentrant. Multiplexing the *connections*
    while serializing the *work* is the whole trick: a single-connection server
    made the status probe hang behind whatever an agent was doing, so the UI
    could only see engines that were idle.
    """
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    os.makedirs(os.path.dirname(sock_path), exist_ok=True)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    os.chmod(sock_path, 0o600)
    srv.listen(8)
    srv.setblocking(False)

    sel = selectors.DefaultSelector()
    sel.register(srv, selectors.EVENT_READ, None)
    buffers = {}

    # The daemon waits for this line rather than polling the socket file, so a
    # slow Blender start is never mistaken for a failed one.
    sys.stdout.write("BLENDER_GEOM_READY %s\n" % bpy.app.version_string)
    sys.stdout.flush()

    last_activity = time.time()
    running = True
    while running:
        for key, _ in sel.select(timeout=1.0):
            if key.data is None:
                try:
                    conn, _addr = srv.accept()
                except OSError:
                    continue
                conn.setblocking(False)
                sel.register(conn, selectors.EVENT_READ, "client")
                buffers[conn] = b""
                continue

            conn = key.fileobj
            try:
                chunk = conn.recv(65536)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                chunk = b""
            if not chunk:
                _close(sel, buffers, conn)
                continue

            last_activity = time.time()
            buffers[conn] += chunk
            while b"\n" in buffers[conn]:
                line, buffers[conn] = buffers[conn].split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    frame = protocol.decode(line)
                except Exception as exc:
                    _send(conn, {"ok": False, "error": "bad frame: %s" % exc,
                                 "error_kind": "protocol"})
                    continue
                if frame.get("cmd") == "shutdown":
                    _send(conn, {"id": frame.get("id"), "ok": True, "result": {"bye": True}})
                    running = False
                    break
                _send(conn, handle(frame))
                last_activity = time.time()
            if not running:
                break

        if idle_timeout and time.time() - last_activity > idle_timeout:
            sys.stderr.write("[geom] idle for %ss, exiting\n" % idle_timeout)
            break

    for conn in list(buffers):
        _close(sel, buffers, conn)
    sel.close()
    srv.close()
    try:
        os.unlink(sock_path)
    except OSError:
        pass


def _send(conn, payload):
    try:
        conn.sendall(protocol.encode(payload))
    except OSError:
        pass          # the client went away mid-answer; the next recv reaps it


def _close(sel, buffers, conn):
    try:
        sel.unregister(conn)
    except (KeyError, ValueError):
        pass
    buffers.pop(conn, None)
    try:
        conn.close()
    except OSError:
        pass


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    sock_path = ""
    idle_timeout = 900.0
    for i, a in enumerate(argv):
        if a == "--socket" and i + 1 < len(argv):
            sock_path = argv[i + 1]
        elif a == "--idle-timeout" and i + 1 < len(argv):
            idle_timeout = float(argv[i + 1])
    if not sock_path:
        sys.stderr.write("[geom] --socket is required\n")
        sys.exit(2)

    # Start from an empty file: the factory scene's cube, camera and light would
    # otherwise land in every export.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    serve(sock_path, idle_timeout)


if __name__ == "__main__":
    main()
