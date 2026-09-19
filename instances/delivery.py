"""
Delivering a message to an instance.

Three cases, one entry point:

- the copy is **alive with a loop of its own** (a node or a container) — the
  message goes into its mailbox and its poll loop answers it, in its process;
- the copy is **idle** (finished, stopped, or a carrier-less standby) — there is
  nothing to hand it to, so we revive it: rebuild its history from its journal
  and run one chat turn recorded against the same instance;
- the copy is **busy** — the message waits in the mailbox and is delivered when
  it goes idle (the watchdog drains it).

The revived turn is pumped into the ``instance:<id>`` broker channel, so the
instance page renders it live exactly like a chat.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Set

from common.session_broker import broker
from instances import inbox, store
from instances.history import build_instance_history

# Instances that run their own loop drain their own mailbox; delivering to them
# from here would answer in the wrong process, with the wrong tools mounted.
CARRIER_KINDS = ("node", "container")

_PUMP_TASKS: Set[asyncio.Task] = set()


def channel_for(instance_id: str) -> str:
    return f"instance:{instance_id}"


def _conversation_id(instance: Dict[str, Any]) -> str:
    """A stable conversation for this copy across revivals.

    Chat instances already carry their conversation id; everything else gets a
    thread of its own keyed by the instance, so a revived task copy does not
    land in some unrelated conversation.
    """
    return str(instance.get("task_id") or instance.get("instance_id"))


def _build_request(instance: Dict[str, Any], body: str, max_turns: int = 20):
    from chat.models import ChatRequest

    instance_id = str(instance["instance_id"])
    return ChatRequest(
        agent_id=instance.get("agent_id"),
        message=body,
        workspace=instance.get("workspace"),
        project_id=instance.get("project_id"),
        history=build_instance_history(instance_id, max_turns=max_turns),
        conversation_id=_conversation_id(instance),
        conversation_title=instance.get("label") or instance.get("agent_id"),
        source="instance",
        instance_id=instance_id,
    )


def can_deliver_directly(instance: Dict[str, Any]) -> bool:
    """True when this process should run the turn itself."""
    if instance.get("kind") in CARRIER_KINDS:
        return False
    return instance.get("state") != "active"


async def deliver(instance: Dict[str, Any], body: str, *,
                  client_id: Optional[str] = None,
                  msg_id: Optional[str] = None) -> Dict[str, Any]:
    """Deliver one message, choosing mailbox or direct revival.

    Returns ``{mode, instance_id, channel?, msg_id?}``. ``mode`` is ``queued``
    when the copy will answer it itself, ``running`` when this call revived it.
    """
    instance_id = str(instance["instance_id"])

    if not can_deliver_directly(instance):
        queued = msg_id or inbox.enqueue(instance_id, body)
        return {"mode": "queued", "instance_id": instance_id, "msg_id": queued}

    channel = channel_for(instance_id)
    if client_id:
        broker.add_channel(client_id, channel)

    request = _build_request(instance, body)

    async def pump():
        from chat.pipelines import run_chat_pipeline
        run_id = None
        try:
            async for event in run_chat_pipeline(request):
                if not run_id and isinstance(event, dict) and event.get("run_id"):
                    run_id = event["run_id"]
                await broker.apublish(channel, event)
        except Exception as e:  # noqa: BLE001 — surfaced to the page, not swallowed
            await broker.apublish(channel, {"type": "done", "ok": False, "error": str(e)})
            if msg_id:
                inbox.mark_error(msg_id, str(e))
        finally:
            if msg_id and run_id:
                inbox.attach_run(msg_id, run_id)
            await broker.apublish(channel, {"type": "instance_stream_end"})
            if client_id:
                broker.remove_channel(client_id, channel)

    task = asyncio.create_task(pump())
    _PUMP_TASKS.add(task)
    task.add_done_callback(_PUMP_TASKS.discard)
    return {"mode": "running", "instance_id": instance_id, "channel": channel,
            "msg_id": msg_id}


def claim_for_idle(limit: int = 50) -> list:
    """Claim one queued message from each instance that is idle enough to answer.

    Synchronous: it reads and writes the database. Callers on an event loop must
    hand it to a worker thread — see :func:`drain_idle`.
    """
    from common import db

    rows = db.get_conn().execute(
        "SELECT DISTINCT instance_id FROM instance_inbox WHERE delivered_at IS NULL LIMIT ?",
        (int(limit),),
    ).fetchall()
    claimed = []
    for row in rows:
        instance = store.get(str(row["instance_id"]))
        if instance is None or not can_deliver_directly(instance):
            continue
        message = inbox.claim_next(instance["instance_id"])
        if message is not None:
            claimed.append((instance, message))
    return claimed


async def drain_idle(limit: int = 50) -> int:
    """Deliver queued messages to instances that have since gone idle.

    A message written to a busy copy waits in its mailbox; nothing in the
    subprocess that finishes the run can start a chat turn, so the backend picks
    it up here. Carrier-backed instances are skipped — their own loop claims.

    The scan and the claim are SQLite work, so they run in a worker thread: this
    is called from the watchdog's loop, and a blocking write there would stall
    every request the server is serving.
    """
    claimed = await asyncio.to_thread(claim_for_idle, limit)
    delivered = 0
    for instance, message in claimed:
        msg_id = str(message.get("msg_id"))
        try:
            await deliver(instance, str(message.get("body") or ""), msg_id=msg_id)
            delivered += 1
        except Exception:
            await asyncio.to_thread(inbox.mark_error, msg_id, "delivery failed")
    return delivered
