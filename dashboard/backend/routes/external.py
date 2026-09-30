"""
External API: the public address of a published instance.

A resident instance (instances/carrier.py) published from its page gets a
token, and so does a service (services/store.py, docs/services.md); anyone
holding it can talk to the instance, or to whichever replica of the service
the routing picks, through the hub, without a dashboard account:

- ``POST /api/external/{token}/messages`` puts a message into the instance's
  mailbox, the same one the instance page writes to, in the caller's own
  conversation (``conversation_id``, the main one when left out). The call
  answers in one of three ways:

  - by default it waits up to ``wait_seconds`` (60, at most 300) and returns
    the answer (200), or where the message stands (202, ``queued`` or
    ``running``) with a ``poll_url``;
  - ``"stream": true`` returns Server-Sent Events: the run's live events
    (``token``, ``tool_start``, ``tool_end``, ``thinking``, ``done``) and a
    closing ``reply`` event with the answer;
  - ``"wait_seconds": 0`` returns 202 at once.

  A stopped instance is started again to answer; the backend never runs the
  agent itself.
- ``GET /api/external/{token}/messages/{msg_id}`` reads where a message
  stands, for a caller that did not wait.
- ``POST /api/external/{token}/run`` is the older address: it creates a task
  for the instance's agent and returns 202 with the task id, no answer.

Every call is throttled per client address (``AGENTS_HUB_EXTERNAL_RATE_PER_MINUTE``,
common/rate_limit.py; known and unknown tokens share the window, so guessing
tokens is as slow as flooding a real one), checked against the instance's
inbound secret when it has one (signed like notify.outbound signs what this
hub sends), and logged to the instance's connection history.
"""
import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from common import identity, rate_limit
from instances import carrier
from notify.inbound import seen_delivery, verify_signature

router = APIRouter(prefix="/api/external", tags=["external"])

DEFAULT_WAIT_SECONDS = 60.0
MAX_WAIT_SECONDS = 300.0
STREAM_MAX_SECONDS = 900.0


class ExternalRunRequest(BaseModel):
    prompt: str
    workspace: Optional[str] = None


class ExternalMessageRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    stream: bool = False
    wait_seconds: float = Field(DEFAULT_WAIT_SECONDS, ge=0, le=MAX_WAIT_SECONDS)
    # A published runner service answers for the agent named here
    # (docs/services.md, "Talking to a service").
    agent_id: Optional[str] = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Call:
    """One public call: its connection-log record and the checks every
    address shares."""

    def __init__(self, request: Request, token: str, path: str, preview: str,
                 workspace: Optional[str] = None):
        self.request = request
        self.token = token
        self.path = path
        self.preview = preview
        self.workspace = workspace
        self.client_ip = identity.client_ip(request) or "unknown"
        self.timestamp = _utc_now()
        self.started = time.time()
        self.instance: Optional[Dict[str, Any]] = None
        # A service's token instead of an instance's: the call is logged under
        # the service and answered by one of its replicas.
        self.service: Optional[Dict[str, Any]] = None

    @property
    def target(self) -> Optional[Dict[str, Any]]:
        return self.service or self.instance

    def log(self, status_code: int, detail: str) -> None:
        carrier.log_connection(
            (self.service["service_id"] if self.service
             else self.instance["instance_id"] if self.instance else "__unknown__"),
            {
                "id": str(uuid4()),
                "timestamp": self.timestamp,
                "client_ip": self.client_ip,
                "method": self.request.method,
                "path": self.path,
                "prompt_preview": self.preview[:120],
                "workspace": self.workspace,
                "response_status": status_code,
                "response_detail": detail,
                "elapsed_ms": int((time.time() - self.started) * 1000),
            },
        )

    async def authorize(self) -> Tuple[Optional[JSONResponse], Dict[str, Any]]:
        """Throttle, find the instance, verify the signature and publication.

        Returns ``(response, instance)``: a ready 429 response when throttled,
        otherwise None and the instance. Raises HTTPException for the rest.
        """
        from services import store as service_store

        within, retry_after = rate_limit.check_external(self.client_ip)
        self.service = service_store.get_by_token(self.token)
        self.instance = None if self.service else carrier.get_by_token(self.token)
        if not within:
            # Logged only against a real instance: an unknown token writes
            # nothing, so guessing costs the hub no more than the lookup.
            if self.target:
                self.log(429, f"rate_limited:retry_after={retry_after}")
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests from this address", "retry_after": retry_after},
                headers={"Retry-After": str(retry_after)}), {}
        if not self.target:
            raise HTTPException(status_code=404, detail="No published instance found for this token")
        secret = self.target.get("inbound_secret")
        if secret:
            raw_body = await self.request.body()
            signature = self.request.headers.get("X-AgentsHub-Signature", "")
            timestamp = self.request.headers.get("X-AgentsHub-Timestamp", "")
            delivery_id = self.request.headers.get("X-AgentsHub-Delivery", "")
            if not verify_signature(secret, raw_body, signature, timestamp):
                self.log(401, "bad_signature")
                raise HTTPException(status_code=401, detail="Invalid or missing signature")
            if delivery_id and seen_delivery(delivery_id):
                self.log(409, "replayed_delivery")
                raise HTTPException(status_code=409, detail="Delivery already processed")
        if not self.target.get("is_exposed"):
            self.log(403, "instance_not_published")
            raise HTTPException(status_code=403, detail="Instance is not published")
        return None, self.target


def _poll_url(request: Request, token: str, msg_id: str) -> str:
    return f"{str(request.base_url).rstrip('/')}/api/external/{token}/messages/{msg_id}"


@router.post("/{token}/messages")
async def external_message(token: str, body: ExternalMessageRequest, request: Request):
    """Send a message to a published instance and get its answer."""
    from instances import delivery, inbox, replies

    call = _Call(request, token, f"/api/external/{token[:8]}…/messages", body.message)
    throttled, target = await call.authorize()
    if throttled is not None:
        return throttled
    text = (body.message or "").strip()
    if not text:
        call.log(400, "empty_message")
        raise HTTPException(status_code=400, detail="message is empty")

    if call.service is not None:
        # A service: one of its replicas answers, with the conversation's
        # history across replicas. Chosen before subscribing so no event of
        # the answering run is missed, then written into its mailbox.
        from services import replicas as service_replicas
        from services import routing as service_routing
        if not target.get("agent_id"):
            try:
                service_routing.agent_for_runner(body.agent_id, target.get("workspace"))
            except ValueError as exc:
                call.log(400, "runner_needs_agent")
                raise HTTPException(status_code=400, detail=str(exc))
        try:
            instance = await asyncio.to_thread(
                service_replicas.pick, target, conversation_id=body.conversation_id)
        except service_replicas.ServiceUnavailable as exc:
            call.log(503, "service_unavailable")
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 - a replica that cannot start
            call.log(503, f"replica_start_failed:{str(exc)[:80]}")
            raise HTTPException(status_code=503, detail=f"could not start a replica: {exc}")
    else:
        instance = target

    instance_id = instance["instance_id"]
    channel = delivery.channel_for(instance_id)
    stream_client = None
    if body.stream:
        # Subscribe before the message exists, so no event of its run is missed.
        from common.session_broker import broker
        stream_client = broker.open_client([channel])

    if call.service is not None:
        _replica, msg_id = await asyncio.to_thread(
            service_routing.dispatch_message, target, text, conversation_id=body.conversation_id,
            origin="external", replica=instance, agent_id=body.agent_id)
    else:
        msg_id = inbox.enqueue(instance_id, text, origin="external",
                               conversation_id=body.conversation_id)
        await asyncio.to_thread(delivery.wake_resident, instance)
    conversation = inbox.public_conversation(inbox.normalize_conversation(body.conversation_id))

    if stream_client is not None:
        call.log(200, f"stream:{msg_id}")
        return StreamingResponse(
            _stream_reply(stream_client, msg_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    reply = await replies.wait_async(msg_id, body.wait_seconds)
    reply = reply or {"msg_id": msg_id, "status": "queued"}
    reply.setdefault("conversation_id", conversation)
    if replies.is_final(reply):
        call.log(200, f"{reply.get('status')}:{msg_id}")
        return reply
    call.log(202, f"{reply.get('status')}:{msg_id}")
    return JSONResponse(status_code=202, content={
        **reply, "poll_url": _poll_url(request, token, msg_id)})


async def _stream_reply(stream_client, msg_id: str):
    """The live events of the run answering ``msg_id``, then its ``reply``."""
    from common.session_broker import broker
    from instances import replies

    client_id, queue = stream_client
    run_id: Optional[str] = None
    deadline = time.monotonic() + STREAM_MAX_SECONDS
    last_check = 0.0

    def frame(event: Dict[str, Any]) -> str:
        return f"data: {json.dumps(event, default=str)}\n\n"

    try:
        yield frame({"type": "accepted", "msg_id": msg_id})
        while time.monotonic() < deadline:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                event = None
            if event is not None:
                if event.get("type") == "meta" and event.get("msg_id") == msg_id:
                    run_id = event.get("run_id") or run_id
                ev_run = event.get("run_id")
                if run_id and ev_run == run_id and event.get("type") not in ("heartbeat",):
                    out = {k: v for k, v in event.items() if k not in ("channel", "id", "session_id")}
                    yield frame(out)
                if event.get("type") == "instance_stream_end" and event.get("msg_id") == msg_id:
                    break
            now = time.monotonic()
            if now - last_check >= 1.0:
                last_check = now
                reply = await asyncio.to_thread(replies.reply_for, msg_id)
                if reply is not None:
                    run_id = run_id or reply.get("run_id")
                    if replies.is_final(reply):
                        break
                if event is None:
                    yield ": keep-alive\n\n"
        reply = await asyncio.to_thread(replies.reply_for, msg_id)
        yield frame({"type": "reply", **(reply or {"msg_id": msg_id, "status": "queued"})})
    finally:
        broker.close_client(client_id)


@router.get("/{token}/messages/{msg_id}")
async def external_message_status(token: str, msg_id: str, request: Request):
    """Where one message stands: queued, running, or its answer."""
    from instances import replies

    call = _Call(request, token, f"/api/external/{token[:8]}…/messages/{msg_id}", "")
    throttled, target = await call.authorize()
    if throttled is not None:
        return throttled
    reply = await asyncio.to_thread(replies.reply_for, msg_id)
    if reply is None or not _reply_belongs(reply, call):
        raise HTTPException(status_code=404, detail="Message not found")
    return reply


def _reply_belongs(reply: Dict[str, Any], call: _Call) -> bool:
    """The message is the token holder's: of the instance, or of a replica of
    the service."""
    instance_id = str(reply.get("instance_id") or "")
    if call.service is not None:
        from instances import store as instance_store
        inst = instance_store.get(instance_id)
        return bool(inst) and str(inst.get("service_id") or "") == str(call.service["service_id"])
    return bool(call.instance) and instance_id == str(call.instance["instance_id"])


@router.post("/{token}/run", status_code=202)
async def external_run(token: str, body: ExternalRunRequest, request: Request):
    """Create a task for a published instance's agent (the older address).

    The task is assigned to the instance's agent, so an instance of it that
    takes tasks picks it up (in the workspace's ``node`` execution mode;
    otherwise the task is launched like any assigned task). The orchestrator
    agent gets the task as a ready external task to route. Returns 202 with
    the task id and no answer; ``/messages`` is the address that answers.
    """
    call = _Call(request, token, f"/api/external/{token[:8]}…/run", body.prompt,
                 workspace=body.workspace)
    throttled, instance = await call.authorize()
    if throttled is not None:
        return throttled
    if call.service is not None:
        if instance.get("status") != "active":
            call.log(503, "service_paused")
            raise HTTPException(status_code=503, detail="The service is paused")
        if not instance.get("agent_id"):
            call.log(400, "runner_has_no_agent")
            raise HTTPException(status_code=400, detail="A runner service has no agent of its own")
        instance_id = instance["service_id"]
    else:
        if instance.get("state") not in ("starting", "standby", "active"):
            call.log(503, f"instance_not_running:{instance.get('state')}")
            raise HTTPException(status_code=503,
                                detail=f"Instance is not running (state={instance.get('state')})")
        instance_id = instance["instance_id"]
    agent_id = instance.get("agent_id", "")
    try:
        from tasks import service as tasks_service
        from tasks import TaskStatus, CreatedBy

        # A task always names its workspace: one with none is in no
        # workspace's queue, so an instance taking tasks would never see it.
        workspace = body.workspace or instance.get("workspace") or "default"
        task = tasks_service.create_task(
            title=body.prompt[:120],
            description=body.prompt,
            workspace=workspace,
            status=TaskStatus.ready,
            created_by=CreatedBy.external,
        )
        task_id = str(task.id)
        if agent_id != "orchestrator":
            run_id = str(uuid4())
            from managers.run_manager import _upsert_run
            _upsert_run({
                "run_id": run_id,
                "task_id": task_id,
                "agent_id": agent_id,
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
        call.log(202, f"task_created:{task_id}")
        return {
            "accepted": True,
            "task_id": task_id,
            "instance_id": instance_id,
            "agent_id": agent_id,
        }
    except Exception as e:
        call.log(500, f"error:{str(e)[:120]}")
        raise HTTPException(status_code=500, detail=str(e))
