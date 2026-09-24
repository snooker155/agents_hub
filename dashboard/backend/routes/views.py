"""
Views API — serve rendered views, their assets, and per-user view state.

- ``GET  /api/views``                list index rows (filter by workspace, run_id,
                                     or owner_kind/owner_id)
- ``GET  /api/views/{view_id}``      full envelope + saved state + owner
- ``PATCH /api/views/{view_id}/state`` persist control values / selection
- ``DELETE /api/views/{view_id}``    remove a view
- ``GET  /api/views/{view_id}/assets/{path}``  serve a contained view asset
- ``GET|POST|DELETE /api/views/{view_id}/chat`` the Studio build chat (Visualizer)
- ``GET  /api/views/{view_id}/code/versions``   a code view's recorded edit history
- ``POST /api/views/{view_id}/code/versions``   record a user edit; returns the view
- ``GET  /api/views/{view_id}/code/diff``       unified diff between two versions
- ``POST /api/views/{view_id}/code/run``        run the snippet in the run_code sandbox
- ``GET  /api/views/{view_id}/code/runs``       a code view's recorded run history
- ``POST /api/views/{view_id}/code/save``       write the snippet into a project

Asset serving is path-contained (guards ``..``/absolute like ``/file-raw``) and
sends a strict CSP so future ``html`` views run origin-isolated; for Phase 1
kinds (chart/table/diagram/markdown/image) the assets are just data files and
images.
"""
from __future__ import annotations

import mimetypes
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

import base64

from views.store import (
    get_view,
    list_views,
    delete_view,
    set_view_state,
    view_asset_path,
    get_ops,
    append_ops,
    revert_to,
    set_snapshot,
    get_clip,
    list_clips,
    save_checkpoint,
    list_checkpoints,
    checkpoint_seq,
    list_code_versions,
    add_code_version,
    list_code_runs,
    add_code_run,
    add_code_save,
)
from views.serve import is_allowed_upstream
from views.ops import OpError
from views.models import SUPPORTED_KINDS
from views.studio import create_studio_view, scene_context_note
from views.code import diff_versions as _diff_code_versions, runner_language, resolve_project_save_path
from tools import run_code as run_code_tool

from chat.entity_chat import EntityChatSpec
from chat.entity_chat_router import EntityChatRoute, build_entity_chat_router

router = APIRouter(prefix="/api/views", tags=["views"])

# Default-deny CSP for served assets: no network, scripts only from the asset
# origin. The sandboxed-iframe html kind relies on this. When the view has a
# configured service (view_serve), connect-src is opened to *exactly its own
# proxy prefix* — never the rest of the dashboard API (§16.8).
_ASSET_CSP = (
    "default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; font-src 'self' data:; connect-src {connect}"
)


def _owner_entity_id(owner_kind: Optional[str], owner_id: Optional[str]) -> Optional[str]:
    """The flow/team/scenario id an entity-kind owner's run belongs to.

    A view's owner is recorded as a *run* (``owner_id`` is a row in
    ``entity_runs``), but a frontend link wants the entity itself, e.g.
    /teams/``teamId``, not a team run id no route addresses. ``None`` for a
    ``run`` owner (nothing to resolve: the run id is already what
    /messages/{runId} wants) or when the owning run can't be found (e.g.
    since deleted).
    """
    if not owner_kind or not owner_id or owner_kind == "run":
        return None
    try:
        from common import entity_runs
        rec = entity_runs.get(owner_id)
    except Exception:
        return None
    entity_id = (rec or {}).get("entity_id")
    return str(entity_id) if entity_id else None


def _asset_csp(view_id: str, request: Request) -> str:
    """The CSP for this view's assets; connect-src stays 'none' unless the view
    serves a backend, in which case only its scoped proxy path is allowed."""
    connect = "'none'"
    view = get_view(view_id) or {}
    if ((view.get("serve") or {}).get("upstream")):
        base = f"{request.url.scheme}://{request.url.netloc}"
        connect = f"{base}/api/views/{view_id}/proxy/"
    return _ASSET_CSP.format(connect=connect)


class ViewStateUpdate(BaseModel):
    state: dict = {}


class StudioSessionCreate(BaseModel):
    kind: str
    title: str = ""
    workspace: Optional[str] = None


class RevertRequest(BaseModel):
    seq: int = -1
    checkpoint: str = ""


class CheckpointRequest(BaseModel):
    name: str


class ApplyOpsRequest(BaseModel):
    ops: list = []


class SnapshotRequest(BaseModel):
    data_url: str = ""   # "data:image/png;base64,...."


class CodeVersionCreate(BaseModel):
    body: str
    note: str = ""


class CodeRunRequest(BaseModel):
    body: Optional[str] = None
    mount_workspace: bool = False


class CodeSaveRequest(BaseModel):
    project_id: str
    path: str
    overwrite: bool = False


@router.get("")
async def get_views(workspace: Optional[str] = None, run_id: Optional[str] = None,
                    owner_kind: Optional[str] = None, owner_id: Optional[str] = None,
                    limit: int = 200):
    """List view index rows, newest first, optionally filtered.

    ``run_id`` stays the shorthand for "owned by that agent run" (an alias for
    ``owner_kind=run&owner_id=<run_id>``, see views.store.list_views);
    ``owner_kind``/``owner_id`` filter directly, e.g. every view a team run
    produced.
    """
    rows = list_views(workspace=workspace, run_id=run_id,
                      owner_kind=owner_kind, owner_id=owner_id, limit=limit)
    for row in rows:
        row["owner_entity_id"] = _owner_entity_id(row.get("owner_kind"), row.get("owner_id"))
    return {"views": rows}


@router.post("/studio")
async def create_studio_session(payload: StudioSessionCreate):
    """Create a new empty live view for a Studio session and return its envelope.

    The Studio then binds its chat to ``view_id`` and subscribes to ``view:<id>``.
    """
    if payload.kind not in SUPPORTED_KINDS:
        raise HTTPException(status_code=400, detail=f"Unsupported view kind '{payload.kind}'")
    return create_studio_view(payload.kind, payload.title, workspace=payload.workspace)


@router.get("/{view_id}")
async def get_one_view(view_id: str):
    """Return the full view envelope merged with its saved per-user state."""
    view = get_view(view_id)
    if view is None:
        raise HTTPException(status_code=404, detail="View not found")
    owner = view.get("owner")
    if owner:
        entity_id = _owner_entity_id(owner.get("kind"), owner.get("id"))
        if entity_id:
            view = {**view, "owner": {**owner, "entity_id": entity_id}}
    return view


@router.patch("/{view_id}/state")
async def patch_view_state(view_id: str, payload: ViewStateUpdate):
    """Persist per-user view state (control values, selection, camera pose)."""
    if not set_view_state(view_id, payload.state or {}):
        raise HTTPException(status_code=404, detail="View not found")
    return {"ok": True}


@router.post("/{view_id}/ops")
async def apply_user_ops(view_id: str, payload: ApplyOpsRequest):
    """Apply ops from the user (e.g. a control drag) to a live view.

    Recorded with ``source="user"`` and broadcast on the view channel, so the
    agent and the user co-edit one op history and the change is durable.
    """
    try:
        stored = append_ops(view_id, payload.ops, source="user")
    except KeyError:
        raise HTTPException(status_code=404, detail="View not found")
    except OpError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "applied": len(stored), "ops": stored}


@router.post("/{view_id}/snapshot")
async def save_snapshot(view_id: str, payload: SnapshotRequest):
    """Store a client-captured PNG snapshot as the view's fallback image.

    Feeds a static preview to non-visual surfaces (Telegram photo, gallery
    thumbnail). Body is a ``data:image/png;base64,…`` data URL.
    """
    raw = payload.data_url or ""
    if "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        png = base64.b64decode(raw, validate=False)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image data")
    if not png:
        raise HTTPException(status_code=400, detail="Empty image")
    ref = set_snapshot(view_id, png)
    if ref is None:
        raise HTTPException(status_code=404, detail="View not found")
    return {"ok": True, "asset": ref}


@router.get("/{view_id}/ops")
async def get_view_ops(view_id: str, after_seq: int = 0):
    """Return this view's ops after ``after_seq`` — the build log a late-joining
    Studio replays to catch up before subscribing to new ops live."""
    if get_view(view_id) is None:
        raise HTTPException(status_code=404, detail="View not found")
    return {"view_id": view_id, "ops": get_ops(view_id, after_seq=after_seq)}


@router.get("/{view_id}/clips")
async def get_view_clips(view_id: str):
    """List recorded compute clips for a view."""
    if get_view(view_id) is None:
        raise HTTPException(status_code=404, detail="View not found")
    return {"view_id": view_id, "clips": list_clips(view_id)}


@router.get("/{view_id}/clips/{name}")
async def get_view_clip(view_id: str, name: str):
    """Fetch a recorded compute clip (runtime + params + frames) to replay."""
    clip = get_clip(view_id, name)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    return clip


# ── view_serve scoped proxy (Phase 6) ────────────────────────────────────────
# Forward requests to a per-view localhost upstream a generated web service runs
# on, behind this scoped path. Origin-isolated by construction; the SSRF guard
# (localhost-only) is the security boundary — the backend must never forward to
# an arbitrary host.

@router.api_route("/{view_id}/proxy/{path:path}",
                  methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def view_proxy(view_id: str, path: str, request: Request):
    view = get_view(view_id)
    if view is None:
        raise HTTPException(status_code=404, detail="View not found")
    upstream = ((view.get("serve") or {}).get("upstream") or "").rstrip("/")
    if not upstream:
        raise HTTPException(status_code=404, detail="No service configured for this view (view_serve)")
    if not is_allowed_upstream(upstream):
        raise HTTPException(status_code=403, detail="Upstream not allowed")

    import httpx
    from fastapi.responses import Response
    target = f"{upstream}/{path}"
    body = await request.body()
    # only forward safe request headers; never pass the dashboard's auth/cookies
    fwd_headers = {k: v for k, v in request.headers.items()
                   if k.lower() in ("content-type", "accept")}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.request(request.method, target, params=dict(request.query_params),
                                        content=body, headers=fwd_headers)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Upstream error: {exc}")
    # strip hop-by-hop / framing headers that don't apply to the proxied response
    drop = {"content-encoding", "transfer-encoding", "connection", "content-length"}
    out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in drop}
    # The sandboxed html view has a null origin, so its fetches to this proxy are
    # cross-origin; grant CORS here — safe because the path is scoped to the
    # view's own service and no credentials are ever forwarded.
    out_headers["Access-Control-Allow-Origin"] = "*"
    return Response(content=resp.content, status_code=resp.status_code,
                    headers=out_headers, media_type=resp.headers.get("content-type"))


@router.post("/{view_id}/revert")
async def revert_view(view_id: str, payload: RevertRequest):
    """Undo a live view back to an op seq (0 = empty) or a named checkpoint."""
    seq = payload.seq
    if payload.checkpoint.strip():
        cp = checkpoint_seq(view_id, payload.checkpoint.strip())
        if cp is None:
            raise HTTPException(status_code=404, detail=f"No checkpoint named '{payload.checkpoint.strip()}'")
        seq = cp
    if seq < 0:
        raise HTTPException(status_code=400, detail="Pass a seq (0 = empty) or a checkpoint name")
    if not revert_to(view_id, seq):
        raise HTTPException(status_code=404, detail="View not found")
    return {"ok": True, "view_id": view_id, "reverted_to": seq}


@router.get("/{view_id}/checkpoints")
async def get_view_checkpoints(view_id: str):
    """Named checkpoints for a view: {name: seq}."""
    if get_view(view_id) is None:
        raise HTTPException(status_code=404, detail="View not found")
    return {"view_id": view_id, "checkpoints": list_checkpoints(view_id)}


@router.post("/{view_id}/checkpoints")
async def create_view_checkpoint(view_id: str, payload: CheckpointRequest):
    """Save a named checkpoint at the view's current op seq."""
    seq = save_checkpoint(view_id, payload.name)
    if seq is None:
        raise HTTPException(status_code=404, detail="View not found")
    return {"ok": True, "view_id": view_id, "name": payload.name, "seq": seq}


@router.delete("/{view_id}")
async def delete_one_view(view_id: str):
    """Delete a view (its dir and index row)."""
    if not delete_view(view_id):
        raise HTTPException(status_code=404, detail="View not found")
    return {"deleted": True, "view_id": view_id}


# ── code views: versions, diff, run, save (Chat code panel) ───────────────────
# The `code` view kind (views/models.py CodeSpec). These routes are user
# actions on the Studio/Chat panel, not agent tools, so they carry no approval
# gate — the same reasoning the rest of this file's routes already follow —
# but the run route honours the view's own workspace scoping, same as every
# other view route here.

def _require_code_view(view_id: str) -> dict:
    view = get_view(view_id)
    if view is None:
        raise HTTPException(status_code=404, detail="View not found")
    if view.get("kind") != "code":
        raise HTTPException(status_code=400, detail="Not a code view")
    return view


@router.get("/{view_id}/code/versions")
async def get_code_versions(view_id: str):
    """Every recorded version of a code view's body, oldest first."""
    _require_code_view(view_id)
    return {"versions": list_code_versions(view_id)}


@router.post("/{view_id}/code/versions")
async def create_code_version(view_id: str, payload: CodeVersionCreate):
    """Record a user edit as a new version; return the updated view envelope."""
    _require_code_view(view_id)
    body = payload.body or ""
    if not body.strip():
        raise HTTPException(status_code=400, detail="body must not be empty")
    updated = add_code_version(view_id, body, author="user", note=payload.note or "")
    if updated is None:
        raise HTTPException(status_code=404, detail="View not found")
    return updated


@router.get("/{view_id}/code/diff")
async def get_code_diff(view_id: str, a: int, b: int):
    """A unified diff between two recorded versions of a code view's body."""
    _require_code_view(view_id)
    try:
        diff = _diff_code_versions(view_id, a, b)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"diff": diff}


@router.post("/{view_id}/code/run")
async def run_code_view(view_id: str, payload: CodeRunRequest):
    """Run a code view's snippet in the same sandbox the run_code tool uses.

    Runs the view's current body, or ``payload.body`` when given (recorded as
    a new user version first, like any other edit). Refuses a non-runnable
    language with 400. Appends the result to the view's run history (last 10).
    """
    view = _require_code_view(view_id)
    spec = view.get("spec") or {}
    language = str(spec.get("language") or "")
    run_lang = runner_language(language)
    if run_lang is None:
        raise HTTPException(status_code=400, detail=(
            f"language {language!r} is not runnable here (only python, "
            "node/javascript and bash can be executed)"))

    body = spec.get("body", "")
    if payload.body is not None:
        body = payload.body
        if body != spec.get("body"):
            updated = add_code_version(view_id, body, author="user", note="edited before running")
            if updated is not None:
                spec = updated.get("spec") or spec

    workspace_path = None
    if payload.mount_workspace:
        ws_name = view.get("workspace")
        if not ws_name:
            raise HTTPException(status_code=400, detail="mount_workspace: this view has no workspace")
        from workspace import get_workspace_folder
        ws_dir = get_workspace_folder(ws_name)
        if ws_dir is None:
            raise HTTPException(status_code=400, detail="mount_workspace: workspace folder not found")
        workspace_path = str(ws_dir)

    result = run_code_tool.run_snippet(
        run_lang, body, mount_workspace=payload.mount_workspace, workspace=workspace_path,
    )
    add_code_run(view_id, {
        "version": int(spec.get("version") or 1),
        "exit_code": result.get("exit_code"),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "duration_ms": result.get("duration_ms", 0),
    })
    return {
        "ok": result.get("ok", False),
        "exit_code": result.get("exit_code"),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "duration_ms": result.get("duration_ms", 0),
        "language": result.get("language", run_lang),
        "sandbox": result.get("sandbox", "unavailable"),
        "error": result.get("error", ""),
    }


@router.get("/{view_id}/code/runs")
async def get_code_runs(view_id: str):
    """A code view's recorded run history (last 10), newest first.

    ``views.store.list_code_runs`` itself keeps insertion order (oldest first,
    the natural order to cap the last N in); this route reverses it for
    display, matching the code panel's expectation (see api/code.js).
    """
    _require_code_view(view_id)
    return {"runs": list(reversed(list_code_runs(view_id)))}


@router.post("/{view_id}/code/save")
async def save_code_view(view_id: str, payload: CodeSaveRequest):
    """Write a code view's body into a project's own folder.

    ``path`` must stay inside the project folder (no ``..``/absolute paths);
    refuses to overwrite an existing file unless ``overwrite`` is set.
    """
    view = _require_code_view(view_id)
    spec = view.get("spec") or {}
    try:
        target = resolve_project_save_path(payload.project_id, payload.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if target.exists() and not payload.overwrite:
        raise HTTPException(status_code=409, detail=(
            f"{payload.path} already exists in this project; pass overwrite to replace it"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(spec.get("body") or "", encoding="utf-8")
    add_code_save(view_id, {
        "project_id": payload.project_id, "path": payload.path,
        "version": int(spec.get("version") or 1),
    })
    return {"ok": True, "path": payload.path}


# ── the Studio build chat ─────────────────────────────────────────────────────
# The Visualizer, pinned to one view: the same entity-chat surface the loop,
# scenario and team builders use, so the conversation is stored server-side and
# the floating page-chat panel can host it (see components/pageChat).

VIEW_CHAT_KIND = "view"
VIEW_AGENT_ID = "visualizer"


def _view_chat_prompt(view_id: str, history: List[dict], user_message: str) -> str:
    """One turn's prompt: the live view, then the talk.

    The scene note is the same one the Studio-bound chat pipeline prepends, so
    the agent reads the view it edits in the shape it already knows.
    """
    from chat.entity_chat import transcript_block

    parts = [
        "You are building ONE interactive view in this platform's Visualization "
        "Studio. The user is looking at it: every change you make with your view "
        "tools streams to their canvas live.",
    ]
    note = scene_context_note(view_id)
    if note:
        parts += ["", note]
    parts += [
        "",
        "Rules for this conversation:",
        f"- Apply every change to view '{view_id}'. Never create a second view "
        "unless the user explicitly asks for one.",
        "- Build step by step, so the canvas fills as you work rather than in "
        "one lump at the end.",
        "- When the user only asks a question, answer it without changing the view.",
        "- Finish with one short line saying what the view now shows.",
    ]
    talk = transcript_block(history[:-1])
    if talk:
        parts += ["", "=== Conversation so far ===", talk]
    parts += ["", "=== The user's latest message ===", user_message]
    return "\n".join(parts)


def _workspace_path(workspace: Optional[str]) -> Optional[str]:
    """The folder the agent's file tools are rooted in, when it resolves."""
    if not workspace:
        return None
    try:
        from workspace import resolve_workspace_arg
        ws_path, _ = resolve_workspace_arg(workspace)
        return str(ws_path) if ws_path else None
    except Exception:
        return None


def _load_view_chat(request):
    from types import SimpleNamespace

    view_id = request.path_params["view_id"]
    doc = get_view(view_id)
    if not doc:
        raise HTTPException(status_code=404, detail="View not found")
    workspace = doc.get("workspace") or None
    return SimpleNamespace(entity_id=view_id, workspace=workspace, doc=doc)


def _load_view_stop(request):
    """Stopping never checks existence: a deleted view still has an id worth
    cancelling a run for, and there is nothing else this route needs."""
    from types import SimpleNamespace

    return SimpleNamespace(entity_id=request.path_params["view_id"])


def _view_context_setup(ctx):
    from common.agent_context import current_view_id
    from common.workspace_context import _workspace_ctx

    # The view tools take the view to edit from this ContextVar, so the agent
    # can "add a node" without being told which view every time; the workspace
    # one roots its file/data tools in the view's own workspace.
    current_view_id.set(ctx.entity_id)
    if ctx.workspace:
        _workspace_ctx.set(ctx.workspace)


router.include_router(build_entity_chat_router(EntityChatRoute(
    kind=VIEW_CHAT_KIND,
    path="/{view_id}/chat",
    load=_load_view_chat,
    load_for_stop=_load_view_stop,
    prompt=lambda ctx, history, msg: _view_chat_prompt(ctx.entity_id, history, msg),
    spec=lambda ctx: EntityChatSpec(
        kind=VIEW_CHAT_KIND, agent_id=VIEW_AGENT_ID,
        title=f"{ctx.doc.get('title') or ctx.entity_id} · view",
        workspace=ctx.workspace, workspace_path=_workspace_path(ctx.workspace),
    ),
    context_setup=_view_context_setup,
)))


@router.get("/{view_id}/assets/{asset_path:path}")
async def get_view_asset(view_id: str, asset_path: str, request: Request):
    """Serve a file from a view's asset dir, path-contained, with a strict CSP."""
    target = view_asset_path(view_id, asset_path)
    if target is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(
        str(target),
        media_type=media_type,
        headers={
            "Content-Security-Policy": _asset_csp(view_id, request),
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="{target.name}"',
        },
    )
