"""The hub link and the nodes (docs/hub-link.md).

Three routers, three callers:

* ``/api/hublink``: the operator's view of this hub's link to another
  (status, link, unlink, sync), and the receiving end of the mirror
  (``/mirror/chats``, ``/mirror/memory``), which a linked hub calls with
  its personal key.
* ``/api/nodes``: the nodes attached to this hub. Anyone signed in may
  create one (it is their own machine); an administrator sees them all.
* ``/api/node-link``: what a node and the hub's own model drivers call.
  Listed in ``common.auth.SELF_AUTHENTICATING_PREFIXES``, so each handler
  checks its own credential: the node token on ``hello``, ``poll`` and the
  job answers; the node's proxy key on ``/<id>/v1/...``; a hub credential
  (a run token, the service token, a key) on ``/<id>/exec``; and the
  loop's per-process secret on ``/self/exec``, the one route that runs
  something on *this* machine.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from common import identity
from hublink import jobs, nodes, store
from hublink import link as hub_link
from hublink import receive

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hublink", tags=["hub-link"])
nodes_router = APIRouter(prefix="/api/nodes", tags=["nodes"])
node_link_router = APIRouter(prefix="/api/node-link", tags=["node-link"])

MAX_POLL_WAIT = 30.0
PROXY_TIMEOUT = 600.0
#: Request bodies a node job carries (a chat completion with a long history
#: is tens of KB; an image never travels this way).
MAX_JOB_BODY = 4 * 1024 * 1024


# ── /api/hublink ─────────────────────────────────────────────────────────────

class NodeSettings(BaseModel):
    enabled: Optional[bool] = None
    token: Optional[str] = None
    share_models: Optional[bool] = None
    sandbox: Optional[bool] = None
    shared_providers: Optional[List[str]] = None


class LinkBody(BaseModel):
    url: str
    api_key: str = ""
    name: Optional[str] = None
    use_models: bool = True
    use_agents: bool = True
    mirror: Optional[Dict[str, bool]] = None
    target_workspace: str = ""
    node: Optional[NodeSettings] = None


def _admin(request: Request) -> None:
    identity.require_role(identity.request_principal(request), admin=True)


@router.get("")
async def get_link(request: Request):
    _admin(request)
    return hub_link.status()


@router.put("")
async def put_link(request: Request, body: LinkBody):
    _admin(request)
    try:
        doc = await asyncio.to_thread(
            hub_link.link, url=body.url, api_key=body.api_key, name=body.name,
            use_models=body.use_models, use_agents=body.use_agents, mirror=body.mirror,
            target_workspace=body.target_workspace,
            node=body.node.model_dump(exclude_none=True) if body.node else None)
    except hub_link.LinkError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    return {"link": store.public(doc), **hub_link.status()}


@router.delete("")
async def delete_link(request: Request):
    _admin(request)
    removed = await asyncio.to_thread(hub_link.unlink)
    return {"removed": removed}


@router.post("/sync")
async def sync_link(request: Request):
    _admin(request)
    try:
        return await asyncio.to_thread(hub_link.sync)
    except hub_link.LinkError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


class MirrorChats(BaseModel):
    node: str = "node"
    workspace: Optional[str] = None
    chats: List[Dict[str, Any]] = Field(default_factory=list)


class MirrorMemory(BaseModel):
    node: str = "node"
    workspace: Optional[str] = None
    pools: List[Dict[str, Any]] = Field(default_factory=list)


@router.put("/mirror/chats")
async def mirror_chats(request: Request, body: MirrorChats):
    principal = identity.request_principal(request)
    try:
        return await asyncio.to_thread(receive.store_chats, principal, node=body.node,
                                       workspace=body.workspace, chats=body.chats)
    except receive.ReceiveError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


@router.put("/mirror/memory")
async def mirror_memory(request: Request, body: MirrorMemory):
    principal = identity.request_principal(request)
    try:
        return await asyncio.to_thread(receive.store_memory, principal, node=body.node,
                                       workspace=body.workspace, pools=body.pools)
    except receive.ReceiveError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


# ── /api/nodes ───────────────────────────────────────────────────────────────

class CreateNode(BaseModel):
    name: str
    workspaces: Optional[List[str]] = None


def _own_or_admin(request: Request, record: Dict[str, Any]) -> Any:
    principal = identity.request_principal(request)
    from common import access
    if not access.owner_or_admin(principal, record.get("owner")):
        raise HTTPException(status_code=403, detail="This node belongs to another account")
    return principal


@nodes_router.get("")
async def list_nodes(request: Request):
    principal = identity.request_principal(request)
    owner = None
    if identity.current_mode() == "multi" and principal is not None and not principal.is_admin:
        owner = principal.id
    return {"nodes": [nodes.public(n) for n in nodes.list_nodes(owner=owner)]}


@nodes_router.post("", status_code=201)
async def create_node(request: Request, body: CreateNode):
    principal = identity.request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    from common import access
    for ws in body.workspaces or []:
        if not access.can_see_workspace(principal, ws):
            raise HTTPException(status_code=403, detail=f"workspace '{ws}' is not visible to this account")
    try:
        token, record = nodes.create_node(owner=principal.id, name=body.name, workspaces=body.workspaces)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"node": nodes.public(record), "token": token}


@nodes_router.get("/{node_id}")
async def get_node(request: Request, node_id: str):
    record = nodes.get_node(node_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Node not found")
    _own_or_admin(request, record)
    return nodes.public(record)


@nodes_router.post("/{node_id}/rotate")
async def rotate_node(request: Request, node_id: str):
    record = nodes.get_node(node_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Node not found")
    _own_or_admin(request, record)
    token = nodes.rotate_token(node_id)
    return {"node": nodes.public(nodes.get_node(node_id) or record), "token": token}


@nodes_router.delete("/{node_id}")
async def delete_node(request: Request, node_id: str):
    record = nodes.get_node(node_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Node not found")
    _own_or_admin(request, record)
    return {"removed": nodes.delete_node(node_id)}


# ── /api/node-link: the node's own calls ─────────────────────────────────────

def _bearer(request: Request) -> str:
    from common.auth import extract_bearer
    return extract_bearer(request.headers.get("authorization")) or ""


def _node_of(request: Request) -> Dict[str, Any]:
    record = nodes.resolve_token(_bearer(request))
    if record is None:
        raise HTTPException(status_code=401, detail="a valid node token is required")
    return record


class HelloBody(BaseModel):
    name: str = ""
    host: str = ""
    version: str = ""
    models: List[Any] = Field(default_factory=list)
    capabilities: Dict[str, Any] = Field(default_factory=dict)


@node_link_router.post("/hello")
async def node_hello(request: Request, body: HelloBody):
    record = _node_of(request)
    updated = await asyncio.to_thread(nodes.hello, record, body.model_dump())
    return {"node_id": updated["id"], "name": updated.get("name"),
            "backend_id": nodes.backend_id(updated) if updated.get("models") else None,
            "sandbox_provider": f"node:{updated['id']}" if (updated.get("capabilities") or {}).get("sandbox") else None}


@node_link_router.get("/poll")
async def node_poll(request: Request, wait: float = 20.0):
    record = _node_of(request)
    node_id = record["id"]
    await asyncio.to_thread(nodes.touch, node_id)
    deadline = time.monotonic() + max(0.0, min(float(wait), MAX_POLL_WAIT))
    while True:
        taken = await asyncio.to_thread(jobs.take, node_id)
        if taken or time.monotonic() >= deadline:
            return {"jobs": taken}
        if await request.is_disconnected():
            return {"jobs": []}
        await asyncio.sleep(0.25)


def _job_of(request: Request, job_id: str) -> str:
    record = _node_of(request)
    owner = jobs.owner_of(job_id)
    if owner is None or owner != record["id"]:
        raise HTTPException(status_code=404, detail="no such job for this node")
    return job_id


class HeadBody(BaseModel):
    status: int = 200
    headers: Dict[str, str] = Field(default_factory=dict)


class ChunkBody(BaseModel):
    data: str


class EndBody(BaseModel):
    error: Optional[str] = None


@node_link_router.post("/jobs/{job_id}/head")
async def job_head(request: Request, job_id: str, body: HeadBody):
    _job_of(request, job_id)
    ok = await asyncio.to_thread(jobs.set_head, job_id, status=body.status, headers=body.headers)
    return {"ok": ok}


@node_link_router.post("/jobs/{job_id}/chunk")
async def job_chunk(request: Request, job_id: str, body: ChunkBody):
    _job_of(request, job_id)
    seq = await asyncio.to_thread(jobs.append_chunk, job_id, body.data)
    return {"seq": seq}


@node_link_router.post("/jobs/{job_id}/end")
async def job_end(request: Request, job_id: str, body: EndBody):
    _job_of(request, job_id)
    await asyncio.to_thread(jobs.finish, job_id, error=body.error)
    return {"ok": True}


# ── /api/node-link/self/exec: a snippet on this machine, for the hub above ──

@node_link_router.post("/self/exec")
async def self_exec(request: Request):
    from hublink import node_client
    if request.headers.get(node_client.SELF_HEADER, "") != node_client.self_secret():
        raise HTTPException(status_code=403, detail="only this hub's node loop may call this")
    body = await request.json()
    return await asyncio.to_thread(_run_locally, body if isinstance(body, dict) else {})


def _run_locally(body: Dict[str, Any]) -> Dict[str, Any]:
    from common.config import settings
    from sandbox import registry
    from sandbox.base import NetworkType, SandboxNetwork, SandboxRequest, SandboxResult
    net = body.get("network") or {}
    net_type = str(net.get("type") or "none")
    if net_type not in ("none", "limited", "unrestricted"):
        net_type = "none"
    network = SandboxNetwork(type=cast(NetworkType, net_type), hosts=list(net.get("hosts") or []))
    req = SandboxRequest(
        language=str(body.get("language") or ""), code=str(body.get("code") or ""),
        extra_files={str(k): str(v) for k, v in (body.get("extra_files") or {}).items()},
        timeout=int(body.get("timeout") or 60), stdin=body.get("stdin"),
        memory=body.get("memory"), cpus=body.get("cpus"), pids_limit=body.get("pids_limit"),
        network=network, workspace=None, environment_id=body.get("environment_id"),
        image=body.get("image"))
    try:
        name = registry.resolve(None, settings)
        provider = registry.get_provider(name)
    except Exception as exc:  # noqa: BLE001 - reported as a result, never raised at the remote
        return asdict(SandboxResult(exit_code=-1, provider="none", error=f"no sandbox provider here: {exc}"))
    if name == "local" and network.type != "unrestricted":
        return asdict(SandboxResult(exit_code=-1, provider=name, error=(
            "this node's sandbox is a plain process and cannot fence the network; "
            "the request asked for a fenced one")))
    ok, reason = provider.is_available()
    if not ok:
        return asdict(SandboxResult(exit_code=-1, provider="none",
                                    error=f"the node's {name} sandbox is not available ({reason})"))
    result = provider.run(req)
    result.provider = f"node/{result.provider or name}"
    return asdict(result)


# ── /api/node-link/<id>/...: the hub's own calls to a node ───────────────────

def _node_record(node_id: str) -> Dict[str, Any]:
    record = nodes.get_node(node_id)
    if record is None or record.get("disabled"):
        raise HTTPException(status_code=404, detail="Node not found")
    return record


async def _relay(node_id: str, *, method: str, path: str, headers: Dict[str, str],
                 body: bytes, timeout: float) -> StreamingResponse:
    if len(body) > MAX_JOB_BODY:
        raise HTTPException(status_code=413, detail="request body too large for a node")
    job_id = await asyncio.to_thread(jobs.submit, node_id, method=method, path=path,
                                     headers=headers, body=body, timeout=timeout)
    try:
        head = await jobs.wait_head(job_id, timeout=timeout)
    except jobs.JobFailed as exc:
        await asyncio.to_thread(jobs.discard, job_id)
        raise HTTPException(status_code=502, detail=f"node {node_id}: {exc}")
    hdrs = {k: v for k, v in (head.get("headers") or {}).items()
            if k.lower() in ("content-type", "cache-control")}

    async def stream():
        try:
            async for piece in jobs.iter_chunks(job_id, timeout=timeout):
                yield piece
        finally:
            await asyncio.to_thread(jobs.discard, job_id)

    return StreamingResponse(stream(), status_code=int(head.get("status") or 200), headers=hdrs,
                             media_type=hdrs.get("content-type") or hdrs.get("Content-Type"))


@node_link_router.get("/{node_id}/v1/{path:path}", operation_id="node_proxy_get")
@node_link_router.post("/{node_id}/v1/{path:path}", operation_id="node_proxy_post")
async def node_proxy(request: Request, node_id: str, path: str):
    """The node's ``/v1`` as this hub's model drivers see it: the custom
    backend ``node-<slug>`` points here with the node's proxy key."""
    record = _node_record(node_id)
    if not nodes.resolve_proxy_key(node_id, _bearer(request)):
        raise HTTPException(status_code=401, detail="a valid node proxy key is required")
    body = await request.body()
    query = f"?{request.url.query}" if request.url.query else ""
    headers = {k: v for k, v in request.headers.items()
               if k.lower() in ("content-type", "accept", "x-hub-source")}
    return await _relay(record["id"], method=request.method, path=f"/v1/{path}{query}",
                        headers=headers, body=body, timeout=PROXY_TIMEOUT)


def _caller_workspace(request: Request, principal: Any, claimed: str) -> str:
    """The workspace the exec is for: the run's own for a run token (looked
    up, never trusted from the body), what the body says for anyone else."""
    if principal is not None and getattr(principal, "kind", "") == "run":
        username = str(getattr(principal, "username", "") or "")
        run_id = username[len("run:"):] if username.startswith("run:") else ""
        if run_id:
            from managers.run_manager import get_run_by_id
            rec = get_run_by_id(run_id)
            if rec and rec.get("workspace"):
                return str(rec["workspace"])
            try:
                from common import entity_runs
                erec = entity_runs.get(run_id)
                if erec and erec.get("workspace"):
                    return str(erec["workspace"])
            except Exception:  # noqa: BLE001 - no entity run of that id
                log.debug("node exec: no entity run %s", run_id, exc_info=True)
    return (claimed or "").strip() or "default"


@node_link_router.post("/{node_id}/exec")
async def node_exec(request: Request, node_id: str):
    """Run a snippet on the node, for ``sandbox/node.py``."""
    record = _node_record(node_id)
    principal = identity.current_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not (record.get("capabilities") or {}).get("sandbox"):
        raise HTTPException(status_code=409, detail="this node offers no sandbox")
    body = await request.json()
    body = body if isinstance(body, dict) else {}
    workspace = _caller_workspace(request, principal, str(body.pop("workspace_name", "") or ""))
    if not nodes.allows_workspace(record, workspace):
        raise HTTPException(status_code=403, detail=f"node '{record.get('name')}' is not allowed for workspace '{workspace}'")
    if getattr(principal, "kind", "") == "user":
        from common import access
        if not access.can_see_workspace(principal, workspace):
            raise HTTPException(status_code=403, detail=f"workspace '{workspace}' is not visible to this account")
    import json
    payload = json.dumps(body, default=str).encode("utf-8")
    timeout = float(body.get("timeout") or 60) + 30.0
    response = await _relay(record["id"], method="POST", path="/api/node-link/self/exec",
                            headers={"Content-Type": "application/json"}, body=payload, timeout=timeout)
    chunks = []
    async for piece in response.body_iterator:
        chunks.append(piece if isinstance(piece, bytes) else str(piece).encode("utf-8"))
    raw = b"".join(chunks)
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        data = {"exit_code": -1, "error": "the node answered with something that is not a result"}
    if response.status_code != 200:
        detail = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else data.get("detail") or data.get("error")
        raise HTTPException(status_code=502, detail=f"node refused: {detail or response.status_code}")
    return JSONResponse(data)


__all__ = ["node_link_router", "nodes_router", "router"]
