"""
External Nodes API – token-authenticated endpoint for exposed nodes.

Allows external callers (outside the service) to send a task prompt to an
exposed node using only its access token.  No dashboard auth required.
"""
import time
from datetime import datetime, timezone
from uuid import uuid4, UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from managers import node_manager
from notify.inbound import seen_delivery, verify_signature

router = APIRouter(prefix="/api/external", tags=["external"])


class ExternalRunRequest(BaseModel):
    prompt: str
    workspace: Optional[str] = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post("/{token}/run", status_code=202)
async def external_run(token: str, body: ExternalRunRequest, request: Request):
    """
    Submit a task prompt to an exposed node identified by its access token.

    For worker nodes the task is assigned directly to the node's agent so the
    worker picks it up on its next poll cycle.  For orchestrator nodes the task
    is queued as a ready external task and the orchestrator routes it normally.

    The request is logged to the node's connection history regardless of
    whether the node is currently running.
    """
    client_ip = request.client.host if request.client else "unknown"
    timestamp = _utc_now()
    connection_id = str(uuid4())
    t_start = time.time()

    node = node_manager.get_node_by_token(token)

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
        raise HTTPException(status_code=404, detail="No exposed node found for this token")

    # When the node has an inbound secret configured, the caller must sign the
    # request the same way notify.outbound signs what this hub sends out.
    # Nodes with no secret configured keep today's behaviour: the token alone
    # is enough, unchanged.
    secret = node.get("inbound_secret")
    if secret:
        raw_body = await request.body()
        signature = request.headers.get("X-AgentsHub-Signature", "")
        timestamp = request.headers.get("X-AgentsHub-Timestamp", "")
        delivery_id = request.headers.get("X-AgentsHub-Delivery", "")
        if not verify_signature(secret, raw_body, signature, timestamp):
            _log(401, "bad_signature")
            raise HTTPException(status_code=401, detail="Invalid or missing signature")
        if delivery_id and seen_delivery(delivery_id):
            _log(409, "replayed_delivery")
            raise HTTPException(status_code=409, detail="Delivery already processed")

    node_id = node["node_id"]
    agent_id = node.get("agent_id", "")

    if not node.get("is_exposed"):
        _log(403, "node_not_exposed")
        raise HTTPException(status_code=403, detail="Node is not exposed")

    if node.get("status") not in ("running", "starting"):
        _log(503, f"node_not_running:{node.get('status')}")
        raise HTTPException(
            status_code=503,
            detail=f"Node is not running (status={node.get('status')})",
        )

    try:
        from tasks import service as tasks_service
        from tasks import TaskStatus, CreatedBy

        workspace = body.workspace or node.get("workspace")

        # Create the task marked as externally submitted and immediately ready
        task = tasks_service.create_task(
            title=body.prompt[:120],
            description=body.prompt,
            workspace=workspace,
            status=TaskStatus.ready,
            created_by=CreatedBy.external,
        )
        task_id = str(task.id)

        # For worker nodes (non-orchestrator): assign directly so the node's
        # worker loop can pick it up without waiting for the orchestrator.
        if agent_id != "orchestrator":
            run_id = str(uuid4())
            from managers.run_manager import _upsert_run
            _upsert_run({
                "run_id": run_id,
                "task_id": task_id,
                "agent_id": agent_id,
                "node_id": node_id,
                "channel": "external",
                "pid": None,
                "status": "assigned",
                "session_type": "task",
                "session_id": None,
                "started_at": None,
                "finished_at": None,
                "exit_code": None,
                "error": None,
                "log_file": None,
                "input": body.prompt,
            })
            tasks_service.assign_agent(UUID(task_id), agent_type=agent_id, run_id=run_id)

        _log(202, f"task_created:{task_id}")
        return {
            "accepted": True,
            "task_id": task_id,
            "node_id": node_id,
            "agent_id": agent_id,
        }
    except Exception as e:
        _log(500, f"error:{str(e)[:120]}")
        raise HTTPException(status_code=500, detail=str(e))
