"""The MCP server's tools beyond the agent turn (routes/mcp_server.py).

``ask_agent`` hands a client one agent with all it can do; these give any MCP
client (Claude Code, Cursor, VS Code, Windsurf, Codex, Claude Desktop...) the
rest of a workspace directly, without a model call in between:

* files: ``list_files``, ``read_file``, ``upload_file`` (files/service.py);
* knowledge: ``list_knowledge``, ``search_knowledge`` (the memory pools and
  their indexed documents, ranked the way ``search_memory`` ranks them);
* workflows: ``list_workflows``, ``run_workflow`` (teams, flows and loops,
  launched by their own launchers as entity runs);
* runs: ``list_runs``, ``get_run``, ``stop_run`` over agent runs and entity
  runs alike.

Reads need the workspace to be reachable with the credential (visible to the
caller and inside a key's scope); anything that spends money or writes
(``upload_file``, ``run_workflow``, ``stop_run``) needs the ``editor`` role
there as well, like ``ask_agent`` and ``/v1``. Nothing here creates, edits or
deletes agents, settings or connectors: what an IDE agent forwards may come
from any file it read, so the hub's configuration stays behind the dashboard.

A personal memory pool (memory/personal.py) is searched and listed only for
its owner, never for an admin: through MCP nobody reads another person's
memory. ``list_runs`` shows the caller's own runs unless asked for every run
of the workspace.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import HTTPException, Request

from common import identity

log = logging.getLogger(__name__)


class ToolError(Exception):
    """A tool call the client should see as a failed tool result
    (``isError: true``), not as a protocol error."""


class Ctx:
    """What a tool sees of the request: who calls, and from where."""

    def __init__(self, request: Request, principal: Any) -> None:
        self.request = request
        self.principal = principal


ToolFn = Callable[[Ctx, Dict[str, Any]], Awaitable[Dict[str, Any]]]

#: Longest text read_file returns in one call; a longer file is read in pages.
MAX_READ_CHARS = 200_000
DEFAULT_READ_CHARS = 20_000

#: run_workflow waits this long for a result by default, and at most MAX.
DEFAULT_WORKFLOW_WAIT = 120
MAX_WORKFLOW_WAIT = 1800
_POLL_SECONDS = 2.0

WORKFLOW_KINDS = ("team", "flow", "loop")


# ── Workspaces ───────────────────────────────────────────────────────────────

def workspace_names(principal: Any) -> List[str]:
    """The workspaces this caller can reach: visible to it, and inside an API
    key's scope."""
    from common import access
    from workspace.storage import list_workspace_folders
    names = sorted(p.name for p in list_workspace_folders())
    if "default" not in names:
        names.insert(0, "default")
    return [n for n in names
            if access.can_see_workspace(principal, n)
            and (principal is None or principal.reaches(n))]


def _named_workspace(ctx: Ctx, named: Any) -> str:
    from routes import openai_compat as v1
    workspace = str(named or "").strip() or v1._header_workspace(ctx.request)
    scope = getattr(ctx.principal, "scope", None)
    if not workspace and scope is not None and len(scope) == 1:
        workspace = scope[0]
    return workspace or "default"


def read_workspace(ctx: Ctx, named: Any) -> str:
    """The workspace a read runs in: the argument, else the header, else a
    key's only workspace, else ``default``; refused when not reachable."""
    workspace = _named_workspace(ctx, named)
    if workspace not in workspace_names(ctx.principal):
        raise ToolError(f"workspace '{workspace}' is not reachable with this credential")
    return workspace


def require_editor(ctx: Ctx, workspace: str) -> None:
    from common.auth import WS_EDITOR
    try:
        identity.require_role(ctx.principal, workspace=workspace, role=WS_EDITOR)
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from exc


def write_workspace(ctx: Ctx, named: Any) -> str:
    """As :func:`read_workspace`, and the caller must be an editor there."""
    workspace = read_workspace(ctx, named)
    require_editor(ctx, workspace)
    return workspace


def check_budget(principal: Any) -> None:
    """The caller's tokens per day and a key's money cap, checked before
    anything that runs a model starts."""
    from common import rate_limit
    within, _ = rate_limit.check_tokens_per_day(principal)
    if not within:
        raise ToolError("daily token limit reached; it resets at 00:00 UTC")
    within_budget, _ = rate_limit.check_key_budget(principal)
    if not within_budget:
        raise ToolError("this API key has reached its monthly budget")


def _audit(ctx: Ctx, action: str, *, object_type: str, object_id: str, workspace: str,
           details: Optional[Dict[str, Any]] = None) -> None:
    from common import audit
    audit.record(action, principal=ctx.principal, object_type=object_type, object_id=object_id,
                 workspace=workspace, ip=identity.client_ip(ctx.request), path="/v1/mcp",
                 details={"via": "mcp", **(details or {})})


def _int_arg(args: Dict[str, Any], name: str, default: int, low: int, high: int) -> int:
    value = args.get(name)
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ToolError(f"{name} must be a number") from None
    return max(low, min(number, high))


# ── Files ────────────────────────────────────────────────────────────────────

def _file_summary(record: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: record.get(k) for k in ("file_id", "name", "mime_type", "size", "source",
                                       "created_at")}
    path = (record.get("meta") or {}).get("path")
    if path:
        out["path"] = path
    return out


async def list_files(ctx: Ctx, args: Dict[str, Any]) -> Dict[str, Any]:
    from files import service
    workspace = read_workspace(ctx, args.get("workspace"))
    limit = _int_arg(args, "limit", 50, 1, 500)
    records = await asyncio.to_thread(
        service.list_files, workspace, source=str(args.get("source") or "") or None,
        q=str(args.get("query") or "") or None, limit=limit)
    return {"workspace": workspace, "files": [_file_summary(r) for r in records]}


async def read_file(ctx: Ctx, args: Dict[str, Any]) -> Dict[str, Any]:
    from files import service
    file_id = str(args.get("file_id") or "").strip()
    path = str(args.get("path") or "").strip()
    if not file_id and not path:
        raise ToolError("give file_id (from list_files) or path")
    if file_id:
        record = service.get_file(file_id)
        if record is None or record["workspace"] not in workspace_names(ctx.principal):
            # The same answer for "no such file" and "not yours".
            raise ToolError(f"no file '{file_id}'")
    else:
        workspace = read_workspace(ctx, args.get("workspace"))
        record = service.file_at_path(workspace, path)
        if record is None:
            raise ToolError(f"no file at '{path}' in workspace '{workspace}'")
    try:
        text = await asyncio.to_thread(service.extract_text, record["file_id"])
    except KeyError:
        raise ToolError("the content of this file is no longer available") from None
    out = _file_summary(record)
    out["workspace"] = record["workspace"]
    if text is None:
        raise ToolError(f"{service.describe(record)} is not a text file; it cannot be read as text")
    offset = _int_arg(args, "offset", 0, 0, max(0, len(text)))
    cap = _int_arg(args, "max_chars", DEFAULT_READ_CHARS, 1, MAX_READ_CHARS)
    chunk = text[offset:offset + cap]
    end = offset + len(chunk)
    out.update({"text": chunk, "offset": offset, "total_chars": len(text),
                "truncated": end < len(text)})
    if end < len(text):
        out["next_offset"] = end
    return out


async def upload_file(ctx: Ctx, args: Dict[str, Any]) -> Dict[str, Any]:
    from files import service
    workspace = write_workspace(ctx, args.get("workspace"))
    name = str(args.get("name") or "").strip()
    path = str(args.get("path") or "").strip()
    if not name and not path:
        raise ToolError("name is required")
    content, encoded = args.get("content"), args.get("content_base64")
    if (content is None) == (encoded is None):
        raise ToolError("give exactly one of content (text) or content_base64")
    if encoded is not None:
        try:
            data = base64.b64decode(str(encoded), validate=True)
        except (binascii.Error, ValueError):
            raise ToolError("content_base64 is not valid base64") from None
    else:
        data = str(content).encode("utf-8")
    limit = service.max_file_bytes()
    if len(data) > limit:
        raise ToolError(f"the file is larger than the limit of {limit} bytes per file")
    created_by = getattr(ctx.principal, "id", None)
    try:
        if path:
            record = await asyncio.to_thread(service.write_folder_file, workspace, path, data,
                                             source="upload", created_by=created_by)
            deduplicated = False
        else:
            existing = service.find_duplicate(workspace, hashlib.sha256(data).hexdigest())
            if existing is not None:
                return {**_file_summary(existing), "workspace": workspace, "deduplicated": True}
            record = await asyncio.to_thread(
                service.create_file, workspace, name, data,
                mime_type=str(args.get("mime_type") or "") or None, source="upload",
                created_by=created_by)
            deduplicated = False
    except service.FileError as exc:
        raise ToolError(str(exc)) from exc
    _audit(ctx, "file.upload", object_type="file", object_id=record["file_id"],
           workspace=workspace, details={"name": record["name"], "size": record["size"],
                                         **({"path": path} if path else {})})
    return {**_file_summary(record), "workspace": workspace, "deduplicated": deduplicated}


# ── Knowledge ────────────────────────────────────────────────────────────────

def _pools(ctx: Ctx, workspace: str) -> List[Any]:
    """The memory pools of ``workspace`` this caller may read: a pool bound
    to it (``default`` also holds the pools bound to none), and of the
    personal pools only the caller's own."""
    from memory import personal
    from memory.store import MemoryStore
    user_id = str(getattr(ctx.principal, "id", "") or "")
    out = []
    for mem in MemoryStore().load():
        bound = mem.workspace or "default"
        if bound != workspace:
            continue
        if personal.is_personal(mem) and str(mem.owner_user or "") != user_id:
            continue
        out.append(mem)
    return out


def _pick_pool(pools: List[Any], wanted: str) -> Any:
    wanted = wanted.strip()
    for mem in pools:
        if str(mem.id) == wanted or (mem.name or "").strip().lower() == wanted.lower():
            return mem
    raise ToolError(f"no memory pool '{wanted}' in this workspace; call list_knowledge")


async def list_knowledge(ctx: Ctx, args: Dict[str, Any]) -> Dict[str, Any]:
    from rag import pool_file_index
    workspace = read_workspace(ctx, args.get("workspace"))
    pools = []
    for mem in _pools(ctx, workspace):
        try:
            documents = len(pool_file_index(str(mem.id)))
        except Exception:  # noqa: BLE001 - a count is a readout, not a gate
            documents = 0
        pools.append({"pool_id": str(mem.id), "name": mem.name, "description": mem.description,
                      "personal": mem.kind == "personal", "documents": documents,
                      "notes": len(mem.notes or [])})
    return {"workspace": workspace, "pools": pools}


def _search_pool(mem: Any, query: str, top_k: int) -> List[Dict[str, Any]]:
    """One pool ranked for ``query`` the way ``search_memory`` ranks it: the
    pool's blocks, slots, notes and episodes by BM25, fused with its indexed
    documents (BM25, plus vectors when a store is configured)."""
    from memory.rag_query import search_rag
    from memory.ranking import Candidate, pool_candidates, rank_candidates
    from memory.tool import _rag_payload

    pool_id = str(mem.id)
    candidates = pool_candidates(mem, pool_id=pool_id)
    vector_keys: List[str] = []
    for hit in search_rag(query, pool_id, top_k=max(top_k, 5)):
        key = f"{pool_id}:rag:{hit.get('file_id', '')}:{hit.get('chunk_idx', 0)}"
        candidates.append(Candidate(key=key, layer="rag", text=hit.get("text", ""),
                                    payload=_rag_payload(hit)))
        vector_keys.append(key)
    ranked = rank_candidates(query, candidates, vector_keys=vector_keys)
    return [{**cand.payload, "layer": cand.layer, "score": round(float(score), 6),
             "pool_id": pool_id, "pool": mem.name}
            for cand, score in ranked[:top_k]]


async def search_knowledge(ctx: Ctx, args: Dict[str, Any]) -> Dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("query is required")
    workspace = read_workspace(ctx, args.get("workspace"))
    top_k = _int_arg(args, "top_k", 8, 1, 50)
    pools = _pools(ctx, workspace)
    wanted = str(args.get("pool") or "").strip()
    if wanted:
        pools = [_pick_pool(pools, wanted)]
    if not pools:
        return {"workspace": workspace, "results": [],
                "note": "this workspace has no memory pools you can read"}

    def run() -> List[Dict[str, Any]]:
        hits: List[Dict[str, Any]] = []
        for mem in pools:
            try:
                hits.extend(_search_pool(mem, query, top_k))
            except Exception:  # noqa: BLE001 - one broken pool must not blank the rest
                log.warning("mcp search_knowledge: pool %s failed", mem.id, exc_info=True)
        hits.sort(key=lambda h: h.get("score") or 0.0, reverse=True)
        return hits[:top_k]

    results = await asyncio.to_thread(run)
    out: Dict[str, Any] = {"workspace": workspace, "results": results}
    if not results:
        out["note"] = "nothing in the memory pools matched the query"
    return out


# ── Workflows ────────────────────────────────────────────────────────────────

def _flows_in(workspace: str) -> List[Dict[str, Any]]:
    from flow import store as flow_store
    from workspace import get_workspace_metadata
    allowed = get_workspace_metadata(workspace).get("allowed_flows")
    out = []
    for flow in flow_store.list_flows():
        if (flow.get("workspace") or workspace) != workspace:
            continue
        if allowed is not None and flow.get("id") not in allowed:
            continue
        out.append(flow)
    return out


async def list_workflows(ctx: Ctx, args: Dict[str, Any]) -> Dict[str, Any]:
    from loops import store as loops_store
    from teams import store as teams_store
    workspace = read_workspace(ctx, args.get("workspace"))
    kind = str(args.get("kind") or "").strip()
    if kind and kind not in WORKFLOW_KINDS:
        raise ToolError(f"kind is one of {', '.join(WORKFLOW_KINDS)}")
    out: Dict[str, Any] = {"workspace": workspace}
    if kind in ("", "team"):
        out["teams"] = [{"id": t.team_id, "name": t.name, "description": t.description,
                         "mode": t.mode, "members": len(t.members)}
                        for t in teams_store.list_teams(workspace)]
    if kind in ("", "flow"):
        out["flows"] = [{"id": f.get("id"), "name": f.get("name"),
                         "description": f.get("description") or ""}
                        for f in _flows_in(workspace)]
    if kind in ("", "loop"):
        out["loops"] = [{"id": lp.loop_id, "name": lp.name, "description": lp.description,
                         "flow_id": lp.flow_id}
                        for lp in loops_store.list_loops(workspace)]
    return out


def _launch(kind: str, entity_id: str, text: str, workspace: str) -> str:
    """Start one workflow run through its own launcher; its run id."""
    if kind == "team":
        from teams import store as teams_store
        from teams.launcher import start_team_run
        team = teams_store.get_team(entity_id)
        if not team or (team.workspace or workspace) != workspace:
            raise ToolError(f"no team '{entity_id}' in workspace '{workspace}'")
        goal = text or (team.description or "").strip()
        if not goal:
            raise ToolError("a team run needs input: the request to work on")
        return start_team_run(entity_id, goal, workspace=workspace).team_run_id
    if kind == "loop":
        from loops import store as loops_store
        from loops.launcher import start_loop_run
        loop = loops_store.get_loop(entity_id)
        if not loop or (loop.workspace or workspace) != workspace:
            raise ToolError(f"no loop '{entity_id}' in workspace '{workspace}'")
        return start_loop_run(entity_id, text, workspace=workspace).loop_run_id
    from flow import launcher as flow_launcher
    if entity_id not in {f.get("id") for f in _flows_in(workspace)}:
        raise ToolError(f"no flow '{entity_id}' runnable in workspace '{workspace}'")
    return flow_launcher.trigger_flow(entity_id, workspace=workspace, description=text or None,
                                      created_by="mcp")["run_id"]


async def run_workflow(ctx: Ctx, args: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(args.get("kind") or "").strip()
    entity_id = str(args.get("id") or "").strip()
    if kind not in WORKFLOW_KINDS:
        raise ToolError(f"kind is one of {', '.join(WORKFLOW_KINDS)}")
    if not entity_id:
        raise ToolError("id is required; call list_workflows to see the ids")
    workspace = write_workspace(ctx, args.get("workspace"))
    check_budget(ctx.principal)
    wait = float(_int_arg(args, "wait_seconds", DEFAULT_WORKFLOW_WAIT, 0, MAX_WORKFLOW_WAIT))
    try:
        run_id = await asyncio.to_thread(_launch, kind, entity_id,
                                         str(args.get("input") or "").strip(), workspace)
    except ToolError:
        raise
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    _audit(ctx, "workflow.run", object_type=kind, object_id=entity_id, workspace=workspace,
           details={"run_id": run_id})
    deadline = time.monotonic() + wait
    while True:
        result = entity_run_summary(run_id)
        if result is None or result.get("finished") or time.monotonic() >= deadline:
            break
        await asyncio.sleep(_POLL_SECONDS)
    result = result or {"run_id": run_id, "kind": kind, "status": "pending"}
    if not result.get("finished"):
        result["note"] = (f"the {kind} is still working after {int(wait)}s; "
                          "call get_run with this run_id")
    return result


# ── Runs ─────────────────────────────────────────────────────────────────────

def _entity_answer(rec: Dict[str, Any]) -> str:
    kind = rec.get("kind")
    if kind in ("team", "loop"):
        return str(rec.get("result") or "")
    if kind == "flow" and rec.get("task_id"):
        from uuid import UUID
        from tasks import service as tasks_service
        try:
            return str(tasks_service.get_task_result(UUID(str(rec["task_id"]))) or "")
        except (ValueError, TypeError):
            return ""
    return ""


def entity_run_summary(run_id: str) -> Optional[Dict[str, Any]]:
    """A flow, loop, team or scenario run as a tool reports it, with its
    answer once it finished; None when there is no such entity run."""
    from common import entity_runs, run_status
    rec = entity_runs.get(run_id)
    if not rec:
        return None
    status = run_status.normalize(rec.get("status"))
    out: Dict[str, Any] = {
        "run_id": run_id, "kind": rec.get("kind"), "id": rec.get("entity_id"),
        "title": rec.get("title"), "status": status, "workspace": rec.get("workspace") or "default",
        "started_at": rec.get("started_at"), "finished_at": rec.get("finished_at"),
        "total_cost": rec.get("total_cost"), "finished": run_status.is_terminal(status),
    }
    if out["finished"]:
        out["answer"] = _entity_answer(rec)
        if rec.get("error"):
            out["error"] = str(rec["error"])
        if rec.get("stop_reason"):
            out["stop_reason"] = rec["stop_reason"]
    return out


def _agent_run_summary(run_id: str, run: Dict[str, Any]) -> Dict[str, Any]:
    from managers.run_manager import get_run_process
    status = str(run.get("status") or "")
    out: Dict[str, Any] = {"run_id": run_id, "kind": "agent", "status": status,
                           "agent_id": run.get("agent_id"),
                           "workspace": str(run.get("workspace") or "") or "default",
                           "started_at": run.get("started_at"),
                           "finished_at": run.get("finished_at")}
    if status in ("completed", "failed", "stopped", "stop"):
        ctx_rec = (get_run_process(run_id) or {}).get("llm_input_context") or {}
        out["answer"] = str(ctx_rec.get("response") or "")
        if run.get("error"):
            out["error"] = str(run.get("error"))
    return out


def _find_run(ctx: Ctx, run_id: str) -> Dict[str, Any]:
    """The run, agent or entity, if its workspace is reachable; the same
    answer for "no such run" and "not yours", so ids cannot be probed."""
    from managers.run_manager import get_run_by_id
    if not run_id:
        raise ToolError("run_id is required")
    run = get_run_by_id(run_id)
    summary = _agent_run_summary(run_id, run) if run else entity_run_summary(run_id)
    if summary is None or summary["workspace"] not in workspace_names(ctx.principal):
        raise ToolError(f"no run '{run_id}'")
    return summary


async def get_run(ctx: Ctx, args: Dict[str, Any]) -> Dict[str, Any]:
    return await asyncio.to_thread(_find_run, ctx, str(args.get("run_id") or "").strip())


async def stop_run(ctx: Ctx, args: Dict[str, Any]) -> Dict[str, Any]:
    run_id = str(args.get("run_id") or "").strip()
    summary = await asyncio.to_thread(_find_run, ctx, run_id)
    require_editor(ctx, summary["workspace"])
    if summary["kind"] == "agent":
        from managers.run_manager import stop_run_by_id
        stopped = await asyncio.to_thread(stop_run_by_id, run_id)
    else:
        from managers.runs.groups import stop_group
        stopped = await asyncio.to_thread(stop_group, summary["kind"], run_id)
    if not stopped:
        raise ToolError(f"run '{run_id}' is not running (status: {summary['status']})")
    _audit(ctx, "run.stop", object_type="run", object_id=run_id, workspace=summary["workspace"],
           details={"kind": summary["kind"]})
    return {"run_id": run_id, "kind": summary["kind"], "stopped": True}


def _list_runs(ctx: Ctx, workspace: str, kind: str, status: str, limit: int,
               mine: bool) -> List[Dict[str, Any]]:
    from common import entity_runs, run_status
    from managers import run_manager
    user_id = str(getattr(ctx.principal, "id", "") or "")
    fetch = limit * 4 if mine else limit

    def own(rec: Dict[str, Any]) -> bool:
        return not mine or str(rec.get("launched_by") or "") == user_id

    rows: List[Dict[str, Any]] = []
    if kind in ("", "agent"):
        page = run_manager.query_runs(workspace=workspace, status=status or None, limit=fetch)
        for run in page.get("items", []):
            if own(run):
                rows.append({"run_id": run.get("run_id"), "kind": "agent",
                             "agent_id": run.get("agent_id"), "status": run.get("status"),
                             "origin": run.get("message_origin"),
                             "started_at": run.get("started_at"),
                             "finished_at": run.get("finished_at")})
    if kind != "agent":
        kinds = [kind] if kind else list(entity_runs.KINDS)
        for rec in entity_runs.list_runs(kinds=kinds, workspace=workspace,
                                         statuses=[status] if status else None, limit=fetch):
            if own(rec):
                rows.append({"run_id": rec.get("run_id"), "kind": rec.get("kind"),
                             "id": rec.get("entity_id"), "title": rec.get("title"),
                             "status": run_status.normalize(rec.get("status")),
                             "started_at": rec.get("started_at"),
                             "finished_at": rec.get("finished_at")})
    rows.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
    return rows[:limit]


async def list_runs(ctx: Ctx, args: Dict[str, Any]) -> Dict[str, Any]:
    from common import entity_runs
    workspace = read_workspace(ctx, args.get("workspace"))
    kind = str(args.get("kind") or "").strip()
    if kind and kind != "agent" and kind not in entity_runs.KINDS:
        raise ToolError(f"kind is one of agent, {', '.join(entity_runs.KINDS)}")
    limit = _int_arg(args, "limit", 20, 1, 200)
    mine = args.get("mine") is not False
    runs = await asyncio.to_thread(_list_runs, ctx, workspace, kind,
                                   str(args.get("status") or "").strip(), limit, mine)
    return {"workspace": workspace, "mine": mine, "runs": runs}


# ── Definitions ──────────────────────────────────────────────────────────────

WORKSPACE_ARG = {"type": "string",
                 "description": "Workspace to use. Defaults to the X-Agents-Hub-Workspace "
                                "header, the API key's only workspace, or default."}

_READ = {"readOnlyHint": True, "openWorldHint": False}

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "list_files",
        "title": "List files",
        "description": "Files of a workspace, newest first: what people uploaded and what "
                       "agents wrote. Narrow by a name fragment with query.",
        "inputSchema": {"type": "object", "properties": {
            "workspace": WORKSPACE_ARG,
            "query": {"type": "string", "description": "Part of a file name, or a file id."},
            "source": {"type": "string",
                       "description": "Only files from one source: upload, agent, chat, "
                                      "memory, task."},
            "limit": {"type": "number", "description": "At most this many (default 50)."},
        }, "additionalProperties": False},
        "annotations": _READ,
    },
    {
        "name": "read_file",
        "title": "Read a file",
        "description": "The text of a workspace file (text, code, markdown, JSON, CSV, PDF), "
                       "by file_id or by its path in the workspace folder. A long file comes "
                       "in pages: pass next_offset back as offset.",
        "inputSchema": {"type": "object", "properties": {
            "file_id": {"type": "string", "description": "An id from list_files."},
            "path": {"type": "string",
                     "description": "Path in the workspace folder, e.g. reports/q3.md."},
            "workspace": WORKSPACE_ARG,
            "offset": {"type": "number", "description": "Character to start from (default 0)."},
            "max_chars": {"type": "number",
                          "description": f"At most this many characters (default "
                                         f"{DEFAULT_READ_CHARS}, at most {MAX_READ_CHARS})."},
        }, "additionalProperties": False},
        "annotations": _READ,
    },
    {
        "name": "upload_file",
        "title": "Upload a file",
        "description": "Put a file into a workspace so its agents can use it; returns its "
                       "file_id, which ask_agent takes in file_ids. With path, the file is "
                       "written into the workspace folder and replaces a file already there.",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "File name, e.g. notes.md."},
            "content": {"type": "string", "description": "The content, as text."},
            "content_base64": {"type": "string",
                               "description": "The content as base64, for a binary file."},
            "path": {"type": "string",
                     "description": "Optional path in the workspace folder, e.g. docs/spec.md."},
            "mime_type": {"type": "string"},
            "workspace": WORKSPACE_ARG,
        }, "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True,
                        "openWorldHint": False},
    },
    {
        "name": "list_knowledge",
        "title": "List knowledge",
        "description": "The memory pools of a workspace: what each one is about and how many "
                       "documents and notes it holds.",
        "inputSchema": {"type": "object", "properties": {"workspace": WORKSPACE_ARG},
                        "additionalProperties": False},
        "annotations": _READ,
    },
    {
        "name": "search_knowledge",
        "title": "Search knowledge",
        "description": "Search a workspace's knowledge: the documents indexed in its memory "
                       "pools and the notes and facts kept there. Returns the best passages "
                       "with their source, no model call.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What to look for."},
            "workspace": WORKSPACE_ARG,
            "pool": {"type": "string",
                     "description": "Only this memory pool, by name or id from list_knowledge."},
            "top_k": {"type": "number", "description": "How many passages (default 8)."},
        }, "required": ["query"], "additionalProperties": False},
        "annotations": _READ,
    },
    {
        "name": "list_workflows",
        "title": "List workflows",
        "description": "The teams, flows and loops of a workspace that run_workflow can start. "
                       "A team is agents working on one request together, a flow is a fixed "
                       "graph of steps, a loop repeats a flow until a goal is met.",
        "inputSchema": {"type": "object", "properties": {
            "workspace": WORKSPACE_ARG,
            "kind": {"type": "string", "enum": list(WORKFLOW_KINDS)},
        }, "additionalProperties": False},
        "annotations": _READ,
    },
    {
        "name": "run_workflow",
        "title": "Run a workflow",
        "description": "Start a team, flow or loop on the hub with an input and wait for its "
                       "result. A run that takes longer than wait_seconds returns its run_id; "
                       "call get_run with it later, or stop_run to stop it.",
        "inputSchema": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": list(WORKFLOW_KINDS)},
            "id": {"type": "string", "description": "An id from list_workflows."},
            "input": {"type": "string",
                      "description": "The request: a team's goal, a loop's goal, a flow's "
                                     "task description."},
            "workspace": WORKSPACE_ARG,
            "wait_seconds": {"type": "number",
                             "description": f"How long to wait for the result (default "
                                            f"{DEFAULT_WORKFLOW_WAIT}, 0 to return at once)."},
        }, "required": ["kind", "id"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True},
    },
    {
        "name": "list_runs",
        "title": "List runs",
        "description": "Recent runs in a workspace, newest first: agent turns and team, flow, "
                       "loop and scenario runs. Your own runs unless mine is false.",
        "inputSchema": {"type": "object", "properties": {
            "workspace": WORKSPACE_ARG,
            "kind": {"type": "string", "enum": ["agent", "team", "flow", "loop", "scenario"]},
            "status": {"type": "string",
                       "description": "Only runs with this status, e.g. running or failed."},
            "mine": {"type": "boolean",
                     "description": "Only runs you started (default true)."},
            "limit": {"type": "number", "description": "At most this many (default 20)."},
        }, "additionalProperties": False},
        "annotations": _READ,
    },
    {
        "name": "get_run",
        "title": "Get a run",
        "description": "The status of a run (an agent turn, or a team, flow or loop run) and "
                       "its answer once it finished. Use it with the run_id ask_agent or "
                       "run_workflow returned when the work was still going.",
        "inputSchema": {"type": "object",
                        "properties": {"run_id": {"type": "string"}},
                        "required": ["run_id"], "additionalProperties": False},
        "annotations": _READ,
    },
    {
        "name": "stop_run",
        "title": "Stop a run",
        "description": "Stop a running agent turn, team, flow or loop, with everything it "
                       "started. What it was producing is lost.",
        "inputSchema": {"type": "object",
                        "properties": {"run_id": {"type": "string"}},
                        "required": ["run_id"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
    },
]

HANDLERS: Dict[str, ToolFn] = {
    "list_files": list_files,
    "read_file": read_file,
    "upload_file": upload_file,
    "list_knowledge": list_knowledge,
    "search_knowledge": search_knowledge,
    "list_workflows": list_workflows,
    "run_workflow": run_workflow,
    "list_runs": list_runs,
    "get_run": get_run,
    "stop_run": stop_run,
}

__all__ = ["ToolError", "Ctx", "TOOLS", "HANDLERS", "workspace_names", "check_budget"]
