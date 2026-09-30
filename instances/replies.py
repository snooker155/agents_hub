"""
The answer to one mailbox message, for callers that wait on it.

The instance page streams its answers live; a caller of the public address
(``/api/external/{token}/messages``) or of a carrier's direct port usually
wants the answer itself. A message is answered by exactly one run
(``instance_inbox.run_id``), so its state is readable from two rows:

- ``queued``: not claimed yet (the carrier is busy or starting);
- ``running``: claimed, its run is going;
- ``completed`` / ``failed`` / ``stopped``: the run's end, with its output
  or error.

:func:`wait_sync` and :func:`wait_async` poll those rows until the message
reaches an end or the timeout runs out; either way they return the latest
state, so a caller that timed out can come back for the answer later.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from instances import inbox

TERMINAL = ("completed", "failed", "stopped", "error")
POLL_SECONDS = 0.25


def reply_for(msg_id: str) -> Optional[Dict[str, Any]]:
    """The current state of one message, or None when there is no such message."""
    msg = inbox.get(msg_id)
    if msg is None:
        return None
    out: Dict[str, Any] = {
        "msg_id": msg["msg_id"],
        "instance_id": msg.get("instance_id"),
        "conversation_id": inbox.public_conversation(msg.get("conversation_id")),
        "run_id": msg.get("run_id"),
        "status": "queued" if not msg.get("delivered_at") else "running",
    }
    run_id = msg.get("run_id")
    if not run_id:
        if msg.get("error"):
            out.update(status="failed", error=msg["error"])
        return out
    from managers.run_manager import get_run_by_id

    run = get_run_by_id(str(run_id)) or {}
    status = str(run.get("status") or "running")
    if status == "error":
        status = "failed"
    if status in TERMINAL:
        out["status"] = status
        proc = run.get("process") if isinstance(run.get("process"), dict) else {}
        if status == "completed":
            output = run.get("output") or _process_text(proc)
            if not output:
                # The record carries a slim process; the answer of a turn
                # executed as the chat pipeline is in the full payload.
                from managers.run_manager import get_run_process
                try:
                    output = _process_text(get_run_process(str(run_id)) or {})
                except Exception:  # noqa: BLE001 - an unreadable payload reads as no answer
                    output = ""
            out["output"] = output
        else:
            out["error"] = run.get("error") or msg.get("error") or status
        usage = proc.get("token_usage") or {}
        out["usage"] = {
            "prompt_tokens": int(usage.get("inbound_tokens") or 0),
            "completion_tokens": int(usage.get("outbound_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        } if usage else {}
        if proc.get("duration_ms") is not None:
            out["duration_ms"] = proc.get("duration_ms")
    return out


def _process_text(proc: Dict[str, Any]) -> str:
    """The answer of a run that kept it in its process payload rather than in
    ``output``: a chat turn executed on a replica (chat/turns.py) stores the
    reply as the pipeline does, under ``response.text`` and the input context."""
    response = proc.get("response")
    if isinstance(response, dict) and response.get("text"):
        return str(response["text"])
    if isinstance(response, str) and response:
        return response
    ctx = proc.get("llm_input_context")
    if isinstance(ctx, dict) and ctx.get("response"):
        return str(ctx["response"])
    return ""


def is_final(reply: Optional[Dict[str, Any]]) -> bool:
    return bool(reply) and reply.get("status") in TERMINAL


def wait_sync(msg_id: str, timeout: float) -> Optional[Dict[str, Any]]:
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        reply = reply_for(msg_id)
        if reply is None or is_final(reply) or time.monotonic() >= deadline:
            return reply
        time.sleep(POLL_SECONDS)


async def wait_async(msg_id: str, timeout: float) -> Optional[Dict[str, Any]]:
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        reply = await asyncio.to_thread(reply_for, msg_id)
        if reply is None or is_final(reply) or time.monotonic() >= deadline:
            return reply
        await asyncio.sleep(POLL_SECONDS)
