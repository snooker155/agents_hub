"""
The Blender geometry command protocol.

This module is the contract between the host script running *inside* Blender
(``host/main.py``) and the client driving it (``daemon.py``). It is deliberately
**stdlib-only and project-import-free**: Blender ships its own Python
interpreter with no pydantic and no access to this project's dependencies, so
anything the host imports has to stand on its own.

Two things live here:

- :data:`COMMANDS` — the whitelist. The agent never sends Blender Python; it
  sends one of these named commands with arguments that are validated against
  this table before they reach ``bpy``. A command that is not in the table does
  not exist, which is the whole security model in one sentence.
- :data:`LIMITS` — the ceilings. Geometry operators are the easy way to hang or
  OOM a process (a subdivide with 12 cuts, a bevel with 500 segments), so every
  unbounded number an agent can send is clamped here rather than in the host.

Wire format: newline-delimited JSON over a Unix socket, one request per line,
one response per line.

    → {"id": "c7", "cmd": "mesh_extrude", "args": {...}}
    ← {"id": "c7", "ok": true, "result": {...}}
    ← {"id": "c7", "ok": false, "error": "...", "error_kind": "protocol"}

Responses are never free-form: a failed command reports ``error_kind`` so the
caller can tell an agent mistake (``protocol``, fix the arguments and retry)
from an engine failure (``engine``, the operator itself blew up).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


PROTOCOL_VERSION = 1


class ProtocolError(ValueError):
    """An argument failed validation. The message is written to be handed back
    to the agent verbatim, so it names the field and what was expected."""


# ── ceilings ─────────────────────────────────────────────────────────────────
# Chosen so that a single bad command cannot take the daemon down: every one of
# these is a number an agent could plausibly get wrong by an order of magnitude.

LIMITS: Dict[str, float] = {
    "max_verts": 200_000,        # per object, checked after every mutation
    "max_faces": 200_000,
    "max_objects": 32,           # per daemon
    "max_bevel_segments": 16,
    "max_segments": 128,         # primitive resolution
    "max_subdivide_cuts": 6,
    "max_coord": 10_000.0,       # any position / translation component
    "max_selection_ids": 20_000,
    "max_preview_px": 1600,
    "max_preview_angles": 6,
}

#: How many element ids a response carries before it is truncated. A full id
#: list for a 50k-vertex mesh is useless to an agent and expensive in context;
#: the counts stay exact, the ids become a sample.
MAX_REPORTED_IDS = 64


# ── argument types ───────────────────────────────────────────────────────────

_PRIMITIVES = ("cube", "plane", "circle", "cylinder", "cone", "uv_sphere", "ico_sphere", "torus")
_SELECT_BY = ("ids", "last_created", "last_selection", "all", "none",
              "normal_axis", "bbox", "group", "boundary", "non_manifold")
_DELETE_MODES = ("verts", "edges", "faces", "faces_only", "faces_keep_boundary")
_BEVEL_AFFECT = ("edges", "verts")
_EXPORT_FORMATS = ("glb", "gltf", "obj", "stl", "ply")
_AXES = ("+x", "-x", "+y", "-y", "+z", "-z")


def _as_float(value: Any, field: str, *, lo: Optional[float] = None,
              hi: Optional[float] = None) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ProtocolError(f"{field}: expected a number, got {value!r}")
    if out != out or out in (float("inf"), float("-inf")):
        raise ProtocolError(f"{field}: must be finite")
    if lo is not None and out < lo:
        raise ProtocolError(f"{field}: must be >= {lo} (got {out})")
    if hi is not None and out > hi:
        raise ProtocolError(f"{field}: must be <= {hi} (got {out})")
    return out


def _as_int(value: Any, field: str, *, lo: Optional[int] = None,
            hi: Optional[int] = None) -> int:
    if isinstance(value, bool):
        raise ProtocolError(f"{field}: expected an integer, got a boolean")
    try:
        out = int(value)
    except (TypeError, ValueError):
        raise ProtocolError(f"{field}: expected an integer, got {value!r}")
    if lo is not None and out < lo:
        raise ProtocolError(f"{field}: must be >= {lo} (got {out})")
    if hi is not None and out > hi:
        raise ProtocolError(f"{field}: must be <= {hi} (got {out})")
    return out


def _as_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise ProtocolError(f"{field}: expected true or false, got {value!r}")


def _as_vec3(value: Any, field: str, *, default: Optional[Tuple[float, float, float]] = None,
             limit: Optional[float] = None) -> List[float]:
    if value is None and default is not None:
        return list(default)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ProtocolError(f"{field}: expected [x, y, z], got {value!r}")
    cap = LIMITS["max_coord"] if limit is None else limit
    return [_as_float(v, f"{field}[{i}]", lo=-cap, hi=cap) for i, v in enumerate(value)]


def _as_id_list(value: Any, field: str) -> List[int]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ProtocolError(f"{field}: expected a list of element ids")
    if len(value) > LIMITS["max_selection_ids"]:
        raise ProtocolError(f"{field}: too many ids ({len(value)} > {int(LIMITS['max_selection_ids'])})")
    return [_as_int(v, f"{field}[{i}]", lo=0) for i, v in enumerate(value)]


def _as_name(value: Any, field: str, *, allow_empty: bool = False) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        if allow_empty:
            return ""
        raise ProtocolError(f"{field}: required")
    if len(text) > 120:
        raise ProtocolError(f"{field}: too long (max 120 chars)")
    # Ids end up as Blender datablock names and as path fragments for exported
    # files, so keep them boring on purpose.
    ok = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")
    bad = sorted(set(text) - ok)
    if bad:
        raise ProtocolError(f"{field}: only letters, digits, '_', '-' and '.' are allowed "
                            f"(offending: {''.join(bad)!r})")
    return text


def _as_choice(value: Any, field: str, choices: Tuple[str, ...], default: Optional[str] = None) -> str:
    if value is None and default is not None:
        return default
    text = str(value or "").strip().lower()
    if text not in choices:
        raise ProtocolError(f"{field}: expected one of {', '.join(choices)} (got {value!r})")
    return text


def validate_selection(value: Any, field: str = "selection") -> Dict[str, Any]:
    """Validate a selection spec.

    Selection is its own little language because it is the part of a modeling
    API an agent gets wrong most often. Every form resolves, in the host, to
    concrete element ids against the object's current geometry:

    ``{"by": "last_created"}``      what the previous command made (the common case)
    ``{"by": "ids", "faces": [..]}`` explicit gids
    ``{"by": "normal_axis", "axis": "+z", "tolerance": 0.2}``
    ``{"by": "bbox", "min": [..], "max": [..]}``   nulls mean unbounded
    ``{"by": "group", "name": "roof"}``
    ``{"by": "boundary"}`` / ``{"by": "non_manifold"}`` / ``{"by": "all"}``
    """
    if value is None:
        return {"by": "last_created"}
    if isinstance(value, str):
        value = {"by": value}
    if not isinstance(value, dict):
        raise ProtocolError(f"{field}: expected an object like {{\"by\": \"last_created\"}}")

    by = _as_choice(value.get("by"), f"{field}.by", _SELECT_BY)
    out: Dict[str, Any] = {"by": by}

    if by == "ids":
        for kind in ("verts", "edges", "faces"):
            ids = _as_id_list(value.get(kind), f"{field}.{kind}")
            if ids:
                out[kind] = ids
        if not any(k in out for k in ("verts", "edges", "faces")):
            raise ProtocolError(f"{field}: 'ids' needs at least one of verts/edges/faces")
    elif by == "normal_axis":
        out["axis"] = _as_choice(value.get("axis"), f"{field}.axis", _AXES)
        out["tolerance"] = _as_float(value.get("tolerance", 0.25), f"{field}.tolerance", lo=0.0, hi=2.0)
    elif by == "bbox":
        out["min"] = _bbox_corner(value.get("min"), f"{field}.min", -LIMITS["max_coord"])
        out["max"] = _bbox_corner(value.get("max"), f"{field}.max", LIMITS["max_coord"])
        out["mode"] = _as_choice(value.get("mode"), f"{field}.mode", ("verts", "faces", "edges"), "faces")
    elif by == "group":
        out["name"] = _as_name(value.get("name"), f"{field}.name")
    return out


def _bbox_corner(value: Any, field: str, fill: float) -> List[float]:
    """A bbox corner where any component may be null, meaning unbounded.

    Half-open boxes are what an agent actually wants ("everything above z=0.4"),
    and making it spell out ±10000 for the other five components is how you get
    typos in the other five components.
    """
    if value is None:
        return [fill, fill, fill]
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ProtocolError(f"{field}: expected [x, y, z] with nulls for unbounded")
    cap = LIMITS["max_coord"]
    return [fill if v is None else _as_float(v, f"{field}[{i}]", lo=-cap, hi=cap)
            for i, v in enumerate(value)]


# ── the command whitelist ────────────────────────────────────────────────────
# Each entry is (validator, docstring). The validator returns the cleaned args
# dict the host will execute; it never mutates the input.

def _v_ping(a: Dict[str, Any]) -> Dict[str, Any]:
    return {}


def _v_mesh_new(a: Dict[str, Any]) -> Dict[str, Any]:
    out = {
        "object_id": _as_name(a.get("object_id"), "object_id"),
        "primitive": _as_choice(a.get("primitive"), "primitive", _PRIMITIVES, "cube"),
        "size": _as_float(a.get("size", 1.0), "size", lo=1e-4, hi=LIMITS["max_coord"]),
        "location": _as_vec3(a.get("location"), "location", default=(0.0, 0.0, 0.0)),
        "segments": _as_int(a.get("segments", 32), "segments", lo=3, hi=int(LIMITS["max_segments"])),
        "replace": _as_bool(a.get("replace", False), "replace"),
    }
    if a.get("dimensions") is not None:
        out["dimensions"] = _as_vec3(a.get("dimensions"), "dimensions")
    for key, lo, hi in (("radius", 1e-4, LIMITS["max_coord"]),
                        ("radius2", 0.0, LIMITS["max_coord"]),
                        ("depth", 1e-4, LIMITS["max_coord"])):
        if a.get(key) is not None:
            out[key] = _as_float(a.get(key), key, lo=lo, hi=hi)
    return out


def _v_mesh_select(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "object_id": _as_name(a.get("object_id"), "object_id"),
        "selection": validate_selection(a.get("selection")),
        "store_as": _as_name(a.get("store_as"), "store_as", allow_empty=True),
    }


def _v_mesh_extrude(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "object_id": _as_name(a.get("object_id"), "object_id"),
        "selection": validate_selection(a.get("selection")),
        "translate": _as_vec3(a.get("translate"), "translate", default=(0.0, 0.0, 0.0)),
        "along_normal": _as_float(a.get("along_normal", 0.0), "along_normal",
                                  lo=-LIMITS["max_coord"], hi=LIMITS["max_coord"]),
        "individual": _as_bool(a.get("individual", False), "individual"),
    }


def _v_mesh_inset(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "object_id": _as_name(a.get("object_id"), "object_id"),
        "selection": validate_selection(a.get("selection")),
        "thickness": _as_float(a.get("thickness", 0.1), "thickness", lo=0.0, hi=LIMITS["max_coord"]),
        "depth": _as_float(a.get("depth", 0.0), "depth",
                           lo=-LIMITS["max_coord"], hi=LIMITS["max_coord"]),
        "individual": _as_bool(a.get("individual", False), "individual"),
    }


def _v_mesh_bevel(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "object_id": _as_name(a.get("object_id"), "object_id"),
        "selection": validate_selection(a.get("selection")),
        "offset": _as_float(a.get("offset", 0.05), "offset", lo=0.0, hi=LIMITS["max_coord"]),
        "segments": _as_int(a.get("segments", 2), "segments", lo=1,
                            hi=int(LIMITS["max_bevel_segments"])),
        "profile": _as_float(a.get("profile", 0.5), "profile", lo=0.0, hi=1.0),
        "affect": _as_choice(a.get("affect"), "affect", _BEVEL_AFFECT, "edges"),
        "clamp": _as_bool(a.get("clamp", True), "clamp"),
    }


def _v_mesh_transform(a: Dict[str, Any]) -> Dict[str, Any]:
    out = {
        "object_id": _as_name(a.get("object_id"), "object_id"),
        "selection": validate_selection(a.get("selection")),
        "translate": _as_vec3(a.get("translate"), "translate", default=(0.0, 0.0, 0.0)),
        "rotate": _as_vec3(a.get("rotate"), "rotate", default=(0.0, 0.0, 0.0), limit=3600.0),
        "scale": _as_vec3(a.get("scale"), "scale", default=(1.0, 1.0, 1.0)),
        "pivot": _as_choice(a.get("pivot"), "pivot", ("center", "origin", "point"), "center"),
    }
    if out["pivot"] == "point":
        out["point"] = _as_vec3(a.get("point"), "point", default=(0.0, 0.0, 0.0))
    return out


def _v_mesh_delete(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "object_id": _as_name(a.get("object_id"), "object_id"),
        "selection": validate_selection(a.get("selection")),
        "mode": _as_choice(a.get("mode"), "mode", _DELETE_MODES, "faces"),
    }


def _v_mesh_subdivide(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "object_id": _as_name(a.get("object_id"), "object_id"),
        "selection": validate_selection(a.get("selection")),
        "cuts": _as_int(a.get("cuts", 1), "cuts", lo=1, hi=int(LIMITS["max_subdivide_cuts"])),
        "smooth": _as_float(a.get("smooth", 0.0), "smooth", lo=0.0, hi=1.0),
    }


def _v_mesh_merge(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "object_id": _as_name(a.get("object_id"), "object_id"),
        "selection": validate_selection(a.get("selection", {"by": "all"})),
        "distance": _as_float(a.get("distance", 0.0001), "distance", lo=0.0, hi=1.0),
    }


def _v_mesh_normals(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "object_id": _as_name(a.get("object_id"), "object_id"),
        "inside": _as_bool(a.get("inside", False), "inside"),
    }


def _v_mesh_group(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "object_id": _as_name(a.get("object_id"), "object_id"),
        "name": _as_name(a.get("name"), "name"),
        "selection": validate_selection(a.get("selection")),
    }


def _v_object_only(a: Dict[str, Any]) -> Dict[str, Any]:
    return {"object_id": _as_name(a.get("object_id"), "object_id")}


def _v_mesh_validate(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "object_id": _as_name(a.get("object_id"), "object_id"),
        "full": _as_bool(a.get("full", False), "full"),
    }


def _v_mesh_export(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "object_id": _as_name(a.get("object_id"), "object_id"),
        "path": _as_path(a.get("path"), "path"),
        "format": _as_choice(a.get("format"), "format", _EXPORT_FORMATS, "glb"),
    }


def _v_mesh_preview(a: Dict[str, Any]) -> Dict[str, Any]:
    angles = a.get("angles") or ["persp"]
    if not isinstance(angles, (list, tuple)):
        raise ProtocolError("angles: expected a list like [\"front\", \"persp\"]")
    if len(angles) > LIMITS["max_preview_angles"]:
        raise ProtocolError(f"angles: at most {int(LIMITS['max_preview_angles'])} per call")
    known = ("front", "back", "left", "right", "top", "bottom", "persp")
    return {
        "object_id": _as_name(a.get("object_id"), "object_id"),
        "path": _as_path(a.get("path"), "path"),
        "angles": [_as_choice(v, f"angles[{i}]", known) for i, v in enumerate(angles)],
        "size": _as_int(a.get("size", 512), "size", lo=64, hi=int(LIMITS["max_preview_px"])),
        "samples": _as_int(a.get("samples", 16), "samples", lo=1, hi=128),
    }


def _v_snapshot(a: Dict[str, Any]) -> Dict[str, Any]:
    return {"path": _as_path(a.get("path"), "path")}


def _as_path(value: Any, field: str) -> str:
    """A filesystem path supplied by the *caller*, never by the agent.

    The daemon fills these in from the view's asset dir; the host only checks
    that it is absolute and free of traversal, so a malformed call cannot write
    outside a directory the caller chose.
    """
    text = str(value or "").strip()
    if not text:
        raise ProtocolError(f"{field}: required")
    if not text.startswith("/"):
        raise ProtocolError(f"{field}: must be an absolute path")
    if ".." in text.split("/"):
        raise ProtocolError(f"{field}: must not contain '..'")
    if len(text) > 1024:
        raise ProtocolError(f"{field}: too long")
    return text


COMMANDS: Dict[str, Any] = {
    "ping": _v_ping,
    "state": _v_ping,
    "mesh_new": _v_mesh_new,
    "mesh_select": _v_mesh_select,
    "mesh_extrude": _v_mesh_extrude,
    "mesh_inset": _v_mesh_inset,
    "mesh_bevel": _v_mesh_bevel,
    "mesh_transform": _v_mesh_transform,
    "mesh_delete": _v_mesh_delete,
    "mesh_subdivide": _v_mesh_subdivide,
    "mesh_merge": _v_mesh_merge,
    "mesh_normals": _v_mesh_normals,
    "mesh_group": _v_mesh_group,
    "mesh_stats": _v_object_only,
    "mesh_validate": _v_mesh_validate,
    "mesh_export": _v_mesh_export,
    "mesh_preview": _v_mesh_preview,
    "object_delete": _v_object_only,
    "snapshot": _v_snapshot,
}

#: Commands that change geometry. These are the ones worth a revision, a
#: re-export and a cheap validation pass; the read-only rest are not.
MUTATING = frozenset({
    "mesh_new", "mesh_extrude", "mesh_inset", "mesh_bevel", "mesh_transform",
    "mesh_delete", "mesh_subdivide", "mesh_merge", "mesh_normals", "object_delete",
})


def validate(cmd: str, args: Any) -> Dict[str, Any]:
    """Validate one command's arguments, returning the cleaned dict.

    Raises :class:`ProtocolError` with a message meant for the agent's eyes.
    """
    name = str(cmd or "").strip()
    if name not in COMMANDS:
        raise ProtocolError(f"unknown command {name!r}; known commands: "
                            f"{', '.join(sorted(COMMANDS))}")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ProtocolError("args must be an object")
    return COMMANDS[name](args)


# ── framing ──────────────────────────────────────────────────────────────────

def encode(payload: Dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def decode(line: bytes) -> Dict[str, Any]:
    data = json.loads(line.decode("utf-8"))
    if not isinstance(data, dict):
        raise ProtocolError("each frame must be a JSON object")
    return data


__all__ = ["COMMANDS", "MUTATING", "LIMITS", "MAX_REPORTED_IDS", "PROTOCOL_VERSION",
           "ProtocolError", "validate", "validate_selection", "encode", "decode"]
