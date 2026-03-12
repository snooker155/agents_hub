"""
Nodes API – manage long-running agent nodes (Kubernetes pod-style).

A node is a persistent subprocess running an agent in service/worker mode,
decoupled from any specific task.
"""
from fastapi import APIRouter, HTTPException
from typing import Optional
from pathlib import Path
from pydantic import BaseModel

from agents import node_manager, registry

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


class NodeCreate(BaseModel):
    agent_id: str
    workspace: Optional[str] = None
    label: Optional[str] = None


def _enrich(node: dict) -> dict:
    """Add agent display name and domain to a node record."""
    agent_id = node.get("agent_id", "")
    spec = registry.get_agent(agent_id)
    return {
        **node,
        "agent_name": spec.name if spec else agent_id,
        "agent_domain": getattr(spec, "domain", "") if spec else "",
    }


@router.get("")
async def list_nodes():
    """List all nodes sorted newest-first."""
    nodes = node_manager.list_nodes()
    enriched = [_enrich(n) for n in nodes]
    enriched.sort(key=lambda n: n.get("started_at") or "", reverse=True)
    return enriched


@router.post("")
async def start_node(data: NodeCreate):
    """Start a new node for the given agent."""
    spec = registry.get_agent(data.agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Agent '{data.agent_id}' not found")
    if getattr(spec, "is_remote", False):
        raise HTTPException(status_code=400, detail="Cannot start a node for a remote agent")
    try:
        node_id = node_manager.start_node(
            data.agent_id,
            workspace=data.workspace,
            label=data.label,
        )
        node = node_manager.get_node(node_id)
        return _enrich(node)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{node_id}")
async def get_node(node_id: str):
    """Get a single node's details."""
    node = node_manager.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return _enrich(node)


@router.get("/{node_id}/logs")
async def get_node_logs(node_id: str):
    """Return the node's stdout/stderr log."""
    node = node_manager.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    log_file = node.get("log_file")
    if not log_file or not Path(log_file).exists():
        return {"logs": "(no logs yet)"}
    try:
        return {"logs": Path(log_file).read_text(encoding="utf-8", errors="replace")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{node_id}/stop")
async def stop_node(node_id: str):
    """Send SIGTERM to a running node."""
    node = node_manager.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    stopped = node_manager.stop_node(node_id)
    return {"stopped": stopped}


@router.delete("/{node_id}")
async def delete_node(node_id: str):
    """Remove a stopped/failed node record."""
    deleted = node_manager.delete_node(node_id)
    if not deleted:
        raise HTTPException(
            status_code=400,
            detail="Node not found or is still running (stop it first)",
        )
    return {"deleted": True}
