"""
External Nodes API – token-authenticated endpoint for exposed nodes.

Allows external callers (outside the service) to send a task prompt to an
exposed node using only its access token.  No dashboard auth required.
"""
import time
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from agents import node_manager

router = APIRouter(prefix="/api/external", tags=["external"])


class ExternalRunRequest(BaseModel):
    prompt: str
    workspace: Optional[str] = None


@router.post("/{token}/run")
async def external_run(token: str, body: ExternalRunRequest, request: Request):
    """
    Submit a task prompt to an exposed node identified by its access token.

    The request is logged to the node's connection history regardless of
    whether the node is currently running.
    """
    client_ip = request.client.host if request.client else "unknown"
    timestamp = datetime.now(timezone.utc).isoformat()
    connection_id = str(uuid4())
    t_start = time.time()

    node = node_manager.get_node_by_token(token)

    # Build a base connection record (status filled in below)
    def _log(status_code: int, detail: str):
        elapsed_ms = int((time.time() - t_start) * 1000)
        node_manager.log_connection(
            node["node_id"] if node else "__unknown__",
            {
                "id": connection_id,
                "timestamp": timestamp,
                "client_ip": client_ip,
                "method": "POST",
                "path": f"/api/external/{token[:8]}…/run",
                "prompt_preview": body.prompt[:120],
                "workspace": body.workspace,
                "response_status": status_code,
                "response_detail": detail,
                "elapsed_ms": elapsed_ms,
            },
        )

    if not node:
        # Still try to log even without a node – use a placeholder bucket
        elapsed_ms = int((time.time() - t_start) * 1000)
        # We can't call log_connection with __unknown__ meaningfully; just raise
        raise HTTPException(status_code=404, detail="No exposed node found for this token")

    node_id = node["node_id"]

    if not node.get("is_exposed"):
        _log(403, "node_not_exposed")
        raise HTTPException(status_code=403, detail="Node is not exposed")

    if node.get("status") not in ("running", "starting"):
        _log(503, f"node_not_running:{node.get('status')}")
        raise HTTPException(
            status_code=503,
            detail=f"Node is not running (status={node.get('status')})",
        )

    # Create a task for the node's agent and workspace
    try:
        from common import tasks_service
        workspace = body.workspace or node.get("workspace")
        task = tasks_service.create_task(
            title=body.prompt[:120],
            description=body.prompt,
            workspace=workspace,
        )
        task_id = str(task.id)
        _log(202, f"task_created:{task_id}")
        return {
            "accepted": True,
            "task_id": task_id,
            "node_id": node_id,
            "agent_id": node.get("agent_id"),
        }
    except Exception as e:
        _log(500, f"error:{str(e)[:120]}")
        raise HTTPException(status_code=500, detail=str(e))
