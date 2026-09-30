"""
Where a chat turn runs, and the relay that brings it back.

With ``AGENTS_HUB_CHAT_EXECUTION=instances`` (the default, common/config.py
chat_execution) the backend runs no agent for a chat turn. ``run_chat_pipeline``
and its flow and team siblings (chat/pipelines.py) hand the turn to
:func:`relay` instead: it chooses the service and the replica the turn
belongs to (services/routing.py), subscribes to the conversation's channel,
writes the turn into the replica's mailbox and yields what the replica posts
back (chat/turns.py) until the turn's ``chat_stream_end``. The caller sees
the same events it saw when the pipeline ran here: ``meta``, tokens, tool
calls, ``handoff``, ``done``.

Two cases always run in process, whatever the setting: the replica itself
(it is the process the turn was routed to) and a worker (it serves no chat).
``inprocess`` turns the routing off everywhere, the way things were before
services existed.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, AsyncIterator, Dict, Optional
from uuid import uuid4

log = logging.getLogger(__name__)

#: How long to wait, after the last event, for the ``chat_stream_end`` that
#: follows a turn's ``done``.
END_GRACE_SECONDS = 5.0
#: How often, while nothing arrives, the message's own state is checked.
STATE_CHECK_SECONDS = 2.0


def enabled() -> bool:
    """Whether this process hands chat turns to service replicas."""
    from instances.registry import ENV_INSTANCE_ID
    if os.environ.get(ENV_INSTANCE_ID):
        return False
    from common.config import chat_execution, hub_role
    if hub_role() == "worker":
        return False
    return chat_execution() == "instances"


def _timeouts() -> Dict[str, float]:
    from common.config import live_setting, settings
    chat = float(getattr(settings, "chat_request_timeout", 900) or 900)
    llm = float(getattr(settings, "llm_request_timeout", 600) or 600)
    try:
        start = float(live_setting("AGENTS_HUB_TURN_START_TIMEOUT", str(settings.turn_start_timeout)))
    except ValueError:
        start = float(settings.turn_start_timeout)
    return {"start": max(5.0, start), "idle": max(chat, llm + 60.0)}


def _failure(request: Any, error: str, *, status: Optional[int] = None,
             run_id: Optional[str] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": "done", "ok": False, "response": f"Error: {error}",
                           "error": error, "run_id": run_id,
                           "agent_id": getattr(request, "agent_id", None)}
    if status is not None:
        out["status"] = status
    return out


def _replica_gone(replica_id: str) -> Optional[str]:
    """A reason when the replica can no longer answer, else None."""
    from instances import carrier, store
    current = carrier.sync(store.get(replica_id))
    if current is None:
        return "the replica no longer exists"
    if current.get("state") not in store.LIVE_STATES:
        return (f"the replica stopped ({current.get('state')}): "
                f"{current.get('carrier_error') or current.get('error') or 'process exited'}")
    return None


async def relay(request: Any, kind: str = "agent") -> AsyncIterator[Dict[str, Any]]:
    """Run one turn on a service replica and yield its events."""
    from common import api_keys, identity
    from common.session_broker import broker
    from instances import inbox, replies
    from services import routing as service_routing
    from services.replicas import ServiceUnavailable

    if kind == "agent":
        from chat.runs import validate_chat_request
        validate_chat_request(request)  # a missing agent is a 404 before anything is written
    conv = request.conversation_id or str(uuid4())
    request.conversation_id = conv
    channel = f"chat:{conv}"
    agent_id = request.agent_id if kind == "agent" else None
    user_id = identity.current_user_id() or None
    key_id = api_keys.current_key_id()

    client_id, queue = broker.open_client([channel])
    msg_id: Optional[str] = None
    run_id: Optional[str] = None
    try:
        try:
            service, replica = await asyncio.to_thread(
                service_routing.choose, request.workspace, agent_id, conversation_id=conv)
        except ServiceUnavailable as exc:
            yield _failure(request, str(exc), status=503)
            return
        except Exception as exc:  # noqa: BLE001 - a replica that cannot start is the turn's failure
            log.warning("chat routing: no replica for %s/%s", request.workspace, agent_id, exc_info=True)
            yield _failure(request, f"could not start a replica: {exc}", status=503)
            return
        replica_id = str(replica["instance_id"])
        service, replica, msg_id = await asyncio.to_thread(
            service_routing.dispatch_turn, request, kind, user_id=user_id, key_id=key_id,
            service=service, replica=replica)
        log.debug("chat routing: turn %s -> %s (%s)", msg_id, replica_id, service.get("name"))

        timeouts = _timeouts()
        started = False
        got_done = False
        last_event = time.monotonic()
        last_check = last_event
        deadline = last_event + timeouts["start"]
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                event = None
            now = time.monotonic()
            if event is not None:
                if event.get("turn_msg_id") != msg_id:
                    continue
                last_event = now
                etype = event.get("type")
                if etype == "turn_start":
                    started = True
                    deadline = now + timeouts["idle"]
                    continue
                if etype == "chat_stream_end":
                    return
                started = True
                deadline = now + (END_GRACE_SECONDS if got_done else timeouts["idle"])
                if etype == "meta" and event.get("run_id"):
                    run_id = str(event["run_id"])
                elif etype == "handoff" and event.get("next_run_id"):
                    run_id = str(event["next_run_id"])
                if etype == "done":
                    got_done = True
                    deadline = now + END_GRACE_SECONDS
                yield {k: v for k, v in event.items()
                       if k not in ("channel", "id", "turn_msg_id", "instance_id", "msg_id")}
                continue
            if got_done and now >= deadline:
                return
            if now - last_check >= STATE_CHECK_SECONDS:
                last_check = now
                reply = await asyncio.to_thread(replies.reply_for, msg_id)
                if reply is not None:
                    run_id = run_id or reply.get("run_id")
                    if reply.get("status") == "failed" and not reply.get("run_id"):
                        yield _failure(request, str(reply.get("error") or "the turn failed"),
                                       run_id=run_id)
                        return
                gone = await asyncio.to_thread(_replica_gone, replica_id)
                if gone and not started:
                    await asyncio.to_thread(inbox.mark_error, msg_id, gone)
                    yield _failure(request, gone, status=503, run_id=run_id)
                    return
            if now >= deadline:
                if not started:
                    error = f"the replica did not pick the turn up within {int(timeouts['start'])}s"
                    await asyncio.to_thread(inbox.mark_error, msg_id, error)
                    yield _failure(request, error, status=504, run_id=run_id)
                    return
                if not got_done:
                    error = f"the agent did not answer within {int(timeouts['idle'])}s"
                    if run_id:
                        try:
                            from managers.run_manager import stop_run_by_id
                            await asyncio.to_thread(stop_run_by_id, run_id)
                        except Exception:  # noqa: BLE001 - the stop is a courtesy; the timeout stands
                            log.debug("could not stop run %s after the timeout", run_id, exc_info=True)
                    yield _failure(request, error, status=504, run_id=run_id)
                return
    finally:
        broker.close_client(client_id)


__all__ = ["enabled", "relay"]
