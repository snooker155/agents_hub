"""The hub link and the nodes (docs/hub-link.md).

Three routers, three callers:

* ``/api/hublink``: the operator's view of this hub's link to another
  (status, link, unlink, sync), and the receiving end of the mirror
  (``/mirror/chats``, ``/mirror/memory``), which a linked hub calls with
  its personal key.
* ``/api/nodes``: the nodes attached to this hub. Anyone signed in may
  create one (it is their own machine); an administrator sees them all and
  alone sets a node's trust level (common/data_class.py). ``/api/nodes/pool``
  is the pool of them (hublink/scheduler.py).
* ``/api/node-link``: what a node and the hub's own model drivers call.
  Listed in ``common.auth.SELF_AUTHENTICATING_PREFIXES``, so each handler
  checks its own credential: the node token on ``hello``, ``poll`` and the
  job answers; the node's proxy key on ``/<id>/v1/...``; the pool's key on
  ``/pool/v1/...``; a hub credential (a run token, the service token, a
  key) on ``/<id>/exec`` and ``/pool/exec``; and the
  loop's per-process secret on ``/self/exec``, the one route that runs
  something on *this* machine.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from common import data_class, identity
from hublink import jobs, nodes, scheduler, store
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
    capacity: Optional[int] = None


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


class UpdateNode(BaseModel):
    trust: Optional[str] = None
    max_parallel: Optional[int] = None
    workspaces: Optional[List[str]] = None
    disabled: Optional[bool] = None


def _is_admin(principal: Any) -> bool:
    return identity.current_mode() != "multi" or bool(principal is not None and principal.is_admin)


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
    load = await asyncio.to_thread(jobs.in_flight)
    return {"nodes": [nodes.public(n, in_flight=load.get(n["id"], 0)) for n in nodes.list_nodes(owner=owner)],
            "can_set_trust": _is_admin(principal)}


@nodes_router.get("/pool")
async def node_pool(request: Request, workspace: str = "", agent: str = ""):
    """The pool: its models, capacity and load, and for ``workspace`` (and
    an agent in it) which nodes may take that work and why the rest may not."""
    principal = identity.request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    need = None
    if workspace:
        from common import access
        if not access.can_see_workspace(principal, workspace):
            raise HTTPException(status_code=403, detail=f"workspace '{workspace}' is not visible to this account")
        need = await asyncio.to_thread(scheduler.need_for, workspace=workspace, agent=agent or None)
    return await asyncio.to_thread(scheduler.summary, need)


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
        token, record = nodes.create_node(
            owner=principal.id, name=body.name, workspaces=body.workspaces,
            trust_level=data_class.TRUSTED if _is_admin(principal) else data_class.UNTRUSTED)
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


@nodes_router.patch("/{node_id}")
async def update_node(request: Request, node_id: str, body: UpdateNode):
    """The hub's side of a node: its trust (administrators only, and never
    ``attested`` by hand), a cap on its capacity, its workspaces, and the
    switch that takes it out of service."""
    record = nodes.get_node(node_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Node not found")
    principal = _own_or_admin(request, record)
    changes: Dict[str, Any] = {}
    fields = body.model_fields_set
    if "trust" in fields:
        if not _is_admin(principal):
            raise HTTPException(status_code=403, detail="only an administrator sets a node's trust")
        level = data_class.normalize_trust(body.trust)
        if level == data_class.ATTESTED:
            raise HTTPException(status_code=400, detail=(
                "attested comes from a remote attestation check, which this hub does not run yet; "
                "it cannot be set by hand"))
        if level not in data_class.SETTABLE_TRUST:
            raise HTTPException(status_code=400, detail="trust must be untrusted or trusted")
        changes["trust"] = level
    if "max_parallel" in fields:
        if body.max_parallel is not None and not 1 <= int(body.max_parallel) <= nodes.MAX_CAPACITY:
            raise HTTPException(status_code=400, detail=f"max_parallel must be from 1 to {nodes.MAX_CAPACITY}")
        changes["max_parallel"] = int(body.max_parallel) if body.max_parallel else None
    if "workspaces" in fields:
        from common import access
        cleaned = [str(w).strip() for w in body.workspaces or [] if str(w).strip()]
        for ws in cleaned:
            if not access.can_see_workspace(principal, ws):
                raise HTTPException(status_code=403, detail=f"workspace '{ws}' is not visible to this account")
        changes["workspaces"] = cleaned or None
    if "disabled" in fields:
        changes["disabled"] = bool(body.disabled)
    updated = await asyncio.to_thread(nodes.update_node, node_id, **changes) if changes else record
    if "disabled" in changes:
        await asyncio.to_thread(nodes.sync_pool)
    try:
        from common import audit
        audit.record("node.update", principal=principal, object_type="node", object_id=node_id,
                     ip=identity.client_ip(request), details=changes)
    except Exception:  # noqa: BLE001 - an audit failure must not undo the change
        log.warning("could not record the node update audit entry for %s", node_id, exc_info=True)
    load = await asyncio.to_thread(jobs.in_flight)
    return nodes.public(updated or record, in_flight=load.get(node_id, 0))


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
    capacity: Optional[int] = None


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
    capacity = nodes.capacity(record)
    while True:
        # Never more at once than the node runs: what it cannot take stays
        # waiting, where the pool may still send it to another node.
        room = capacity - await asyncio.to_thread(jobs.running_count, node_id)
        taken = await asyncio.to_thread(jobs.take, node_id, limit=min(8, room)) if room > 0 else []
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


# ── /api/node-link/<id>/... and /pool/...: the hub's own calls to nodes ────

#: The header a relayed answer names its node in (the pool picks one).
NODE_HEADER = "X-Hub-Node"
#: What a model call may ask for through a node or the pool.
MODEL_PATHS = ("chat/completions", "completions", "embeddings", "responses")


def _node_record(node_id: str) -> Dict[str, Any]:
    record = nodes.get_node(node_id)
    if record is None or record.get("disabled"):
        raise HTTPException(status_code=404, detail="Node not found")
    return record


def _stream(job_id: str, head: Dict[str, Any], *, timeout: float,
            record: Optional[Dict[str, Any]] = None) -> StreamingResponse:
    hdrs = {k: v for k, v in (head.get("headers") or {}).items()
            if k.lower() in ("content-type", "cache-control")}
    if record is not None:
        hdrs[NODE_HEADER] = str(record.get("name") or record.get("id"))

    async def stream():
        try:
            async for piece in jobs.iter_chunks(job_id, timeout=timeout):
                yield piece
        finally:
            await asyncio.to_thread(jobs.discard, job_id)

    return StreamingResponse(stream(), status_code=int(head.get("status") or 200), headers=hdrs,
                             media_type=hdrs.get("content-type") or hdrs.get("Content-Type"))


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
    return _stream(job_id, head, timeout=timeout)


async def _dispatch(need: scheduler.Need, *, method: str, path: str, headers: Dict[str, str],
                    body: bytes, timeout: float) -> StreamingResponse:
    if len(body) > MAX_JOB_BODY:
        raise HTTPException(status_code=413, detail="request body too large for a node")
    try:
        record, job_id, head = await scheduler.dispatch(need, method=method, path=path, headers=headers,
                                                        body=body, timeout=timeout)
    except scheduler.NoNode as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    except jobs.JobFailed as exc:
        raise HTTPException(status_code=502, detail=f"node pool: {exc}")
    return _stream(job_id, head, timeout=timeout, record=record)


def _model_caller(request: Request) -> scheduler.Need:
    """Whose work a model call is: the workspace and agent the model builder
    named (providers/adapters.py). A call that names none is the default
    workspace's, at that workspace's class."""
    return scheduler.need_for(workspace=request.headers.get(data_class.WORKSPACE_HEADER) or "default",
                              agent=request.headers.get(data_class.AGENT_HEADER) or None)


def _forward_headers(request: Request) -> Dict[str, str]:
    return {k: v for k, v in request.headers.items()
            if k.lower() in ("content-type", "accept", "x-hub-source")}


@node_link_router.get("/pool/v1/models", operation_id="node_pool_models")
async def pool_models(request: Request):
    """Every model the pool serves, answered here from the node records: no
    node is asked, so the list is there while every node sleeps."""
    if not nodes.resolve_pool_key(_bearer(request)):
        raise HTTPException(status_code=401, detail="a valid node pool key is required")
    models = await asyncio.to_thread(nodes.pool_models)
    online = [r for r in await asyncio.to_thread(nodes.list_nodes)
              if not r.get("disabled") and nodes.is_online(r)]
    return {"object": "list", "capacity": sum(nodes.capacity(r) for r in online), "data": [
        {"id": m["id"], "object": "model", "owned_by": nodes.POOL_BACKEND,
         "context_window": m.get("context_window") or None, "nodes": len(m["nodes"])} for m in models]}


@node_link_router.post("/pool/v1/{path:path}", operation_id="node_pool_post")
async def pool_proxy(request: Request, path: str):
    """A model call to the pool (the custom backend ``nodes``): sent to the
    least busy node that shares the model and may see the caller's work."""
    if not nodes.resolve_pool_key(_bearer(request)):
        raise HTTPException(status_code=401, detail="a valid node pool key is required")
    if path not in MODEL_PATHS:
        raise HTTPException(status_code=404, detail=f"/v1/{path} is not served by the node pool")
    body = await request.body()
    import json
    try:
        model = str((json.loads(body or b"{}") or {}).get("model") or "")
    except ValueError:
        raise HTTPException(status_code=400, detail="the request body is not JSON")
    if not model:
        raise HTTPException(status_code=400, detail="the request names no model")
    need = await asyncio.to_thread(_model_caller, request)
    need.model = model
    query = f"?{request.url.query}" if request.url.query else ""
    return await _dispatch(need, method="POST", path=f"/v1/{path}{query}", headers=_forward_headers(request),
                           body=body, timeout=PROXY_TIMEOUT)


def _trust_check(record: Dict[str, Any], need: scheduler.Need) -> None:
    trust = nodes.trust(record)
    if not data_class.trust_allows(trust, need.data_class):
        raise HTTPException(status_code=403, detail=data_class.refusal(
            str(record.get("name") or record["id"]), trust, need.data_class))


@node_link_router.get("/{node_id}/v1/{path:path}", operation_id="node_proxy_get")
@node_link_router.post("/{node_id}/v1/{path:path}", operation_id="node_proxy_post")
async def node_proxy(request: Request, node_id: str, path: str):
    """The node's ``/v1`` as this hub's model drivers see it: the custom
    backend ``node-<slug>`` points here with the node's proxy key. A model
    call is refused when the caller's work is above the node's trust."""
    record = _node_record(node_id)
    if not nodes.resolve_proxy_key(node_id, _bearer(request)):
        raise HTTPException(status_code=401, detail="a valid node proxy key is required")
    if request.method == "POST":
        _trust_check(record, await asyncio.to_thread(_model_caller, request))
    body = await request.body()
    query = f"?{request.url.query}" if request.url.query else ""
    return await _relay(record["id"], method=request.method, path=f"/v1/{path}{query}",
                        headers=_forward_headers(request), body=body, timeout=PROXY_TIMEOUT)


def _caller(request: Request, principal: Any, claimed: str) -> Tuple[str, Optional[str]]:
    """The workspace and agent the exec is for: the run's own for a run
    token (looked up, never trusted from the body), what the body says for
    anyone else."""
    if principal is not None and getattr(principal, "kind", "") == "run":
        username = str(getattr(principal, "username", "") or "")
        run_id = username[len("run:"):] if username.startswith("run:") else ""
        if run_id:
            from managers.run_manager import get_run_by_id
            rec = get_run_by_id(run_id)
            if rec and rec.get("workspace"):
                return str(rec["workspace"]), (str(rec.get("agent_id") or "") or None)
            try:
                from common import entity_runs
                erec = entity_runs.get(run_id)
                if erec and erec.get("workspace"):
                    return str(erec["workspace"]), None
            except Exception:  # noqa: BLE001 - no entity run of that id
                log.debug("node exec: no entity run %s", run_id, exc_info=True)
    return (claimed or "").strip() or "default", None


async def _exec_request(request: Request) -> Tuple[Dict[str, Any], scheduler.Need]:
    """The snippet and whose work it is, for both exec routes."""
    principal = identity.current_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    body = await request.json()
    body = body if isinstance(body, dict) else {}
    claimed_agent = str(body.pop("agent_id", "") or "") or None
    workspace, agent = _caller(request, principal, str(body.pop("workspace_name", "") or ""))
    # An agent only ever makes the class stricter, so the one the body names
    # is taken when the run's own is not known.
    agent = agent or claimed_agent
    if getattr(principal, "kind", "") == "user":
        from common import access
        if not access.can_see_workspace(principal, workspace):
            raise HTTPException(status_code=403, detail=f"workspace '{workspace}' is not visible to this account")
    need = await asyncio.to_thread(scheduler.need_for, workspace=workspace, agent=agent, sandbox=True)
    return body, need


async def _exec_result(response: StreamingResponse) -> JSONResponse:
    import json
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
    node = response.headers.get(NODE_HEADER)
    if node and isinstance(data, dict):
        data["node"] = node
    return JSONResponse(data)


def _exec_payload(body: Dict[str, Any]) -> Tuple[bytes, float]:
    import json
    return json.dumps(body, default=str).encode("utf-8"), float(body.get("timeout") or 60) + 30.0


@node_link_router.post("/pool/exec")
async def pool_exec(request: Request):
    """Run a snippet on the least busy node with a sandbox that may take the
    run's work, for ``sandbox/node.py``'s provider ``nodes``."""
    body, need = await _exec_request(request)
    payload, timeout = _exec_payload(body)
    response = await _dispatch(need, method="POST", path="/api/node-link/self/exec",
                               headers={"Content-Type": "application/json"}, body=payload, timeout=timeout)
    return await _exec_result(response)


@node_link_router.post("/{node_id}/exec")
async def node_exec(request: Request, node_id: str):
    """Run a snippet on the node, for ``sandbox/node.py``."""
    record = _node_record(node_id)
    body, need = await _exec_request(request)
    if not (record.get("capabilities") or {}).get("sandbox"):
        raise HTTPException(status_code=409, detail="this node offers no sandbox")
    if not nodes.allows_workspace(record, need.workspace):
        raise HTTPException(status_code=403, detail=f"node '{record.get('name')}' is not allowed for workspace '{need.workspace}'")
    _trust_check(record, need)
    payload, timeout = _exec_payload(body)
    response = await _relay(record["id"], method="POST", path="/api/node-link/self/exec",
                            headers={"Content-Type": "application/json"}, body=payload, timeout=timeout)
    return await _exec_result(response)


__all__ = ["node_link_router", "nodes_router", "router"]
