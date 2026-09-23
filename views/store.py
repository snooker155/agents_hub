"""
View persistence: a file dir per view + a lightweight DB index row.

Layout (workspace-scoped, so views travel with the workspace)::

    <workspace>/.views/<view_id>/
        view.json          # the full ViewEnvelope
        <assets...>        # optional data files, images, models, html

Views created without a workspace land in the global ``VIEWS_ROOT``. The
``views`` table (see ``common/db.py``) indexes them for the gallery/routes and
carries per-user ``state`` (control values / selection) so reopening restores
the view. The heavy spec stays in ``view.json``; run records reference a view
only by a lightweight ``view_ref`` (see :func:`view_ref`).
"""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import blobs, db
from common.paths import workspace_views_dir
from views.models import ViewEnvelope, normalize_envelope, base_spec_for
from views import ops as vops


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# Inline views (agent emits the whole spec in a <<<ui>>> block) are capped so a
# runaway spec can't bloat a run's SSE payload; larger views must use the
# create_view tool + asset files instead.
MAX_INLINE_SPEC_BYTES = 32 * 1024


def _new_view_id() -> str:
    return "vw_" + uuid.uuid4().hex[:16]


def _view_dir(workspace: Optional[str], view_id: str) -> Path:
    return workspace_views_dir(workspace) / view_id


def _json_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return 0


def _mirror_view_file(path: Path) -> None:
    """Mirror one view file to the blob store, best-effort (common/blobs.py)."""
    try:
        blobs.mirror(blobs.rel(path))
    except Exception:
        pass


def _mirror_view_dir(view_dir: Path) -> None:
    """Mirror every file currently in a view's dir, so a backend replica or
    worker on another host can serve it even though this host wrote it."""
    for f in view_dir.rglob("*"):
        if f.is_file():
            _mirror_view_file(f)


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def create_view(
    kind: str,
    title: str = "",
    spec: Optional[Dict[str, Any]] = None,
    *,
    workspace: Optional[str] = None,
    summary: str = "",
    data: Optional[Dict[str, Any]] = None,
    assets: Optional[List[str]] = None,
    controls: Optional[List[Dict[str, Any]]] = None,
    actions: Optional[List[Dict[str, Any]]] = None,
    complexity: str = "inline",
    fallback: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
    task_id: Optional[str] = None,
    asset_sources: Optional[Dict[str, str]] = None,
    validate: bool = True,
) -> ViewEnvelope:
    """Validate, persist and index a new view; return the stored envelope.

    ``asset_sources`` maps a destination filename (relative, inside the view
    dir) to an absolute source path already written in the workspace; each is
    copied into the view dir and its name recorded in ``assets``. ``validate``
    is False for live Studio views whose spec starts empty (built by ops).
    Raises :class:`views.models.ViewValidationError` on an invalid kind/spec.
    """
    env = normalize_envelope({
        "kind": kind,
        "title": title or "",
        "summary": summary or "",
        "spec": spec or {},
        "data": data,
        "assets": list(assets or []),
        "controls": list(controls or []),
        "actions": list(actions or []),
        "complexity": complexity or "inline",
        "fallback": fallback or {},
    }, validate_spec_body=validate)
    env.view_id = _new_view_id()

    view_dir = _view_dir(workspace, env.view_id)
    view_dir.mkdir(parents=True, exist_ok=True)
    # The immutable base document — fold(base, ops) reconstructs any later state,
    # which is what makes revert/undo and "how it was built" replay possible.
    (view_dir / "base.json").write_text(
        json.dumps(env.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Copy any referenced workspace files into the view dir (path-contained).
    for dest_name, src in (asset_sources or {}).items():
        dest = _contained(view_dir, dest_name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        rel = dest.relative_to(view_dir.resolve()).as_posix()
        if rel not in env.assets:
            env.assets.append(rel)

    (view_dir / "view.json").write_text(
        json.dumps(env.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    now = utc_iso()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO views (view_id, workspace, run_id, task_id, kind, title,
                                  summary, state, size_bytes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (env.view_id, workspace or None, run_id, task_id, env.kind, env.title,
             env.summary, None, _dir_size(view_dir), now, now),
        )
    _mirror_view_dir(view_dir)
    return env


def view_ref(env: ViewEnvelope) -> Dict[str, Any]:
    """The lightweight reference stored in a run's ``response.structured`` and
    streamed to the client — never the full spec, keeping run records slim."""
    return {
        "kind": "view_ref",
        "view_id": env.view_id,
        "view_kind": env.kind,
        "title": env.title,
        "summary": env.summary,
        "complexity": env.complexity,
    }


def _row(view_id: str) -> Optional[Dict[str, Any]]:
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM views WHERE view_id = ?", (view_id,)).fetchone()
    return dict(row) if row else None


def view_index_row(view_id: str) -> Optional[Dict[str, Any]]:
    """The index row for a view (kind, title, workspace, timestamps) without
    reading its envelope file — enough to label a link to it. ``None`` when the
    id is unknown (e.g. the view was deleted)."""
    return _row(view_id)


def get_view(view_id: str) -> Optional[Dict[str, Any]]:
    """Return the full view: the stored envelope merged with its saved ``state``.

    ``None`` when the view id is unknown or its ``view.json`` is missing.
    """
    row = _row(view_id)
    if not row:
        return None
    view_dir = _view_dir(row.get("workspace"), view_id)
    view_file = view_dir / "view.json"
    if not view_file.exists():
        # Not on this host: a backend replica or worker elsewhere may have
        # written it and mirrored it to the blob store (common/blobs.py).
        try:
            fetched = blobs.ensure_local(blobs.rel(view_file))
        except Exception:
            fetched = None
        if fetched is None:
            return None
        view_file = fetched
    try:
        env = json.loads(view_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    env["view_id"] = view_id
    env["state"] = db.loads(row.get("state"), {}) or {}
    env["workspace"] = row.get("workspace")
    env["run_id"] = row.get("run_id")
    env["task_id"] = row.get("task_id")
    env["size_bytes"] = row.get("size_bytes")
    env["created_at"] = row.get("created_at")
    env["updated_at"] = row.get("updated_at")
    return env


def list_views(
    workspace: Optional[str] = None,
    *,
    run_id: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """List view index rows (newest first), optionally filtered."""
    conn = db.get_conn()
    clauses: List[str] = []
    params: List[Any] = []
    if workspace is not None:
        clauses.append("workspace = ?")
        params.append(workspace)
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(run_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(int(limit))
    rows = conn.execute(
        f"""SELECT view_id, workspace, run_id, task_id, kind, title, summary,
                   size_bytes, created_at, updated_at
            FROM views{where} ORDER BY created_at DESC LIMIT ?""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def set_view_state(view_id: str, state: Dict[str, Any]) -> bool:
    """Persist per-user view state (control values, selection, camera pose).

    Returns False for an unknown view id.
    """
    row = _row(view_id)
    if not row:
        return False
    with db.transaction() as conn:
        conn.execute(
            "UPDATE views SET state = ?, updated_at = ? WHERE view_id = ?",
            (db.dumps(state or {}), utc_iso(), view_id),
        )
    return True


def delete_view(view_id: str) -> bool:
    """Remove a view's dir (local and mirrored) and index row.

    Returns False for an unknown id.
    """
    row = _row(view_id)
    if not row:
        return False
    try:  # a backend launched for this view (view_serve) dies with it
        from views.serve import stop_service
        stop_service(view_id)
    except Exception:
        pass
    view_dir = _view_dir(row.get("workspace"), view_id)
    try:
        mirrored_prefix = blobs.rel(view_dir) + "/"
    except Exception:
        mirrored_prefix = None
    if view_dir.exists():
        shutil.rmtree(view_dir, ignore_errors=True)
    if mirrored_prefix:
        try:
            for key in blobs.list(mirrored_prefix):
                blobs.delete(key)
        except Exception:
            pass
    with db.transaction() as conn:
        conn.execute("DELETE FROM views WHERE view_id = ?", (view_id,))
    return True


def view_dir(view_id: str) -> Optional[Path]:
    """The view's own directory, created if missing; None for an unknown view.

    The public form of :func:`_view_dir` for callers that write their own files
    into a view — the geometry engine's exported meshes, previews and command
    log all live beside the assets rather than in a store of their own.
    """
    row = _row(view_id)
    if not row:
        return None
    path = _view_dir(row.get("workspace"), view_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def add_asset(view_id: str, src_abs: str, dest_name: Optional[str] = None) -> str:
    """Copy a file into a view's asset dir; return its ``asset://<name>`` ref.

    Used to bring a texture or 3D model from the workspace into a live scene so
    it can be served (origin-isolated) through the asset route. Raises
    ``KeyError`` for an unknown view and ``ValueError`` on a bad path.
    """
    row = _row(view_id)
    if not row:
        raise KeyError(view_id)
    src = Path(src_abs)
    if not src.is_file():
        raise ValueError(f"asset source not found: {src_abs}")
    view_dir = _view_dir(row.get("workspace"), view_id)
    view_dir.mkdir(parents=True, exist_ok=True)
    dest = _contained(view_dir, dest_name or src.name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    rel = dest.relative_to(view_dir.resolve()).as_posix()
    with db.transaction() as conn:
        conn.execute("UPDATE views SET updated_at = ?, size_bytes = ? WHERE view_id = ?",
                     (utc_iso(), _dir_size(view_dir), view_id))
    _mirror_view_file(dest)
    return f"asset://{rel}"


def set_snapshot(view_id: str, png_bytes: bytes) -> Optional[str]:
    """Store a PNG snapshot for a view and set it as ``fallback.image``.

    Feeds non-visual surfaces (a Telegram photo, a gallery thumbnail) a static
    preview. Returns the ``asset://snapshot.png`` ref, or None for an unknown id.
    """
    row = _row(view_id)
    if not row:
        return None
    view_dir = _view_dir(row.get("workspace"), view_id)
    view_dir.mkdir(parents=True, exist_ok=True)
    (view_dir / "snapshot.png").write_bytes(png_bytes)
    view_file = view_dir / "view.json"
    try:
        doc = json.loads(view_file.read_text(encoding="utf-8"))
    except Exception:
        doc = {}
    doc.setdefault("fallback", {})
    doc["fallback"]["image"] = "snapshot.png"
    view_file.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    with db.transaction() as conn:
        conn.execute("UPDATE views SET updated_at = ?, size_bytes = ? WHERE view_id = ?",
                     (utc_iso(), _dir_size(view_dir), view_id))
    _mirror_view_file(view_dir / "snapshot.png")
    _mirror_view_file(view_file)
    return "asset://snapshot.png"


def save_clip(view_id: str, name: str, clip: Dict[str, Any]) -> Optional[str]:
    """Persist a recorded compute clip under ``.views/<id>/clips/<name>.json``.

    A clip (runtime + params + recorded frames) replays without recomputing —
    what ``timeline.mode == "recorded"`` plays. Returns ``clip://<name>`` or None.
    """
    row = _row(view_id)
    if not row:
        return None
    safe = "".join(c for c in str(name) if c.isalnum() or c in "-_") or "clip"
    clips_dir = _view_dir(row.get("workspace"), view_id) / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    clip_file = clips_dir / f"{safe}.json"
    clip_file.write_text(json.dumps(clip, ensure_ascii=False), encoding="utf-8")
    with db.transaction() as conn:
        conn.execute("UPDATE views SET updated_at = ?, size_bytes = ? WHERE view_id = ?",
                     (utc_iso(), _dir_size(_view_dir(row.get("workspace"), view_id)), view_id))
    _mirror_view_file(clip_file)
    return f"clip://{safe}"


def get_clip(view_id: str, name: str) -> Optional[Dict[str, Any]]:
    """Load a recorded clip by name (None if unknown / traversal)."""
    row = _row(view_id)
    if not row:
        return None
    try:
        target = _contained(_view_dir(row.get("workspace"), view_id) / "clips", f"{name}.json")
    except ValueError:
        return None
    if not target.is_file():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_clips(view_id: str) -> List[str]:
    """Names of recorded clips for a view."""
    row = _row(view_id)
    if not row:
        return []
    clips_dir = _view_dir(row.get("workspace"), view_id) / "clips"
    if not clips_dir.is_dir():
        return []
    return sorted(p.stem for p in clips_dir.glob("*.json"))


def view_asset_path(view_id: str, rel_path: str) -> Optional[Path]:
    """Resolve an asset path inside a view dir, guarding against traversal.

    Returns the absolute path if it exists and is contained within the view
    dir; ``None`` otherwise. ``view.json`` is not served as an asset. An asset
    not on this host is fetched from the blob store first (common/blobs.py):
    a view created on another worker or backend replica mirrors its assets
    there, and a different replica may be the one serving them.
    """
    row = _row(view_id)
    if not row:
        return None
    view_dir = _view_dir(row.get("workspace"), view_id)
    try:
        target = _contained(view_dir, rel_path)
    except ValueError:
        return None
    if target.name == "view.json":
        return None
    if target.is_file():
        return target
    try:
        return blobs.ensure_local(blobs.rel(target))
    except Exception:
        return None


def _contained(base: Path, rel_path: str) -> Path:
    """Resolve ``rel_path`` under ``base``, rejecting escapes (``..`` / absolute)."""
    candidate = (base / rel_path).resolve()
    base_resolved = base.resolve()
    if base_resolved != candidate and base_resolved not in candidate.parents:
        raise ValueError(f"path escapes view dir: {rel_path!r}")
    return candidate


# ── live views: op protocol (Studio) ─────────────────────────────────────────

# Per-view op budget: a runaway build can't wedge the browser or bloat the log.
MAX_OPS_PER_VIEW = 5000


def create_live_view(
    kind: str,
    title: str = "",
    *,
    workspace: Optional[str] = None,
    summary: str = "",
    run_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> ViewEnvelope:
    """Create an empty live view of ``kind``, seeded with its base spec, ready to
    be built up by ops in the Studio. Spec validation is deferred (the spec is
    empty at first), but a summary/title is still required."""
    return create_view(
        kind, title or f"New {kind}", base_spec_for(kind),
        workspace=workspace,
        summary=summary or f"A {kind} view",
        complexity="fullscreen",
        run_id=run_id,
        task_id=task_id,
        validate=False,
    )


def _base_doc(view_id: str, workspace: Optional[str]) -> Optional[Dict[str, Any]]:
    base_file = _view_dir(workspace, view_id) / "base.json"
    if base_file.exists():
        try:
            return json.loads(base_file.read_text(encoding="utf-8"))
        except Exception:
            return None
    # Legacy views created before base.json existed: fall back to the current doc.
    cur = _view_dir(workspace, view_id) / "view.json"
    try:
        return json.loads(cur.read_text(encoding="utf-8"))
    except Exception:
        return None


def _next_seq(conn, view_id: str) -> int:
    row = conn.execute("SELECT MAX(seq) AS m FROM view_ops WHERE view_id = ?", (view_id,)).fetchone()
    return int((row["m"] or 0)) + 1


def get_ops(view_id: str, after_seq: int = 0) -> List[Dict[str, Any]]:
    """Return this view's ops (seq > ``after_seq``) in order, each as a dict with
    its ``seq``/``ts``/``run_id`` merged in — the wire shape the client applies."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT seq, ts, run_id, op FROM view_ops WHERE view_id = ? AND seq > ? ORDER BY seq",
        (view_id, int(after_seq)),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        op = db.loads(r["op"], {}) or {}
        op["seq"] = r["seq"]
        op["ts"] = r["ts"]
        op["run_id"] = r["run_id"]
        out.append(op)
    return out


def append_ops(
    view_id: str,
    ops: Any,
    *,
    run_id: Optional[str] = None,
    source: str = "agent",
) -> List[Dict[str, Any]]:
    """Validate, persist and broadcast a batch of ops for a live view.

    Assigns each op a monotonic ``seq``, applies it to the materialized document
    (rewriting ``view.json``), records it in ``view_ops``, and publishes it on
    the ``view:<view_id>`` channel so the Studio renders the change immediately.
    Returns the stored ops (with seq/ts). Raises :class:`views.ops.OpError` on a
    malformed batch (returned to the agent to fix) and ``KeyError`` for an
    unknown view id.
    """
    clean = vops.normalize_ops(ops)
    row = _row(view_id)
    if not row:
        raise KeyError(view_id)
    workspace = row.get("workspace")
    view_dir = _view_dir(workspace, view_id)
    view_file = view_dir / "view.json"

    try:
        doc = json.loads(view_file.read_text(encoding="utf-8"))
    except Exception:
        doc = _base_doc(view_id, workspace) or {}

    now = utc_iso()
    stored: List[Dict[str, Any]] = []
    with db.transaction() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM view_ops WHERE view_id = ?", (view_id,)).fetchone()["c"]
        if count + len(clean) > MAX_OPS_PER_VIEW:
            raise vops.OpError(
                f"view op budget exceeded ({MAX_OPS_PER_VIEW}); group elements or start a new view")
        seq = _next_seq(conn, view_id)
        for op in clean:
            vops.apply_op(doc, op)
            record = {**op, "seq": seq, "ts": now, "source": source, "run_id": run_id}
            conn.execute(
                "INSERT INTO view_ops (view_id, seq, ts, run_id, op) VALUES (?, ?, ?, ?, ?)",
                (view_id, seq, now, run_id, db.dumps({**op, "source": source})),
            )
            stored.append(record)
            seq += 1
        conn.execute(
            "UPDATE views SET updated_at = ?, size_bytes = ? WHERE view_id = ?",
            (now, _dir_size(view_dir), view_id),
        )

    view_file.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    _broadcast(view_id, stored)
    return stored


# ── named checkpoints (view_snapshot) ─────────────────────────────────────────
# A checkpoint names an op seq so "revert to before the texture experiments"
# doesn't require remembering numbers. Stored as a small JSON map in the view
# dir (it belongs to the view's history, not to per-user state).

def _checkpoints_file(view_id: str, workspace: Optional[str]) -> Path:
    return _view_dir(workspace, view_id) / "checkpoints.json"


def list_checkpoints(view_id: str) -> Dict[str, int]:
    """Named checkpoints for a view: ``{name: seq}`` (empty for unknown views)."""
    row = _row(view_id)
    if not row:
        return {}
    f = _checkpoints_file(view_id, row.get("workspace"))
    if not f.is_file():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in data.items()}
    except Exception:
        return {}


def save_checkpoint(view_id: str, name: str) -> Optional[int]:
    """Record the view's current op seq under ``name``; return that seq.

    Re-using a name moves the checkpoint. Returns None for an unknown view.
    """
    row = _row(view_id)
    if not row:
        return None
    ops = get_ops(view_id)
    seq = ops[-1]["seq"] if ops else 0
    safe = "".join(c for c in str(name) if c.isalnum() or c in "-_ ").strip() or "checkpoint"
    cps = list_checkpoints(view_id)
    cps[safe] = seq
    f = _checkpoints_file(view_id, row.get("workspace"))
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(cps, ensure_ascii=False, indent=2), encoding="utf-8")
    _mirror_view_file(f)
    return seq


def checkpoint_seq(view_id: str, name: str) -> Optional[int]:
    """The seq a named checkpoint points at, or None."""
    return list_checkpoints(view_id).get(str(name).strip())


def _prune_checkpoints_after(view_id: str, workspace: Optional[str], seq: int) -> None:
    """Drop checkpoints that point past ``seq`` (their ops no longer exist)."""
    f = _checkpoints_file(view_id, workspace)
    if not f.is_file():
        return
    try:
        cps = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return
    kept = {k: v for k, v in cps.items() if int(v) <= int(seq)}
    if kept != cps:
        f.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")


def revert_to(view_id: str, seq: int) -> bool:
    """Undo: drop ops after ``seq`` and re-materialize the document from the base.

    ``seq=0`` reverts to the empty base view. Returns False for an unknown id.
    Broadcasts a ``view_reset`` event carrying the rebuilt document so watchers
    resynchronize without replaying the whole log.
    """
    row = _row(view_id)
    if not row:
        return False
    workspace = row.get("workspace")
    with db.transaction() as conn:
        conn.execute("DELETE FROM view_ops WHERE view_id = ? AND seq > ?", (view_id, int(seq)))
        conn.execute("UPDATE views SET updated_at = ? WHERE view_id = ?", (utc_iso(), view_id))
    base = _base_doc(view_id, workspace) or {}
    doc = vops.fold(base, get_ops(view_id))
    (_view_dir(workspace, view_id) / "view.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    _prune_checkpoints_after(view_id, workspace, seq)
    _broadcast_reset(view_id, doc, seq)
    return True


def _broadcast(view_id: str, ops: List[Dict[str, Any]]) -> None:
    """Publish applied ops on the view channel (best-effort; no-op without a loop)."""
    try:
        from common.session_broker import broker
        for op in ops:
            broker.publish_threadsafe(f"view:{view_id}", {"type": "view_op", "view_id": view_id, "op": op})
    except Exception:
        pass


def _broadcast_reset(view_id: str, doc: Dict[str, Any], seq: int) -> None:
    try:
        from common.session_broker import broker
        broker.publish_threadsafe(f"view:{view_id}", {"type": "view_reset", "view_id": view_id, "doc": doc, "seq": seq})
    except Exception:
        pass


# ── retention (maintenance job) ───────────────────────────────────────────────

# Above this many ops, fold all but the tail into a fresh base so the log stays
# bounded (the materialized view is unaffected; deep undo past the fold is lost).
OP_COMPACT_THRESHOLD = 1000
OP_COMPACT_KEEP_TAIL = 200


def compact_view_ops(view_id: str, keep_tail: int = OP_COMPACT_KEEP_TAIL) -> int:
    """Fold this view's older ops into its base, keeping only the tail. Returns
    the number of ops compacted (0 if under threshold or unknown view)."""
    row = _row(view_id)
    if not row:
        return 0
    ops = get_ops(view_id)
    if len(ops) <= OP_COMPACT_THRESHOLD:
        return 0
    cut = len(ops) - max(0, keep_tail)
    # Never fold past a named checkpoint — revert-to-checkpoint must keep working.
    cps = list_checkpoints(view_id)
    if cps:
        earliest = min(cps.values())
        cut = min(cut, sum(1 for o in ops if o["seq"] <= earliest))
    if cut <= 0:
        return 0
    workspace = row.get("workspace")
    base = _base_doc(view_id, workspace) or {}
    head = ops[:cut]
    new_base = vops.fold(base, head)
    (_view_dir(workspace, view_id) / "base.json").write_text(
        json.dumps(new_base, ensure_ascii=False, indent=2), encoding="utf-8")
    last_head_seq = head[-1]["seq"]
    with db.transaction() as conn:
        conn.execute("DELETE FROM view_ops WHERE view_id = ? AND seq <= ?", (view_id, last_head_seq))
    return len(head)


def prune_orphan_view_dirs() -> int:
    """Delete on-disk view dirs (``vw_*``) that have no ``views`` index row."""
    from common.paths import WORKSPACES_ROOT, VIEWS_ROOT
    conn = db.get_conn()
    known = {r["view_id"] for r in conn.execute("SELECT view_id FROM views").fetchall()}
    roots: List[Path] = [VIEWS_ROOT]
    if WORKSPACES_ROOT.is_dir():
        roots.extend(ws / ".views" for ws in WORKSPACES_ROOT.iterdir() if (ws / ".views").is_dir())
    removed = 0
    for root in roots:
        if not root.is_dir():
            continue
        for d in root.iterdir():
            if d.is_dir() and d.name.startswith("vw_") and d.name not in known:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
    return removed


def run_view_maintenance() -> Dict[str, int]:
    """Compact long op logs and drop orphan view dirs. Called by the daily job."""
    conn = db.get_conn()
    ids = [r["view_id"] for r in conn.execute("SELECT view_id FROM views").fetchall()]
    compacted = sum(1 for vid in ids if compact_view_ops(vid))
    return {"compacted_views": compacted, "pruned_view_dirs": prune_orphan_view_dirs()}


__all__ = [
    "create_view",
    "create_live_view",
    "get_view",
    "list_views",
    "set_view_state",
    "delete_view",
    "view_asset_path",
    "add_asset",
    "view_ref",
    "append_ops",
    "get_ops",
    "revert_to",
    "view_dir",
    "save_checkpoint",
    "list_checkpoints",
    "checkpoint_seq",
    "compact_view_ops",
    "prune_orphan_view_dirs",
    "run_view_maintenance",
    "MAX_INLINE_SPEC_BYTES",
    "MAX_OPS_PER_VIEW",
]
