"""
Nodes API – manage long-running agent nodes (Kubernetes pod-style).

A node is a persistent subprocess running an agent in service/worker mode,
decoupled from any specific task.
"""
from fastapi import APIRouter, HTTPException, Request
from typing import Optional
from pathlib import Path
from pydantic import BaseModel

from agents import node_manager, registry

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


class NodeCreate(BaseModel):
    agent_id: str
    workspace: Optional[str] = None
    label: Optional[str] = None
    node_type: Optional[str] = None


def _enrich(node: dict) -> dict:
    """Add agent display name and domain to a node record."""
    agent_id = node.get("agent_id", "")
    spec = registry.get_agent(agent_id)
    running_sessions = node_manager.get_running_sessions_for_node(node.get("node_id", ""))
    return {
        **node,
        "agent_name": spec.name if spec else agent_id,
        "agent_domain": getattr(spec, "domain", "") if spec else "",
        "running_sessions_count": len(running_sessions),
        "running_sessions": [
            {
                "run_id": r.get("run_id"),
                "task_id": r.get("task_id"),
                "agent_id": r.get("agent_id"),
                "status": r.get("status"),
                "started_at": r.get("started_at"),
            }
            for r in running_sessions
        ],
    }


@router.get("")
async def list_nodes(workspace: Optional[str] = None):
    """List all nodes sorted newest-first, optionally filtered by workspace."""
    nodes = node_manager.list_nodes()
    if workspace:
        nodes = [n for n in nodes if n.get("workspace") == workspace]
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
            node_type=data.node_type,
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


@router.post("/{node_id}/restart")
async def restart_node(node_id: str):
    """Restart a Docker node container in place (docker restart).

    Only available for nodes with execution_mode='docker'.  The container keeps
    its ID and name — no new image build or record is created.
    """
    node = node_manager.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    if node.get("execution_mode") != "docker":
        raise HTTPException(status_code=400, detail="In-place restart is only supported for Docker nodes")
    ok = node_manager.restart_node(node_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to restart container")
    return _enrich(node_manager.get_node(node_id))


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


@router.post("/{node_id}/expose")
async def expose_node(node_id: str, request: Request):
    """Enable external access for a node, generating a unique access token."""
    node = node_manager.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    updated = node_manager.expose_node(node_id)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to expose node")
    base_url = str(request.base_url).rstrip("/")
    token = updated.get("expose_token", "")
    return {
        **_enrich(updated),
        "external_url": f"{base_url}/api/external/{token}/run",
    }


@router.delete("/{node_id}/expose")
async def unexpose_node(node_id: str):
    """Disable external access for a node."""
    node = node_manager.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    ok = node_manager.unexpose_node(node_id)
    return {"unexposed": ok}


@router.get("/{node_id}/runs")
async def get_node_runs(node_id: str, limit: int = 50):
    """Return all runs for a node, newest first."""
    node = node_manager.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    from agents.run_manager import get_all_runs_for_node
    runs = get_all_runs_for_node(node_id, limit=limit)
    return [
        {
            "run_id": r.get("run_id"),
            "agent_id": r.get("agent_id"),
            "task_id": r.get("task_id"),
            "session_type": r.get("session_type"),
            "status": r.get("status"),
            "title": r.get("title"),
            "output": r.get("output"),
            "error": r.get("error"),
            "started_at": r.get("started_at"),
            "finished_at": r.get("finished_at"),
        }
        for r in runs
    ]


@router.get("/{node_id}/connections")
async def get_node_connections(node_id: str):
    """Return connection history for an exposed node."""
    node = node_manager.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return node_manager.get_connections(node_id)
