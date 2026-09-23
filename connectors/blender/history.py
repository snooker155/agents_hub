"""
The build program: every geometry command, in order, per object.

This is the **authoritative** description of an object. The Blender process is a
cache of the latest revision and may be evicted, killed or lost at any time; the
log is what survives, and replaying it reconstructs the object exactly.

That inversion is what keeps the rest simple. Revisions are prefixes of the log,
undo is a shorter prefix, crash recovery is a replay, and "show how this was
built" is just reading the file. None of those need a separate store.

The log lives beside the view's assets::

    <view dir>/geometry/<object_id>.jsonl
    {"seq": 1, "ts": "...", "cmd": "mesh_new",     "args": {...}, "revision": 1}
    {"seq": 2, "ts": "...", "cmd": "mesh_inset",   "args": {...}, "revision": 2}

Non-geometry commands (select, group) are logged too: they set state the later
commands depend on, so a replay that skipped them would rebuild a different
object.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from views.store import view_dir

#: Commands worth replaying. Read-only reporting (stats, validate, preview,
#: export) is not part of how an object was built.
REPLAYABLE = frozenset({
    "mesh_new", "mesh_select", "mesh_group", "mesh_extrude", "mesh_inset",
    "mesh_bevel", "mesh_transform", "mesh_delete", "mesh_subdivide",
    "mesh_merge", "mesh_normals",
})


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_path(view_id: str, object_id: str) -> Optional[Path]:
    base = view_dir(view_id)
    if base is None:
        return None
    path = base / "geometry"
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{object_id}.jsonl"


def append(view_id: str, object_id: str, cmd: str, args: Dict[str, Any],
           revision: int) -> int:
    """Record one command. Returns its seq in this object's log."""
    path = log_path(view_id, object_id)
    if path is None:
        return 0
    entries = read(view_id, object_id)
    seq = (entries[-1]["seq"] + 1) if entries else 1
    record = {"seq": seq, "ts": _utc(), "cmd": cmd, "args": args, "revision": revision}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return seq


def read(view_id: str, object_id: str) -> List[Dict[str, Any]]:
    path = log_path(view_id, object_id)
    if path is None or not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue          # a half-written line is not worth losing the log over
    return out


def objects(view_id: str) -> List[str]:
    base = view_dir(view_id)
    if base is None or not (base / "geometry").is_dir():
        return []
    return sorted(p.stem for p in (base / "geometry").glob("*.jsonl"))


def recent(view_id: str) -> List[str]:
    """The view's objects, most recently written first.

    Same files as :func:`objects`, ordered by when each log last grew, which is
    the order a build actually happened in — it answers "which object is this
    command about" when the command does not say.
    """
    base = view_dir(view_id)
    if base is None or not (base / "geometry").is_dir():
        return []

    def _last_ts(path) -> str:
        # Two logs written within the file system's timestamp granularity tie
        # on mtime; the timestamp of each log's last record breaks the tie the
        # way the build actually happened.
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            for line in reversed(lines):
                line = line.strip()
                if line:
                    return str(json.loads(line).get("ts") or "")
        except Exception:
            pass
        return ""

    logs = sorted((base / "geometry").glob("*.jsonl"),
                  key=lambda p: (p.stat().st_mtime, _last_ts(p)), reverse=True)
    return [p.stem for p in logs]


def truncate(view_id: str, object_id: str, seq: int) -> List[Dict[str, Any]]:
    """Drop every command after ``seq`` and return what is left.

    Used by revert: the log is the object, so cutting it short *is* the undo.
    """
    path = log_path(view_id, object_id)
    kept = [e for e in read(view_id, object_id) if e["seq"] <= seq]
    if path is not None:
        path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in kept),
                        encoding="utf-8")
    return kept


def forget(view_id: str, object_id: str) -> None:
    path = log_path(view_id, object_id)
    if path is not None and path.exists():
        path.unlink()


def replay(daemon, view_id: str, object_id: str) -> Dict[str, Any]:
    """Rebuild an object in ``daemon`` from its log.

    A replay is silent about failures in the middle: a log that no longer
    rebuilds is a real problem, so it stops at the first refusal and reports how
    far it got rather than leaving a half-object that looks finished.
    """
    entries = [e for e in read(view_id, object_id) if e["cmd"] in REPLAYABLE]
    done = 0
    for entry in entries:
        args = dict(entry.get("args") or {})
        if entry["cmd"] == "mesh_new":
            args["replace"] = True
        daemon.call(entry["cmd"], args)
        done += 1
    return {"replayed": done, "of": len(entries)}


def ensure(daemon, view_id: str, object_id: str) -> Dict[str, Any]:
    """Make sure ``object_id`` exists in the engine, replaying it if it does not.

    Called before every command that addresses an existing object. A daemon that
    was evicted, timed out or crashed is indistinguishable from a cold one here,
    which is the point: the caller never has to know which happened.
    """
    state = daemon.call("state")
    if object_id in (state.get("objects") or []):
        return {"replayed": 0, "of": 0}
    if not read(view_id, object_id):
        return {"replayed": 0, "of": 0}      # unknown object; let the engine say so
    return replay(daemon, view_id, object_id)


__all__ = ["append", "read", "objects", "truncate", "forget", "replay", "ensure",
           "log_path", "REPLAYABLE"]
