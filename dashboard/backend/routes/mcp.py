"""Attaching external MCP servers to a workspace.

    GET    /api/mcp/servers              what is attached, and how each is doing
    POST   /api/mcp/servers              attach one
    PATCH  /api/mcp/servers/{id}         edit it
    DELETE /api/mcp/servers/{id}         detach it
    POST   /api/mcp/servers/{id}/test    connect now, and say what is there
    GET    /api/mcp/servers/{id}/tools   the tools, from cache unless asked
    GET    /api/mcp/tools                every MCP tool here, as catalog entries

Two things shape this router.

**Nothing secret goes out.** Every response runs through
``mcp_client.store.masked``, so a stored header or env value that looks like a
credential leaves as its last four characters. The PATCH route is the other half
of that: a value that comes back still masked is taken to mean "unchanged", so
saving a form nobody edited cannot replace a token with dots.

**Connecting is slow and is never implicit.** Listing servers reports a tool
count only for servers already in the cache; asking for a count that is not
there would turn a page refresh into a fan-out of process starts. ``test`` and
``tools?refresh=true`` are the two places a connection actually happens, and both
run on a worker thread so the backend's event loop is not blocked by somebody
else's handshake.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from mcp_client import store as mcp_store

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class CapabilityClaim(BaseModel):
    """What the operator says this server's tools can do. See tools/capabilities.py."""
    ingests_untrusted: bool = False
    reads_private: bool = False
    can_exfiltrate: bool = False


class CreateServer(BaseModel):
    id: str
    name: Optional[str] = None
    description: str = ""
    transport: str = "stdio"
    command: str = ""
    args: List[str] = []
    url: str = ""
    headers: Dict[str, str] = {}
    env: Dict[str, str] = {}
    enabled: bool = True
    capabilities: CapabilityClaim = CapabilityClaim()
    # "none", "all", or the tools that need a human yes.
    approval: Any = "none"
    tool_allowlist: List[str] = []


class UpdateServer(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    transport: Optional[str] = None
    command: Optional[str] = None
    args: Optional[List[str]] = None
    url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    env: Optional[Dict[str, str]] = None
    enabled: Optional[bool] = None
    capabilities: Optional[CapabilityClaim] = None
    approval: Optional[Any] = None
    tool_allowlist: Optional[List[str]] = None


def _workspace(value: Optional[str]) -> str:
    """The workspace this request is about, or a 400 saying there is none.

    Required rather than defaulted to "the first one": the configuration lives
    inside a workspace record, and guessing which one would attach somebody's
    ticket server to the wrong team.
    """
    from common.workspace_context import resolve_active_workspace

    workspace = resolve_active_workspace(value)
    if not workspace:
        raise HTTPException(status_code=400, detail="A workspace is required")
    return workspace


def _found_or_404(workspace: str, server_id: str) -> Dict[str, Any]:
    record = mcp_store.get_server(workspace, server_id)
    if record is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return record


def _status(workspace: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """One server as the page shows it: masked, with its last known state.

    ``tool_count`` is ``None`` when nothing is cached, which the page renders as
    "not loaded yet" rather than as zero. A zero that actually means "we have
    not looked" is how an operator concludes a working server is broken.
    """
    from mcp_client.client import cached_tool_names

    cached = cached_tool_names(workspace, record["id"])
    return {
        **mcp_store.masked(record),
        "tool_count": len(cached) if cached is not None else None,
        "cached": cached is not None,
    }


@router.get("/servers")
async def list_servers(workspace: Optional[str] = None):
    """Every MCP server attached to this workspace, with its last known state."""
    ws = _workspace(workspace)
    records = mcp_store.list_servers(ws)
    return {
        "servers": [_status(ws, r) for r in records],
        "transports": list(mcp_store.TRANSPORTS),
        "workspace": ws,
    }


@router.post("/servers", status_code=201)
async def create_server(body: CreateServer, workspace: Optional[str] = None):
    """Attach a server. Its capability claim is part of attaching it."""
    ws = _workspace(workspace)
    try:
        record = mcp_store.create_server(ws, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"server": _status(ws, record)}


@router.patch("/servers/{server_id}")
async def update_server(server_id: str, body: UpdateServer, workspace: Optional[str] = None):
    """Edit a server. Masked secrets sent back unchanged keep their value.

    Any edit drops this server's cached tools: the configuration hash would
    catch a changed command or URL on its own, but an allowlist widened after
    the remote server gained a tool is a case the operator expects to see at
    once, not in five minutes.
    """
    from mcp_client.client import refresh

    ws = _workspace(workspace)
    _found_or_404(ws, server_id)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    record = mcp_store.update_server(ws, server_id, changes)
    if record is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    refresh(ws, server_id)
    return {"server": _status(ws, record)}


@router.delete("/servers/{server_id}")
async def delete_server(server_id: str, workspace: Optional[str] = None):
    """Detach a server. Agents still naming its tools simply get fewer tools."""
    from mcp_client.client import refresh

    ws = _workspace(workspace)
    _found_or_404(ws, server_id)
    mcp_store.delete_server(ws, server_id)
    refresh(ws, server_id)
    return {"deleted": True, "id": server_id}


@router.post("/servers/{server_id}/test")
async def test_server(server_id: str, workspace: Optional[str] = None):
    """Connect now and report the tools, or the error, without storing tools.

    The answer an operator needs while writing an allowlist, so it lists every
    tool the server offers and marks which the current allowlist would keep.
    """
    from mcp_client.client import discover, refresh

    ws = _workspace(workspace)
    record = _found_or_404(ws, server_id)
    try:
        tools = await asyncio.to_thread(discover, record)
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        mcp_store.record_status(ws, server_id, error=message)
        return {"ok": False, "error": message, "tools": []}
    mcp_store.record_status(ws, server_id, error="", tool_names=[t["name"] for t in tools])
    # A successful probe may well have been the fix for whatever was wrong, so
    # the next agent build should re-load rather than reuse a stale failure.
    refresh(ws, server_id)
    return {"ok": True, "tools": tools, "count": len(tools)}


@router.get("/tools")
async def workspace_tools(workspace: Optional[str] = None):
    """Every MCP tool in this workspace, in the shape ``/api/tools`` uses.

    The agent editor's second list. Served from here rather than merged into
    ``/api/tools`` because the catalog there is static and fully classified in
    ``tools/capabilities.py``, while these are defined on somebody else's server
    and differ per workspace. Each entry still carries the grants its server
    declared, so the editor evaluates a mixed tool set with one rule.
    """
    from tools.registry import list_mcp_tool_specs

    ws = _workspace(workspace)
    specs = await asyncio.to_thread(list_mcp_tool_specs, ws)
    return {"tools": [spec.to_dict() for spec in specs], "workspace": ws}


@router.get("/servers/{server_id}/tools")
async def server_tools(server_id: str, workspace: Optional[str] = None, refresh: bool = False):
    """The tools an agent would get from this server, under their hub ids.

    Served from the cache when it is warm. ``refresh=true`` drops the entry
    first, which is the button next to a server that has just been fixed.
    """
    from mcp_client.client import refresh as drop_cache, tools_for

    ws = _workspace(workspace)
    record = _found_or_404(ws, server_id)
    if refresh:
        drop_cache(ws, server_id)
    tools = await asyncio.to_thread(tools_for, ws, server_id)
    current = mcp_store.get_server(ws, server_id) or record
    return {
        "tools": [
            {
                "id": getattr(t, "name", ""),
                "description": (getattr(t, "description", "") or "").strip(),
            }
            for t in tools
        ],
        "count": len(tools),
        "last_error": current.get("last_error") or "",
        "last_seen": current.get("last_seen"),
        # The grants every tool from this server carries, so the page can show
        # the same three flags the agent editor shows per tool.
        "capabilities": sorted(mcp_store.server_capabilities(ws, server_id)),
    }
