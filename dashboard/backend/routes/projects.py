"""
Projects API routes.

Projects are an organizational layer above tasks.
A project:
  - Lives inside a workspace
  - Can be backed by a git repo (GitHub, GitLab, local, etc.)
  - Can have a frontend (with live preview via iframe)
  - Can have a backend (with Swagger docs + request proxy)
"""
import ipaddress
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from fastapi.responses import StreamingResponse

# Ensure project root is on sys.path
_project_root = Path(__file__).resolve().parents[3]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import asyncio

from tasks import service as tasks_service
from workspace import get_workspace_folder, resolve_project_root, project_folder_name
from models import ProjectCreate, ProjectAttach, ProjectUpdate, ProjectApiRequest, ProjectImportFromRepo, ProjectConnectRepo, ProjectGraphSave, ProjectGraphChat, ProjectTasksChat
from projects.models import Project, RepoConfig
from projects.storage import ProjectStore
from common.paths import PROJECTS_FILE, PROJECT_ROOT, AGENTS_HUB_ROOT
from common.hostnet import host_service_url
from connectors.git import git_ops
from connectors.git.git_ops import GitOpsError
from connectors.git.providers import get_provider, GitProviderError
from connectors.git.issue_sync import sync_issues as _sync_project_issues
from chat.errors import error_event as _error_event

router = APIRouter(prefix="/api/projects", tags=["projects"])

_store = ProjectStore(path=PROJECTS_FILE)


def _project_to_dict(project: Project) -> dict:
    if hasattr(project, "model_dump"):
        return project.model_dump()
    return project.dict()


def _task_to_dict(task) -> dict:
    data = task.model_dump() if hasattr(task, "model_dump") else task.dict()
    data["id"] = str(data["id"])
    if data.get("parent_id"):
        data["parent_id"] = str(data["parent_id"])
    return data


# ─────────────────────────── Attach allowlist ────────────────────────────
#
# POST /attach symlinks an arbitrary directory into a workspace. Without a
# check, any directory the backend process can read (/etc, $HOME, ...) could
# be exposed this way, whether the caller is a dashboard user or an agent
# using the api tool.


def _attach_allowed_roots() -> list[Path]:
    """Directories a project may be attached from.

    ``AGENTS_HUB_ATTACH_ROOTS`` (os.pathsep separated absolute directories)
    replaces the default entirely when set. Unset, the default is the user's
    home directory plus this service's own repo and state roots — enough for
    the common "attach my project" case without allowing the whole filesystem.
    """
    raw = (os.environ.get("AGENTS_HUB_ATTACH_ROOTS") or "").strip()
    if raw:
        return [Path(p).expanduser().resolve() for p in raw.split(os.pathsep) if p.strip()]
    return [Path.home().resolve(), PROJECT_ROOT.resolve(), AGENTS_HUB_ROOT.resolve()]


def _check_attach_allowed(target: Path) -> None:
    """Reject an attach target outside the configured allowlist.

    Not a general sandbox: a permissively configured ``AGENTS_HUB_ATTACH_ROOTS``,
    or a root that itself holds sensitive data (e.g. the home directory), still
    lets that data be attached. It only closes the "any readable path" case.
    """
    target = target.resolve()
    for root in _attach_allowed_roots():
        if target == root or root in target.parents:
            return
    raise HTTPException(
        status_code=403,
        detail=(
            f"{target} is outside the allowed attach roots. Set the "
            "AGENTS_HUB_ATTACH_ROOTS environment variable to allow it."
        ),
    )


# ─────────────────────────── API-request guardrails ──────────────────────
#
# POST /{project_id}/api-request proxies an HTTP request to whatever base_url
# it is given. These checks are deliberately minimal, not full SSRF
# prevention (no protection against DNS rebinding, redirects to an internal
# host, IPv6 link-local forms, etc.) — only the scheme and the well-known
# cloud metadata address are rejected.

_BLOCKED_API_HOSTNAMES = {"metadata.google.internal"}
_METADATA_NETWORK = ipaddress.ip_network("169.254.0.0/16")


def _validated_api_base_url(base: str) -> str:
    """Validate a proxied backend base URL and rewrite its host for containers.

    Rejects non-http(s) schemes and the cloud metadata address/hostname, then
    runs the result through host_service_url() so a backend running inside a
    container reaches the Docker host's localhost the same way every other
    outbound call in this codebase does.
    """
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="base_url must use the http or https scheme")
    host = (parsed.hostname or "").lower()
    if host in _BLOCKED_API_HOSTNAMES:
        raise HTTPException(status_code=400, detail="Requests to cloud metadata addresses are not allowed")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addr = None
    if addr is not None and addr in _METADATA_NETWORK:
        raise HTTPException(status_code=400, detail="Requests to cloud metadata addresses are not allowed")
    return host_service_url(base)


# ─────────────────────────── CRUD ────────────────────────────

@router.get("")
async def list_projects(workspace: Optional[str] = Query(None)):
    projects = _store.list()
    if workspace:
        projects = [p for p in projects if p.workspace == workspace]
    result = []
    all_tasks = tasks_service.list_tasks()
    for p in projects:
        d = _project_to_dict(p)
        d["tasks_count"] = sum(1 for t in all_tasks if t.project_id == p.id)
        d["folder"] = project_folder_name(p.name)
        result.append(d)
    return result


@router.post("")
async def create_project(payload: ProjectCreate):
    # Validate workspace exists
    ws_folder = get_workspace_folder(payload.workspace)
    if ws_folder is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{payload.workspace}' not found")

    project = Project(
        name=payload.name,
        description=payload.description,
        type=payload.type or "general",
        workspace=payload.workspace,
        tags=payload.tags or [],
    )
    if payload.repo:
        from projects.models import RepoConfig
        project.repo = RepoConfig(**payload.repo)
    if payload.frontend:
        from projects.models import FrontendConfig
        project.frontend = FrontendConfig(**payload.frontend)
    if payload.backend:
        from projects.models import BackendConfig
        project.backend = BackendConfig(**payload.backend)

    _store.add(project)

    # Create the project subfolder inside the workspace so agents have a place to work
    try:
        folder = project_folder_name(project.name)
        resolve_project_root(project.workspace, folder)
    except Exception:
        pass

    d = _project_to_dict(project)
    d["folder"] = project_folder_name(project.name)
    return d


@router.get("/{project_id}")
async def get_project(project_id: str):
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    d = _project_to_dict(project)
    all_tasks = tasks_service.list_tasks()
    d["tasks_count"] = sum(1 for t in all_tasks if t.project_id == project_id)
    d["folder"] = project_folder_name(project.name)
    return d


@router.put("/{project_id}")
async def update_project(project_id: str, payload: ProjectUpdate):
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    fields = {}
    if payload.name is not None:
        fields["name"] = payload.name
    if payload.description is not None:
        fields["description"] = payload.description
    if payload.status is not None:
        fields["status"] = payload.status
    if payload.type is not None:
        fields["type"] = payload.type
    if payload.tags is not None:
        fields["tags"] = payload.tags
    if payload.repo is not None:
        fields["repo"] = payload.repo
    if payload.frontend is not None:
        fields["frontend"] = payload.frontend
    if payload.backend is not None:
        fields["backend"] = payload.backend

    updated = _store.update(project_id, **fields)
    if not updated:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_to_dict(updated)


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    deleted = _store.delete(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
    _graph_store.delete_project(project_id)
    return {"deleted": True}


# ─────────────────────────── FILES ────────────────────────────

def _project_root_path(project) -> Optional[Path]:
    """Return the resolved project subfolder path, or None if the workspace doesn't exist."""
    ws = get_workspace_folder(project.workspace)
    if ws is None:
        return None
    folder = project_folder_name(project.name)
    root = (ws / folder).resolve()
    return root if root.exists() else None


@router.get("/{project_id}/files")
async def list_project_files(project_id: str):
    """List all files inside the project's subfolder (hides dot-files/folders)."""
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    root = _project_root_path(project)
    if root is None:
        return {"files": []}
    files = []
    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if any(seg.startswith(".") for seg in rel.split("/")):
                continue
            files.append(rel)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"files": sorted(files)}


@router.get("/{project_id}/file-content")
async def get_project_file_content(project_id: str, path: str):
    """Return the content of a single file inside the project folder."""
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    root = _project_root_path(project)
    if root is None:
        raise HTTPException(status_code=404, detail="Project folder not found")
    target = (root / path).resolve()
    if not str(target).startswith(str(root) + "/") and str(target) != str(root):
        raise HTTPException(status_code=403, detail="Path outside project folder")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"path": path, "content": content, "size": target.stat().st_size}


# ─────────────────────────── SPEC FROM CODE ────────────────────────────

_EXTRACT_SCRIPT = """
import sys, json, os, importlib
sys.path.insert(0, os.getcwd())

spec = None
for mod_name in ["main", "app", "api", "server", "application", "backend"]:
    try:
        mod = importlib.import_module(mod_name)
    except Exception:
        continue
    for attr in ["app", "application", "api"]:
        obj = getattr(mod, attr, None)
        if obj is None:
            continue
        if hasattr(obj, "openapi"):
            try:
                spec = obj.openapi()
                break
            except Exception:
                pass
    if spec:
        break
    # factory pattern
    factory = getattr(mod, "create_app", None)
    if callable(factory):
        try:
            obj = factory()
            if hasattr(obj, "openapi"):
                spec = obj.openapi()
                break
        except Exception:
            pass

print(json.dumps(spec) if spec else "null")
"""


def _detect_port_from_source(root: Path) -> Optional[int]:
    """Scan source files for uvicorn/gunicorn port hints."""
    patterns = [
        re.compile(r'uvicorn\.run\([^)]*port\s*=\s*(\d+)'),
        re.compile(r'port\s*=\s*int\s*\(\s*os\.(?:getenv|environ\.get)\s*\([^)]+\)\s*\w*\s*(\d{4,5})'),
        re.compile(r'PORT\s*=\s*(\d{4,5})'),
        re.compile(r'--port[=\s]+(\d{4,5})'),
    ]
    for py_file in list(root.rglob("*.py"))[:40]:
        try:
            text = py_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for pat in patterns:
            m = pat.search(text)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    pass
    return None


@router.get("/{project_id}/spec-from-code")
async def get_spec_from_code(project_id: str):
    """Extract OpenAPI spec from project source code (no running server needed)."""
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    root = _project_root_path(project)
    if root is None:
        raise HTTPException(status_code=404, detail="Project folder not found")

    # Strategy 1: look for exported spec files
    for spec_file in ["openapi.json", "swagger.json", "api-docs.json", "api-spec.json"]:
        candidate = root / spec_file
        if candidate.is_file():
            try:
                spec = json.loads(candidate.read_text(encoding="utf-8"))
                detected_port = _detect_port_from_source(root)
                return {"spec": spec, "source": spec_file, "detected_port": detected_port}
            except Exception:
                pass

    # Strategy 2: dynamic import via subprocess
    python_exec = sys.executable
    for venv_dir in ["venv", ".venv", "env"]:
        venv_python = root / venv_dir / "bin" / "python"
        if venv_python.exists():
            python_exec = str(venv_python)
            break

    result = None
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [python_exec, "-c", _EXTRACT_SCRIPT],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            raw = result.stdout.strip()
            if raw and raw != "null":
                spec = json.loads(raw)
                detected_port = _detect_port_from_source(root)
                return {"spec": spec, "source": "dynamic_import", "detected_port": detected_port}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Timed out trying to import app")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    stderr = result.stderr.strip() if result else ""
    if stderr:
        raise HTTPException(
            status_code=422,
            detail=f"Could not import app: {stderr[:400]}",
        )
    raise HTTPException(status_code=422, detail="No OpenAPI spec found in project source code")


# ─────────────────────────── TASKS ────────────────────────────

@router.get("/{project_id}/tasks")
async def get_project_tasks(project_id: str):
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    all_tasks = tasks_service.list_tasks()
    project_tasks = [t for t in all_tasks if t.project_id == project_id]
    return [_task_to_dict(t) for t in project_tasks]


# ─────────────────────────── STRUCTURE GRAPH ────────────────────────────

from projects.graph_store import ProjectGraphStore

_graph_store = ProjectGraphStore()


def _validate_view(view: str) -> None:
    if view not in ("architecture", "process"):
        raise HTTPException(status_code=400, detail="view must be 'architecture' or 'process'")


def _project_tasks_for(project_id: str):
    all_tasks = tasks_service.list_tasks()
    return [t for t in all_tasks if t.project_id == project_id]


@router.get("/{project_id}/graph")
async def get_project_graph(project_id: str, view: str = Query("architecture")):
    """Return the project's structure graph.

    Serves a saved (hand-edited or AI-generated) graph when one exists;
    otherwise builds it deterministically. ``view=architecture`` is the technical
    structure (client → service → data/dependencies) inferred from config + a
    shallow source scan; ``view=process`` is the business/process flow derived
    from the project's task hierarchy and ordering. The response carries a
    ``source`` of ``auto`` | ``manual`` | ``generated``.
    """
    _validate_view(view)
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    saved = _graph_store.get(project_id, view)
    if saved:
        from projects.graph import strip_placeholder_nodes
        saved["nodes"], saved["edges"] = strip_placeholder_nodes(
            saved.get("nodes") or [], saved.get("edges") or [])
        return saved

    from projects.graph import build_project_graph

    root = _project_root_path(project)
    tasks = _project_tasks_for(project_id) if view == "process" else None
    try:
        graph = await asyncio.to_thread(build_project_graph, project, view, root, tasks)
        graph["source"] = "auto"
        return graph
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{project_id}/graph")
async def save_project_graph(project_id: str, payload: ProjectGraphSave,
                            view: str = Query("architecture")):
    """Persist hand-edited nodes/edges so they survive a rebuild."""
    _validate_view(view)
    if not _store.get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return _graph_store.save(project_id, view, payload.nodes, payload.edges, source="manual")


@router.post("/{project_id}/graph/relayout")
async def relayout_project_graph(project_id: str, payload: ProjectGraphSave,
                                 view: str = Query("architecture")):
    """Re-arrange the given nodes/edges into horizontal bands by kind (same kind
    side by side, different kinds stacked) and return them (positions only change).
    Not persisted — the caller saves."""
    _validate_view(view)
    if not _store.get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    from projects.graph import kind_grouped_layout
    nodes = list(payload.nodes or [])
    edges = list(payload.edges or [])
    kind_grouped_layout(nodes, edges)
    return {"nodes": nodes, "edges": edges}


@router.delete("/{project_id}/graph")
async def reset_project_graph(project_id: str, view: str = Query("architecture")):
    """Discard the saved graph so the view reverts to the deterministic build."""
    _validate_view(view)
    if not _store.get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    deleted = _graph_store.delete(project_id, view)
    return {"reset": deleted}


@router.post("/{project_id}/graph/generate")
async def generate_project_graph(project_id: str, view: str = Query("architecture")):
    """Generate the graph with an LLM (project context + detected structure),
    persist it as ``generated``, and return it."""
    _validate_view(view)
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    from projects.graph import generate_graph_via_llm

    root = _project_root_path(project)
    tasks = _project_tasks_for(project_id) if view == "process" else None
    try:
        graph = await asyncio.to_thread(generate_graph_via_llm, project, view, root, tasks)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")

    saved = _graph_store.save(project_id, view, graph["nodes"], graph["edges"],
                              source=graph.get("source", "generated"))
    return saved


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


# Tool-call / reasoning channel artifacts some local models (gpt-oss "harmony"
# format) leak into their final text. When present the reply is unusable, so we
# fall back to a summary of what the agent actually changed.
_REPLY_GARBAGE_MARKERS = (
    "to=functions.", "<|constrain|>", "<|channel|>", "<|call|>",
    "<|start|>", "<|end|>", "commentary<|", "assistantfinal",
)


def _clean_agent_reply(text: str) -> str:
    t = (text or "").strip()
    if not t or any(m in t for m in _REPLY_GARBAGE_MARKERS):
        return ""
    return t


# ─────────────────── Disconnect-proof SSE agent runs ───────────────────
# When the user leaves the page (or just switches tabs) mid-run, the browser
# aborts the fetch, which cancels the StreamingResponse generator. We must NOT
# let that abort the agent or skip the run's finalization (persisting the
# assistant reply, closing the run record). So the agent + all persistence run
# in a *detached* task that pushes events into a queue; the SSE generator only
# relays them. If the client disconnects, the relay stops but the worker keeps
# going to completion — the result is there when the user returns (the page
# reloads the graph + chat history on mount).
_STREAM_DONE = object()
_BG_RUNS: set = set()

# Active agent tasks per project, so a "Stop" request can cancel an in-flight
# architect-chat / planner run. Keyed by f"{project_id}:{slot}" (slot = the view
# for chat, or "__plan__" for task generation). Detached runs survive client
# disconnects, so cancelling the underlying agent task is the only way to stop one.
_ACTIVE_GRAPH_RUNS: dict = {}


def _register_run(project_id: str, slot: str, task: asyncio.Task) -> None:
    key = f"{project_id}:{slot}"
    _ACTIVE_GRAPH_RUNS[key] = task
    task.add_done_callback(
        lambda _t, k=key: (_ACTIVE_GRAPH_RUNS.pop(k, None) if _ACTIVE_GRAPH_RUNS.get(k) is _t else None))


def _cancel_runs(project_id: str) -> int:
    """Cancel every in-flight run for a project. Returns how many were cancelled."""
    cancelled = 0
    for key, task in list(_ACTIVE_GRAPH_RUNS.items()):
        if key.startswith(f"{project_id}:") and task and not task.done():
            task.cancel()
            cancelled += 1
    return cancelled


def _spawn_detached(coro) -> asyncio.Task:
    """Run *coro* as a task that survives client disconnects (strong ref held)."""
    task = asyncio.create_task(coro)
    _BG_RUNS.add(task)
    task.add_done_callback(_BG_RUNS.discard)
    return task


class _RecordingQueue(asyncio.Queue):
    """An asyncio.Queue that also keeps every item put into it.

    Lets the detached worker persist the full event trace after the run — even
    if the client disconnected mid-run and the relay stopped reading.
    ``_put`` is the single choke point for both ``put`` and ``put_nowait``.
    """

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.recorded: list = []

    def _put(self, item):
        super()._put(item)
        try:
            self.recorded.append(item)
        except Exception:
            pass


def _trace_item_for(ev: dict):
    """Map one streamed SSE event to a chat feed item (mirrors the UI), or None.

    Kept in sync with the onEvent switch in ProjectGraph.jsx so a reloaded
    session renders identically to the live run.
    """
    t = ev.get("type")
    if t == "think":
        c = ev.get("content")
        return {"k": "thinking", "text": c} if c else None
    if t in ("thinking", "native_reasoning"):
        txt = ev.get("message") or ev.get("content") or ""
        return {"k": "thinking", "text": txt} if txt and not txt.startswith("[") else None
    if t == "tool_start":
        tool = str(ev.get("tool") or "")
        if tool.startswith("add_graph") or tool.startswith("delete_graph") or tool == "clear_graph":
            return None  # graph tools surface as their own node/edge events
        return {"k": "tool", "tool": tool, "status": "running"}
    if t == "graph_node":
        node = ev.get("node") or {}
        return {"k": "node", "label": (node.get("data") or {}).get("label") or node.get("id")}
    if t == "graph_edge":
        e = ev.get("edge") or {}
        return {"k": "node", "label": "", "edge": f"{e.get('source')} → {e.get('target')}"}
    if t == "graph_node_delete":
        return {"k": "tool", "tool": f"removed {ev.get('id')}", "status": "done"}
    if t == "graph_edge_delete":
        return {"k": "tool", "tool": f"removed edge {ev.get('id')}", "status": "done"}
    if t == "graph_clear":
        return {"k": "tool", "tool": "cleared the graph", "status": "done"}
    if t == "stopped":
        return {"k": "tool", "tool": "stopped by you", "status": "done"}
    if t == "message":
        c = ev.get("content")
        return {"k": "assistant", "text": c} if c else None
    if t == "error":
        return {"k": "error", "text": ev.get("error") or "error"}
    return None


async def _relay_queue(queue: "asyncio.Queue"):
    """Yield SSE frames from *queue* until the ``_STREAM_DONE`` sentinel.

    Tolerates client disconnect: if the consuming generator is cancelled we stop
    relaying but leave the producing worker running (it is a detached task).
    """
    try:
        while True:
            item = await queue.get()
            if item is _STREAM_DONE:
                break
            yield _sse(item)
    except (asyncio.CancelledError, GeneratorExit):
        # Client went away — let the detached worker finish on its own.
        return


@router.get("/{project_id}/graph/generate/stream")
async def generate_project_graph_stream(project_id: str, view: str = Query("architecture")):
    """Stream the Architect Agent generating the graph (SSE).

    Forwards the agent's live execution events — ``tool_start`` / ``tool_end`` /
    ``thinking`` / ``token`` / ``error`` — then emits the final ``graph`` (also
    persisted as ``generated``) and a ``done`` marker. Falls back to the
    deterministic build if the agent is unavailable or returns no graph.
    """
    _validate_view(view)
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    root = _project_root_path(project)
    tasks = _project_tasks_for(project_id) if view == "process" else None

    async def event_stream():
        import tempfile
        from pathlib import Path as _Path
        from projects.graph import (
            build_generation_prompt, agent_workspace_path,
            finalize_generated_output, _ensure_architect_agent, _ARCHITECT_AGENT_ID,
        )

        yield _sse({"type": "meta", "view": view})

        prompt = build_generation_prompt(project, view, root, tasks)
        text = None

        if _ensure_architect_agent():
            try:
                from agents.agent_factory import create_agent
                from agents.callbacks import ChatStreamCallback

                loop = asyncio.get_running_loop()
                queue: asyncio.Queue = asyncio.Queue()
                log_file = _Path(tempfile.gettempdir()) / f"arch_gen_{project_id}_{view}.log"
                callback = ChatStreamCallback(loop, queue, [], log_file)

                # No model override — create_agent resolves the provider/model
                # through its canonical chain (workspace model_override /
                # settings → global .env), matching the user's selection.
                # No repetition ceiling (0 = UNLIMITED_TOOL_REPEATS): reading
                # file after file is normal analysis, not a loop (the guard
                # counts by tool name).
                agent = await asyncio.to_thread(
                    create_agent, _ARCHITECT_AGENT_ID,
                    workspace=agent_workspace_path(project, root), streaming=True,
                    max_tool_repeats=0,
                )
                callback.bind_model(agent.provider or "", agent.model or "")
                yield _sse({"type": "agent", "agent_id": _ARCHITECT_AGENT_ID,
                            "provider": agent.provider or "", "model": agent.model or ""})

                task = asyncio.create_task(agent.arun(prompt, callbacks=[callback]))

                # Forward callback events as they arrive; drain anything left
                # once the run finishes.
                while not task.done():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=0.2)
                        yield _sse(event)
                    except asyncio.TimeoutError:
                        continue
                while not queue.empty():
                    yield _sse(queue.get_nowait())

                res = await task
                if getattr(res, "ok", False):
                    text = str(res.agent_output)
                else:
                    yield _sse(_error_event(
                        "agent", getattr(res, "error", None) or "agent returned no output"))
            except Exception as e:  # noqa: BLE001
                yield _sse(_error_event("agent", str(e)))
        else:
            yield _sse(_error_event("registry", "architect agent unavailable"))

        graph = await asyncio.to_thread(finalize_generated_output, text, project, view, root, tasks)
        saved = _graph_store.save(project_id, view, graph["nodes"], graph["edges"],
                                  source=graph.get("source", "generated"))
        yield _sse({"type": "graph", **saved})
        yield _sse({"type": "done", "source": saved["source"]})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/{project_id}/graph/messages")
async def get_graph_messages(project_id: str, view: str = Query("architecture")):
    """Return the interactive build chat transcript for a view.

    ``messages`` is the plain user/assistant transcript; ``trace`` is the rich
    feed (thinking + tool/graph steps interleaved) the UI replays on reload so
    the full session — not just the bare conversation — is restored.
    """
    _validate_view(view)
    if not _store.get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "messages": _graph_store.get_messages(project_id, view),
        "trace": _graph_store.get_trace(project_id, view),
    }


@router.delete("/{project_id}/graph/messages")
async def clear_graph_messages(project_id: str, view: str = Query("architecture")):
    """Clear the build chat transcript and start a fresh chat session.

    Wipes the conversation and advances the session epoch so the next turn opens
    a new backend session (a clean run-thread in Messages). The graph itself is
    left untouched.
    """
    _validate_view(view)
    if not _store.get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    epoch = _graph_store.clear_messages(project_id, view, new_session=True)
    return {"cleared": True, "session_epoch": epoch}


@router.post("/{project_id}/graph/chat")
async def chat_project_graph(project_id: str, payload: ProjectGraphChat,
                             view: str = Query("architecture")):
    """Interactive graph build (SSE).

    Runs the Architect Agent on the user's message with the graph-builder tools
    bound to a live sink: each ``add_graph_node`` / ``add_graph_edge`` /
    ``clear_graph`` is persisted and streamed as a ``graph_node`` / ``graph_edge``
    / ``graph_clear`` event, so the canvas updates step by step. Also forwards
    the agent's ``tool_*`` / ``thinking`` / ``token`` events and a final
    ``message`` (assistant reply) + ``done``.
    """
    _validate_view(view)
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    user_message = (payload.message or "").strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="Empty message")

    root = _project_root_path(project)
    tasks = _project_tasks_for(project_id) if view == "process" else None

    async def run_turn(queue: asyncio.Queue):
        from projects.graph import (
            build_chat_turn_prompt, agent_workspace_path,
            _ensure_architect_agent, _ARCHITECT_AGENT_ID,
        )
        from projects.graph_chat import GraphStreamSink
        from common import graph_sink
        from managers.run_manager import (
            open_run as _open_run, update_run as _update_run,
            new_unique_run_id as _new_run_id, run_log_path as _run_log_path,
        )
        from datetime import datetime, timezone

        def _iso():
            return datetime.now(timezone.utc).isoformat()

        async def emit(payload):
            await queue.put(payload)

        # Record the user turn, then build the prompt from the live graph + history.
        _graph_store.append_message(project_id, view, "user", user_message)
        history = _graph_store.get_messages(project_id, view)
        current = _graph_store.get(project_id, view) or {"nodes": [], "edges": []}
        prompt = build_chat_turn_prompt(project, view, root, tasks, current, history, user_message)

        # Open a run record so the turn appears in Messages, grouped per
        # project+view+session-epoch. The epoch lets "Clear chat" start a fresh
        # session (epoch 0 keeps the legacy un-suffixed id for continuity).
        run_id = _new_run_id()
        log_file = _run_log_path(run_id)
        epoch = _graph_store.get_session_epoch(project_id, view)
        conv_id = f"projgraph:{project_id}:{view}" + (f":{epoch}" if epoch else "")
        session_id = None
        try:
            from common.session_service import get_or_create_chat_session
            session_id = get_or_create_chat_session(
                conversation_id=conv_id,
                title=f"{project.name} · {view} graph" + (f" #{epoch + 1}" if epoch else ""),
                workspace=project.workspace,
                agent_id=_ARCHITECT_AGENT_ID,
            )
        except Exception:
            session_id = None
        _open_run(run_id, _ARCHITECT_AGENT_ID, task_id=conv_id, session_id=session_id,
                  session_type="chat", message_origin="architect-chat", channel="chat",
                  workspace=project.workspace, title=user_message[:60], log_file=str(log_file))

        # Write the same chat-log scaffold every chat run uses (=== Message block,
        # user message, response section). The insights endpoint parses chat runs
        # from this log via _extract_chat_message_runs, which keys on these
        # headers — without the scaffold the run shows no process trace. The
        # ChatStreamCallback appends its markers onto the shared log_lines list
        # and rewrites the file, so the scaffold is preserved (mirrors
        # chat.runs.create_chat_run + chat.pipelines.run_chat_pipeline).
        import time as _time
        from agents.callbacks import write_log as _write_log, append_log as _append_log

        msg_id = run_id[:8]
        started = _iso()
        log_lines = [
            f"=== Chat message  run_id={run_id} ===",
            f"Started   : {started}",
            f"Agent     : {_ARCHITECT_AGENT_ID}",
            f"Workspace : {project.workspace or '—'}",
            f"Conv ID   : {conv_id}",
            f"Session ID: {session_id or '—'}",
            f"Title     : {user_message[:60]}",
            "",
            f"=== Message at {started} id={msg_id} ===",
            "--- User message ---",
            user_message,
            "",
            "--- Agent response (stream) ---",
        ]
        _write_log(log_file, log_lines)
        message_started = _time.perf_counter()

        reply, status, err = "", "completed", None
        sink = None
        callback = None
        provider = model = ""

        if _ensure_architect_agent():
            try:
                from agents.agent_factory import create_agent
                from agents.callbacks import ChatStreamCallback

                loop = asyncio.get_running_loop()
                callback = ChatStreamCallback(loop, queue, log_lines, log_file, session_id=session_id)

                # Building a large graph can mean dozens of node/edge tool calls in
                # one run, so give the architect plenty of headroom: no repetition
                # ceiling at all (0 = UNLIMITED_TOOL_REPEATS), and a high
                # max_iterations keeps a big build from being cut off mid-way (60
                # is the default ceiling).
                agent = await asyncio.to_thread(
                    create_agent, _ARCHITECT_AGENT_ID,
                    workspace=agent_workspace_path(project, root), streaming=True,
                    max_tool_repeats=0, max_iterations=400,
                )
                provider, model = agent.provider or "", agent.model or ""
                _update_run(run_id, {"provider": provider, "model": model})
                callback.bind_model(provider, model)
                await emit({"type": "agent", "agent_id": _ARCHITECT_AGENT_ID,
                            "provider": provider, "model": model})

                sink = GraphStreamSink(loop, queue, _graph_store, project_id, view)
                # Install the sink in the current context BEFORE create_task so it
                # propagates into the agent run (and the threads its sync tools use),
                # mirroring the artifact_sink pattern in the chat pipeline.
                token = graph_sink.set_handler(sink)
                task = asyncio.create_task(agent.arun(prompt, callbacks=[callback]))
                graph_sink.reset_handler(token)
                _register_run(project_id, view, task)

                # The callback/sink push token/tool/graph events straight onto
                # `queue`; the SSE relay drains it concurrently, so here we just
                # await the agent. The relay (and thus this run) is no longer tied
                # to the client connection — a disconnect can't interrupt it.
                try:
                    res = await task
                except asyncio.CancelledError:
                    # Stop button: keep whatever the agent already built and close
                    # the run cleanly (the worker itself is not cancelled).
                    status, reply = "stopped", (sink.summary() if sink and sink.touched else "Stopped.")
                    await emit({"type": "stopped"})
                    res = None
                if res is not None:
                    if getattr(res, "ok", False):
                        reply = _clean_agent_reply(str(res.agent_output))
                    else:
                        status, err = "failed", (getattr(res, "error", None) or "agent returned no output")
                        await emit(_error_event("agent", err))
            except Exception as e:  # noqa: BLE001
                status, err = "failed", str(e)
                await emit(_error_event("agent", err))
        else:
            status, err = "failed", "architect agent unavailable"
            await emit(_error_event("registry", err))

        # Always produce a result: prefer the agent's words, otherwise summarize
        # what it changed (so a run that e.g. only cleared the graph isn't silent).
        if not reply:
            reply = (sink.summary() if sink else "") or (
                f"Couldn't complete the request: {err}" if err
                else "I didn't change the graph — could you clarify what you'd like to build?")

        # Finalize the run record with the response + tool/usage process payload.
        usage = {
            "inbound_tokens": getattr(callback, "prompt_tokens", 0) if callback else 0,
            "outbound_tokens": getattr(callback, "completion_tokens", 0) if callback else 0,
            "total_tokens": getattr(callback, "total_tokens", 0) if callback else 0,
            "context_window": getattr(callback, "context_window", 0),
            "context_used": getattr(callback, "max_prompt_tokens", 0),
        }
        process_payload = {
            "llm_input_context": {
                "system_prompt": (getattr(callback, "_last_prompt_struct", {}) or {}).get("system_prompt", "") if callback else "",
                "user_message": user_message,
                "response": reply,
                "llm_invocations": getattr(callback, "llm_invocations", []) if callback else [],
            },
            "tool_calls": getattr(callback, "tool_history", []) if callback else [],
            "thinking": getattr(callback, "thinking_history", []) if callback else [],
            "llm_invoke_responses": getattr(callback, "llm_invoke_responses", []) if callback else [],
            "artifacts": getattr(callback, "artifact_history", []) if callback else [],
            "token_usage": usage,
        }
        # Close out the chat-log scaffold: the assistant reply, a summary marker
        # (token totals + tool count + duration the insights parser reads), and
        # the Finished/Status footer — matching the normal chat pipeline so this
        # run's process trace renders in the Messages insights view.
        finished = _iso()
        duration_ms = int((_time.perf_counter() - message_started) * 1000)
        summary_line = (
            f"[message_summary] id={msg_id} "
            f"inbound_tokens={usage['inbound_tokens']} "
            f"outbound_tokens={usage['outbound_tokens']} "
            f"total_tokens={usage['total_tokens']} "
            f"tool_calls={getattr(callback, 'tool_calls', 0) if callback else 0} "
            f"duration_ms={duration_ms}"
        )
        _append_log(log_lines, reply, log_file)
        _append_log(log_lines, summary_line, log_file)
        _write_log(log_file, log_lines + ["", f"Finished: {finished}", f"Status  : {status}"])

        _update_run(run_id, {
            "status": status, "finished_at": finished,
            "exit_code": 0 if status == "completed" else 1,
            "error": err, "response": reply, "process": process_payload,
        })

        _graph_store.append_message(project_id, view, "assistant", reply)
        await emit({"type": "message", "role": "assistant", "content": reply, "run_id": run_id})
        await emit({"type": "done", "run_id": run_id, "usage": usage})

        # Persist this turn's rich display trace (the user turn + every thinking /
        # tool / graph step + the reply) so a reload restores the full session.
        # Built from the recording queue, so it's captured even if the client
        # disconnected mid-run (the worker still runs to completion here).
        recorded = list(getattr(queue, "recorded", []))
        turn_items = [{"k": "user", "text": user_message}]
        for ev in recorded:
            item = _trace_item_for(ev)
            if item:
                turn_items.append(item)
        try:
            _graph_store.append_trace(project_id, view, turn_items)
        except Exception:
            pass

    async def event_stream():
        queue = _RecordingQueue()
        yield _sse({"type": "meta", "view": view})
        # Run the agent + persistence detached from this connection, then relay.
        worker = _spawn_detached(_run_turn_guarded(run_turn, queue))
        async for frame in _relay_queue(queue):
            yield frame
        await worker  # connected client: surface a crash in the worker

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/{project_id}/graph/chat/stop")
async def stop_project_graph_chat(project_id: str):
    """Stop any in-flight architect-chat / generate / planner run for the project.

    The agent runs detached from the SSE connection, so aborting the browser
    request can't stop it — this cancels the underlying agent task. Whatever the
    agent already built on the canvas is kept and the run closes cleanly.
    """
    if not _store.get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    cancelled = _cancel_runs(project_id)
    return {"stopped": cancelled > 0, "cancelled": cancelled}


async def _run_turn_guarded(run_turn, queue: asyncio.Queue):
    """Drive a detached run worker, always closing the relay with ``_STREAM_DONE``.

    The sentinel terminates the SSE relay even if the worker raises; if the
    client already disconnected it simply sits unread on the queue (harmless).
    """
    try:
        await run_turn(queue)
    except Exception as exc:  # noqa: BLE001 — last-resort so the stream still closes
        try:
            await queue.put({"type": "error", "source": "server", "error": str(exc)})
        except Exception:
            pass
    finally:
        await queue.put(_STREAM_DONE)


# ─────────────────────────── PLANNER (tasks from views) ────────────────────────────

_PLANNER_AGENT_ID = "planner"
# Store key for the planner chat (its trace/messages/session live on the Tasks
# tab, decoupled from the architecture/process graph views).
_TASKS_VIEW = "tasks"
_PLANNER_USER_MSG = "Generate tasks from the architecture & process graphs."


def _ensure_planner_agent() -> bool:
    """Ensure the Planner agent is registered, backfilling it on installs that
    predate it. Returns False if it cannot be made available."""
    try:
        from agents.registry import get_agent
        if get_agent(_PLANNER_AGENT_ID) is not None:
            return True
        from common.bootstrap import _ensure_system_agents
        _ensure_system_agents()
        return get_agent(_PLANNER_AGENT_ID) is not None
    except Exception:
        return False


@router.get("/{project_id}/tasks/chat")
async def get_tasks_chat(project_id: str):
    """Return the planner chat transcript + rich trace for the Tasks tab."""
    if not _store.get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "messages": _graph_store.get_messages(project_id, _TASKS_VIEW),
        "trace": _graph_store.get_trace(project_id, _TASKS_VIEW),
    }


@router.delete("/{project_id}/tasks/chat")
async def clear_tasks_chat(project_id: str):
    """Clear the planner chat and start a fresh session (tasks are left alone)."""
    if not _store.get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    epoch = _graph_store.clear_messages(project_id, _TASKS_VIEW, new_session=True)
    return {"cleared": True, "session_epoch": epoch}


@router.post("/{project_id}/tasks/generate")
async def generate_project_tasks(project_id: str, payload: Optional[ProjectTasksChat] = None):
    """Generate a task plan from the project's structure graphs (SSE).

    Runs the Planner agent scoped to the project so it can read the architecture
    and process graphs (via the injected ``get_project_graph``) and create tasks
    that attach to the project. Opens a full run record (so the run shows in
    Messages with its tool calls / thinking / stats, like any other run) and
    persists its rich trace under the Tasks-tab chat. Streams the agent's
    tool/thinking events, then a final ``message`` + ``done`` (with how many tasks
    were created).
    """
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    root = _project_root_path(project)
    # The "Generate" button sends no message (use the default); the chat input
    # sends the user's typed instruction for a conversational planning turn.
    user_message = (payload.message if payload else None) or _PLANNER_USER_MSG
    user_message = user_message.strip() or _PLANNER_USER_MSG

    async def run_plan(queue: asyncio.Queue):
        from projects.graph import agent_workspace_path
        from common.workspace_context import (
            _project_ctx, _workspace_ctx, normalize_project_id,
        )
        from managers.run_manager import (
            open_run as _open_run, update_run as _update_run,
            new_unique_run_id as _new_run_id, run_log_path as _run_log_path,
        )
        from datetime import datetime, timezone
        import time as _time
        from agents.callbacks import write_log as _write_log, append_log as _append_log

        def _iso():
            return datetime.now(timezone.utc).isoformat()

        async def emit(payload):
            await queue.put(payload)

        _graph_store.append_message(project_id, _TASKS_VIEW, "user", user_message)

        # Open a run record so the planner run appears in Messages, grouped per
        # project+session-epoch (Clear chat starts a fresh session).
        run_id = _new_run_id()
        log_file = _run_log_path(run_id)
        epoch = _graph_store.get_session_epoch(project_id, _TASKS_VIEW)
        conv_id = f"projtasks:{project_id}" + (f":{epoch}" if epoch else "")
        session_id = None
        try:
            from common.session_service import get_or_create_chat_session
            session_id = get_or_create_chat_session(
                conversation_id=conv_id,
                title=f"{project.name} · task plan" + (f" #{epoch + 1}" if epoch else ""),
                workspace=project.workspace,
                agent_id=_PLANNER_AGENT_ID,
            )
        except Exception:
            session_id = None
        _open_run(run_id, _PLANNER_AGENT_ID, task_id=conv_id, session_id=session_id,
                  session_type="chat", message_origin="planner-chat", channel="chat",
                  workspace=project.workspace, title=user_message[:60], log_file=str(log_file))

        msg_id = run_id[:8]
        started = _iso()
        log_lines = [
            f"=== Chat message  run_id={run_id} ===",
            f"Started   : {started}",
            f"Agent     : {_PLANNER_AGENT_ID}",
            f"Workspace : {project.workspace or '—'}",
            f"Conv ID   : {conv_id}",
            f"Session ID: {session_id or '—'}",
            f"Title     : {user_message[:60]}",
            "",
            f"=== Message at {started} id={msg_id} ===",
            "--- User message ---",
            user_message,
            "",
            "--- Agent response (stream) ---",
        ]
        _write_log(log_file, log_lines)
        message_started = _time.perf_counter()

        # Snapshot the task count so we can report how many were created.
        try:
            before = len(_project_tasks_for(project_id) or [])
        except Exception:
            before = 0

        # Recent conversation so multi-turn planning ("also split X", "reprioritise
        # Y") has context. The user turn was just recorded above, so drop it here.
        history = _graph_store.get_messages(project_id, _TASKS_VIEW)[:-1]
        convo = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in history[-12:])
        prompt = (
            "You are the project's task planner: read its structure graphs and manage the "
            "task tree in the shared tracker.\n\n"
            "Rules:\n"
            "1. Call list_tasks first to see what already exists — extend/refine it, do not duplicate.\n"
            "2. Call get_project_graph for view='process' AND for view='architecture' to read both graphs.\n"
            "3. Create top-level tasks for the major components/stages with create_task, break them into "
            "steps with add_subtask, and use create_sequence for work that must run in order. Align task "
            "titles with the project's structure. For a refinement request, change only what's asked.\n\n"
            f"--- CONVERSATION SO FAR ---\n{convo or '(none)'}\n\n"
            f"--- USER REQUEST ---\n{user_message}\n\n"
            "After applying the changes, reply with ONE short sentence summarising what you did."
        )

        reply, status, err = "", "completed", None
        callback = None
        provider = model = ""
        # Scope the run to the project BEFORE building the agent: create_agent
        # injects the read-only get_project_graph tool only when
        # resolve_active_project() is set, and new tasks inherit the active
        # project the same way. asyncio.to_thread / create_task both copy the
        # current context, so create_agent and the run see the scope; we reset
        # once the task has captured it (mirrors the graph_sink handler pattern).
        ws_token = _workspace_ctx.set(project.workspace)
        pj_token = _project_ctx.set(normalize_project_id(project_id))
        if not _ensure_planner_agent():
            status, err = "failed", "planner agent unavailable"
            await emit(_error_event("registry", err))
        else:
            try:
                from agents.agent_factory import create_agent
                from agents.callbacks import ChatStreamCallback

                loop = asyncio.get_running_loop()
                callback = ChatStreamCallback(loop, queue, log_lines, log_file, session_id=session_id)

                # Planning a project is task after task through the same tool, so
                # there is no repetition ceiling here either (0 = UNLIMITED_TOOL_REPEATS).
                agent = await asyncio.to_thread(
                    create_agent, _PLANNER_AGENT_ID,
                    workspace=agent_workspace_path(project, root), streaming=True,
                    max_tool_repeats=0, max_iterations=400,
                )
                provider, model = agent.provider or "", agent.model or ""
                _update_run(run_id, {"provider": provider, "model": model})
                callback.bind_model(provider, model)
                await emit({"type": "agent", "agent_id": _PLANNER_AGENT_ID,
                            "provider": provider, "model": model})

                task = asyncio.create_task(agent.arun(prompt, callbacks=[callback]))
                _register_run(project_id, "__plan__", task)

                try:
                    res = await task
                except asyncio.CancelledError:
                    status, res = "stopped", None
                    await emit({"type": "stopped"})
                if res is not None:
                    if getattr(res, "ok", False):
                        reply = _clean_agent_reply(str(res.agent_output))
                    else:
                        status, err = "failed", (getattr(res, "error", None) or "agent returned no output")
                        await emit(_error_event("agent", err))
            except Exception as e:  # noqa: BLE001
                status, err = "failed", str(e)
                await emit(_error_event("agent", err))
        _project_ctx.reset(pj_token)
        _workspace_ctx.reset(ws_token)

        try:
            after = len(_project_tasks_for(project_id) or [])
        except Exception:
            after = before
        created = max(0, after - before)

        if not reply:
            reply = (f"Couldn't complete the request: {err}" if err
                     else f"Created {created} task(s) from the project's graphs.")

        # Finalize the run record with the response + tool/usage process payload,
        # so the run renders in Messages exactly like a normal chat run.
        usage = {
            "inbound_tokens": getattr(callback, "prompt_tokens", 0) if callback else 0,
            "outbound_tokens": getattr(callback, "completion_tokens", 0) if callback else 0,
            "total_tokens": getattr(callback, "total_tokens", 0) if callback else 0,
            "context_window": getattr(callback, "context_window", 0),
            "context_used": getattr(callback, "max_prompt_tokens", 0),
        }
        process_payload = {
            "llm_input_context": {
                "system_prompt": (getattr(callback, "_last_prompt_struct", {}) or {}).get("system_prompt", "") if callback else "",
                "user_message": user_message,
                "response": reply,
                "llm_invocations": getattr(callback, "llm_invocations", []) if callback else [],
            },
            "tool_calls": getattr(callback, "tool_history", []) if callback else [],
            "thinking": getattr(callback, "thinking_history", []) if callback else [],
            "llm_invoke_responses": getattr(callback, "llm_invoke_responses", []) if callback else [],
            "artifacts": getattr(callback, "artifact_history", []) if callback else [],
            "token_usage": usage,
        }
        finished = _iso()
        duration_ms = int((_time.perf_counter() - message_started) * 1000)
        summary_line = (
            f"[message_summary] id={msg_id} "
            f"inbound_tokens={usage['inbound_tokens']} "
            f"outbound_tokens={usage['outbound_tokens']} "
            f"total_tokens={usage['total_tokens']} "
            f"tool_calls={getattr(callback, 'tool_calls', 0) if callback else 0} "
            f"duration_ms={duration_ms}"
        )
        _append_log(log_lines, reply, log_file)
        _append_log(log_lines, summary_line, log_file)
        _write_log(log_file, log_lines + ["", f"Finished: {finished}", f"Status  : {status}"])

        _update_run(run_id, {
            "status": status, "finished_at": finished,
            "exit_code": 0 if status == "completed" else 1,
            "error": err, "response": reply, "process": process_payload,
        })

        _graph_store.append_message(project_id, _TASKS_VIEW, "assistant", reply)
        await emit({"type": "message", "role": "assistant", "content": reply, "run_id": run_id})
        await emit({"type": "done", "created": created, "run_id": run_id, "usage": usage})

        # Persist the rich trace under the Tasks-tab chat so a reload restores it
        # (captured in the worker → survives a client disconnect mid-run).
        recorded = list(getattr(queue, "recorded", []))
        turn_items = [{"k": "user", "text": user_message}]
        for ev in recorded:
            item = _trace_item_for(ev)
            if item:
                turn_items.append(item)
        try:
            _graph_store.append_trace(project_id, _TASKS_VIEW, turn_items)
        except Exception:
            pass

    async def event_stream():
        queue = _RecordingQueue()
        yield _sse({"type": "meta", "view": _TASKS_VIEW})
        worker = _spawn_detached(_run_turn_guarded(run_plan, queue))
        async for frame in _relay_queue(queue):
            yield frame
        await worker

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


# ─────────────────────────── REPO ────────────────────────────

def _repo_provider(project: Project) -> Optional[str]:
    """Provider name for git auth injection, when applicable."""
    repo_type = str(project.repo.type.value if hasattr(project.repo.type, "value") else project.repo.type)
    return repo_type if repo_type in ("github", "gitlab") else None


def _run_issue_sync(project: Project) -> dict:
    """Run issue sync, mapping failures to a UI-safe error payload."""
    try:
        return _sync_project_issues(project)
    except (GitProviderError, ValueError) as e:
        return {"imported": 0, "updated": 0, "total": 0, "error": str(e)}


@router.post("/import-from-repo")
async def import_from_repo(payload: ProjectImportFromRepo):
    """Import a GitHub/GitLab repo as a new project: clone + optional issue import."""
    ws_folder = get_workspace_folder(payload.workspace)
    if ws_folder is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{payload.workspace}' not found")

    try:
        repo_info = await asyncio.to_thread(get_provider(payload.provider).get_repo, payload.remote_id)
    except GitProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))

    name = (payload.name or "").strip() or repo_info["name"]
    branch = (payload.branch or "").strip() or repo_info["default_branch"]
    folder = project_folder_name(name)
    local_path = f"{folder}/repo"
    clone_dir = ws_folder / local_path

    if clone_dir.exists():
        raise HTTPException(status_code=409, detail=f"Target directory already exists: {clone_dir}")
    if any(p.workspace == payload.workspace and project_folder_name(p.name) == folder for p in _store.list()):
        raise HTTPException(status_code=409, detail=f"A project named '{name}' already exists in this workspace")

    resolve_project_root(payload.workspace, folder)
    try:
        await asyncio.to_thread(
            git_ops.clone, repo_info["clone_url"], clone_dir,
            branch=branch, provider=payload.provider,
        )
    except GitOpsError as e:
        raise HTTPException(status_code=500, detail=f"Clone failed: {e}")

    project = Project(
        name=name,
        description=repo_info.get("description"),
        type="code",
        workspace=payload.workspace,
        repo=RepoConfig(
            type=payload.provider,
            url=repo_info["clone_url"],
            branch=branch,
            local_path=local_path,
            remote_id=repo_info["remote_id"],
        ),
    )
    _store.add(project)

    issues = None
    if payload.import_issues:
        issues = await asyncio.to_thread(_run_issue_sync, project)

    d = _project_to_dict(project)
    d["folder"] = folder
    return {"project": d, "cloned": True, "issues": issues}


@router.post("/{project_id}/connect-repo")
async def connect_repo(project_id: str, payload: ProjectConnectRepo):
    """Connect an existing project to a GitHub/GitLab repo: clone + optional issue import."""
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    ws_folder = get_workspace_folder(project.workspace)
    if ws_folder is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{project.workspace}' not found")

    try:
        repo_info = await asyncio.to_thread(get_provider(payload.provider).get_repo, payload.remote_id)
    except GitProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))

    branch = (payload.branch or "").strip() or repo_info["default_branch"]
    folder = project_folder_name(project.name)
    local_path = project.repo.local_path or f"{folder}/repo"
    clone_dir = ws_folder / local_path

    cloned = False
    if clone_dir.exists():
        existing_remote = git_ops.remote_url(clone_dir)
        if existing_remote not in (repo_info["clone_url"], repo_info["web_url"]):
            raise HTTPException(
                status_code=409,
                detail=f"Directory {clone_dir} already exists and is not a clone of this repo",
            )
    else:
        try:
            await asyncio.to_thread(
                git_ops.clone, repo_info["clone_url"], clone_dir,
                branch=branch, provider=payload.provider,
            )
            cloned = True
        except GitOpsError as e:
            raise HTTPException(status_code=500, detail=f"Clone failed: {e}")

    project = _store.update(project_id, repo={
        "type": payload.provider,
        "url": repo_info["clone_url"],
        "branch": branch,
        "local_path": local_path,
        "remote_id": repo_info["remote_id"],
    })

    issues = None
    if payload.import_issues:
        issues = await asyncio.to_thread(_run_issue_sync, project)

    d = _project_to_dict(project)
    d["folder"] = folder
    return {"project": d, "cloned": cloned, "issues": issues}


@router.post("/{project_id}/sync-issues")
async def sync_issues(project_id: str):
    """Import new and refresh previously imported issues as project tasks."""
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if _repo_provider(project) is None or not project.repo.remote_id:
        raise HTTPException(
            status_code=400,
            detail="Project is not connected to a GitHub/GitLab repo",
        )
    try:
        return await asyncio.to_thread(_sync_project_issues, project)
    except (GitProviderError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{project_id}/clone-repo")
async def clone_repo(project_id: str):
    """Clone the project's git repo into the workspace directory."""
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    repo = project.repo
    if not repo.url:
        raise HTTPException(status_code=400, detail="No repo URL configured")

    ws_folder = get_workspace_folder(project.workspace)
    if ws_folder is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{project.workspace}' not found")

    clone_dir = ws_folder / (repo.local_path or "repo")

    if clone_dir.exists():
        raise HTTPException(status_code=409, detail=f"Target directory already exists: {clone_dir}")

    try:
        output = await asyncio.to_thread(
            git_ops.clone, repo.url, clone_dir,
            branch=repo.branch or None, provider=_repo_provider(project),
        )
    except GitOpsError as e:
        status = 504 if "timed out" in str(e) else 500
        raise HTTPException(status_code=status, detail=str(e))

    # Update local_path in project
    rel_path = repo.local_path or "repo"
    _store.update(project_id, repo={"local_path": rel_path})

    return {"cloned": True, "path": str(clone_dir), "output": output}


@router.post("/attach")
async def attach_project(payload: ProjectAttach):
    """Register an existing directory as a project in a workspace, in place.

    The project folder inside the workspace becomes a link to that directory, so
    agents work on the real files and a git checkout keeps its history. If the
    directory is already a git repo, git-status and git-pull work on it right
    away — no clone step.

    `path` is resolved by *this process*: in a container it must be a path inside
    the container, so bind mount the host directory first.
    """
    ws_folder = get_workspace_folder(payload.workspace)
    if ws_folder is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{payload.workspace}' not found")

    target = Path(payload.path).expanduser().resolve()
    if not target.exists():
        raise HTTPException(status_code=400, detail=f"No such directory: {target}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {target}")
    _check_attach_allowed(target)

    # The folder name has to survive project_folder_name() unchanged, otherwise
    # the link, the project's derived folder and repo.local_path would disagree.
    folder = project_folder_name(payload.name or target.name)
    link = ws_folder / folder
    # Linking a directory into itself, or into one it already contains, makes a
    # cycle: ws/proj -> ws means ws/proj/proj/proj resolves forever, and any
    # recursive walk of the workspace hangs. Refuse all three overlapping cases.
    ws_real = ws_folder.resolve()
    if target == ws_real:
        raise HTTPException(
            status_code=400,
            detail=f"{target} is the workspace itself — it is already where agents work, "
                   "so it does not also need to be a project inside it",
        )
    if target in ws_real.parents:
        raise HTTPException(
            status_code=400,
            detail=f"{target} contains workspace '{payload.workspace}' — linking it inside "
                   "that workspace would create a loop",
        )
    if ws_real in target.parents:
        raise HTTPException(
            status_code=400,
            detail=f"{target} is already inside workspace '{payload.workspace}' — create the "
                   "project normally instead of attaching it",
        )
    if link.is_symlink():
        if link.resolve() != target:
            raise HTTPException(
                status_code=409,
                detail=f"'{folder}' in this workspace already points at {link.resolve()}",
            )
    elif link.exists():
        raise HTTPException(
            status_code=409,
            detail=f"'{folder}' already exists in workspace '{payload.workspace}' as a real directory",
        )
    else:
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as e:
            raise HTTPException(status_code=400, detail=f"Could not link: {e}")

    is_repo = (target / ".git").exists()
    project = Project(
        name=folder,
        description=payload.description,
        type=payload.type or "code",
        workspace=payload.workspace,
        repo=RepoConfig(
            type="local" if is_repo else "none",
            local_path=folder,
            branch=None,
        ),
    )
    _store.add(project)

    d = _project_to_dict(project)
    d["folder"] = folder
    d["attached"] = True
    d["target"] = str(target)
    d["is_git_repo"] = is_repo
    return d


@router.get("/{project_id}/git-status")
async def git_status(project_id: str):
    """Return git status for the project's local repo."""
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    ws_folder = get_workspace_folder(project.workspace)
    if ws_folder is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    repo_path = ws_folder / (project.repo.local_path or "repo")
    if not repo_path.exists():
        raise HTTPException(status_code=404, detail="Repo directory not found. Clone first.")

    def _collect_git_status():
        status = subprocess.run(
            ["git", "status", "--short"], cwd=str(repo_path),
            capture_output=True, text=True, timeout=10
        )
        log = subprocess.run(
            ["git", "log", "--oneline", "-10"], cwd=str(repo_path),
            capture_output=True, text=True, timeout=10
        )
        branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=str(repo_path),
            capture_output=True, text=True, timeout=10
        )
        return status, log, branch

    try:
        status, log, branch = await asyncio.to_thread(_collect_git_status)
        return {
            "branch": branch.stdout.strip(),
            "status": status.stdout.strip(),
            "recent_commits": log.stdout.strip(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{project_id}/git-pull")
async def git_pull(project_id: str):
    """Pull latest changes from remote."""
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    ws_folder = get_workspace_folder(project.workspace)
    if ws_folder is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    repo_path = ws_folder / (project.repo.local_path or "repo")
    if not repo_path.exists():
        raise HTTPException(status_code=404, detail="Repo directory not found")

    try:
        output = await asyncio.to_thread(git_ops.pull, repo_path, provider=_repo_provider(project))
        return {"output": output, "returncode": 0}
    except GitOpsError as e:
        status = 504 if "timed out" in str(e) else 500
        raise HTTPException(status_code=status, detail=str(e))


# ─────────────────────────── BACKEND / SWAGGER ────────────────────────────

@router.get("/{project_id}/swagger-spec")
async def get_swagger_spec(project_id: str, base_url: Optional[str] = Query(None)):
    """Fetch the OpenAPI JSON spec from the project's backend."""
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    backend = project.backend
    if not backend.enabled:
        raise HTTPException(status_code=400, detail="Backend is not enabled for this project")

    base = base_url or backend.base_url or (f"http://localhost:{backend.port}" if backend.port else None)
    if not base:
        raise HTTPException(status_code=400, detail="No backend URL configured. Provide base_url parameter.")
    # Try common OpenAPI spec paths
    for spec_path in ["/openapi.json", "/swagger.json", "/api-docs"]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{base}{spec_path}")
                if resp.status_code == 200:
                    return resp.json()
        except Exception:
            continue

    raise HTTPException(status_code=502, detail="Could not fetch OpenAPI spec from backend")


@router.post("/{project_id}/api-request")
async def proxy_api_request(project_id: str, payload: ProjectApiRequest):
    """Proxy an HTTP request to the project's backend."""
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    backend = project.backend
    if not backend.enabled:
        raise HTTPException(status_code=400, detail="Backend is not enabled for this project")

    base = payload.base_url or backend.base_url or (f"http://localhost:{backend.port}" if backend.port else None)
    if not base:
        raise HTTPException(status_code=400, detail="No backend URL configured. Set base_url in the API tab.")
    base = _validated_api_base_url(base)
    url = f"{base}{payload.path}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method=payload.method.upper(),
                url=url,
                headers=payload.headers or {},
                json=payload.body if payload.method.upper() not in ("GET", "DELETE") else None,
            )
            try:
                body = response.json()
            except Exception:
                body = response.text

            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": body,
            }
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail=f"Cannot connect to backend at {base}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Request to backend timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── The project registry chat ────────────────────────────────────────────────
#
# The other two project chats are about ONE project: the architect builds its
# graph, the planner turns that into tasks. This one is about the registry — it
# creates projects, renames them, retires them — so it hangs off the list page
# and its "entity" is the workspace, not a project row.
#
# Until this existed, project_manager was a system agent with no caller at all.

REGISTRY_AGENT_ID = "project_manager"
REGISTRY_CHAT_KIND = "projects"


class RegistryChatIn(BaseModel):
    message: str = ""
    #: Accepted in the body too, but the query parameter wins — the shared SSE
    #: client posts only ``{message}``, so the query string is how every caller
    #: actually gets the workspace across.
    workspace: Optional[str] = None


def _registry_chat_id(workspace: Optional[str]) -> str:
    """One conversation per workspace: the registry is workspace-scoped, and a
    single global thread would mix unrelated efforts."""
    return (workspace or "default").strip() or "default"


def _registry_state(workspace: str) -> dict:
    """The projects in this workspace, with their task counts."""
    all_tasks = tasks_service.list_tasks()
    out = []
    for p in _store.list():
        if p.workspace != workspace:
            continue
        d = _project_to_dict(p)
        d["tasks_count"] = sum(1 for t in all_tasks if t.project_id == p.id)
        d["folder"] = project_folder_name(p.name)
        out.append(d)
    return {"workspace": workspace, "projects": out}


def _registry_chat_prompt(workspace: str, history: list, user_message: str) -> str:
    """One turn's prompt: the workspace's registry, then the talk."""
    from chat.entity_chat import transcript_block

    state = _registry_state(workspace)
    parts = [
        "You are managing the PROJECT REGISTRY of one workspace in this "
        "platform. The user is looking at the project list: every change you "
        "make with your tools appears there.",
        "",
        f"Workspace: {workspace}",
        "",
        "=== Projects in this workspace ===",
        json.dumps(state["projects"], ensure_ascii=False, indent=2, default=str),
        "",
        "Rules for this conversation:",
        f"- Create projects in workspace '{workspace}' unless the user names a "
        "different one.",
        "- A project is a folder plus a record. Creating one does not analyse "
        "any code and does not plan any work: the Architect Agent builds the "
        "structure graph from the project's own page, and the Planner turns "
        "that into tasks. Say which of those comes next instead of pretending "
        "to have done it.",
        "- Deleting a project removes the record, not the folder. Say so before "
        "deleting, and never delete one the user has not named.",
        "- A project carrying tasks is not an empty record. Report the count "
        "before proposing to retire it.",
        "- When the user only asks a question, answer it without changing "
        "anything.",
        "- Finish with one short paragraph: what exists now, and what the next "
        "step is.",
    ]
    talk = transcript_block(history[:-1])
    if talk:
        parts += ["", "=== Conversation so far ===", talk]
    parts += ["", "=== The user's latest message ===", user_message]
    return "\n".join(parts)


@router.get("/registry/chat")
async def get_registry_chat(workspace: Optional[str] = Query(None)):
    """The registry chat for one workspace: transcript plus the replay trace."""
    from common.entity_chat_store import entity_chat_store

    chat_id = _registry_chat_id(workspace)
    chat_store = entity_chat_store()
    return {
        "messages": chat_store.get_messages(REGISTRY_CHAT_KIND, chat_id),
        "trace": chat_store.get_trace(REGISTRY_CHAT_KIND, chat_id),
        # What the session picker needs to reach this chat's history
        # (routes/entity_chats.py); the browser never builds the key itself.
        "chat_ref": {"kind": REGISTRY_CHAT_KIND, "id": chat_id},
    }


@router.delete("/registry/chat")
async def clear_registry_chat(workspace: Optional[str] = Query(None)):
    """Clear the transcript and start a fresh session. No project is touched."""
    from common.entity_chat_store import entity_chat_store

    chat_id = _registry_chat_id(workspace)
    epoch = entity_chat_store().clear(REGISTRY_CHAT_KIND, chat_id, new_session=True)
    return {"cleared": True, "session_epoch": epoch}


@router.post("/registry/chat")
async def chat_registry(payload: RegistryChatIn,
                        workspace: Optional[str] = Query(None)):
    """Run one turn of the project registry chat (SSE).

    Streams the agent's ``tool_*`` / ``thinking`` / ``token`` events, then a
    ``projects`` event carrying the registry as it stands after the turn, the
    final ``message`` and ``done``.
    """
    from chat.entity_chat import (
        EntityChatSpec, RecordingQueue, SSE_HEADERS, guarded, relay_queue,
        run_entity_chat_turn, spawn_detached, sse,
    )
    from common.bootstrap import ensure_system_agent

    if not ensure_system_agent(REGISTRY_AGENT_ID):
        raise HTTPException(status_code=503,
                            detail=f"The '{REGISTRY_AGENT_ID}' agent is not registered")
    user_message = (payload.message or "").strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="Empty message")

    workspace = _registry_chat_id(workspace or payload.workspace)
    chat_id = workspace
    before = _registry_state(workspace)

    def _summarize() -> str:
        after = _registry_state(workspace)
        if after == before:
            return ""
        was = {p["id"] for p in before["projects"]}
        now = {p["id"] for p in after["projects"]}
        bits = []
        if now - was:
            bits.append(f"created {len(now - was)} project(s)")
        if was - now:
            bits.append(f"removed {len(was - now)} project(s)")
        if not bits:
            bits.append("updated a project")
        return "Done — " + ", ".join(bits) + "."

    spec = EntityChatSpec(
        kind=REGISTRY_CHAT_KIND,
        agent_id=REGISTRY_AGENT_ID,
        title=f"{workspace} · projects",
        workspace=workspace,
    )

    async def run_turn(queue: asyncio.Queue):
        from common.workspace_context import _workspace_ctx

        # The registry tools resolve the workspace from this ContextVar, so a
        # project lands where the user is looking.
        _workspace_ctx.set(workspace)

        await run_entity_chat_turn(
            queue, spec, chat_id, user_message,
            lambda history: _registry_chat_prompt(workspace, history, user_message),
            summarize=_summarize,
        )
        await queue.put({"type": "projects", "projects": _registry_state(workspace)["projects"]})

    async def event_stream():
        queue = RecordingQueue()
        yield sse({"type": "meta", "kind": REGISTRY_CHAT_KIND, "id": chat_id})
        worker = spawn_detached(guarded(run_turn, queue))
        async for frame in relay_queue(queue):
            yield frame
        await worker

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers=SSE_HEADERS)


@router.post("/registry/chat/stop")
async def stop_registry_chat(workspace: Optional[str] = Query(None)):
    """Stop the in-flight registry turn for this workspace."""
    from chat.entity_chat import cancel_entity_runs

    cancelled = cancel_entity_runs(REGISTRY_CHAT_KIND, _registry_chat_id(workspace))
    return {"stopped": cancelled > 0, "cancelled": cancelled}
