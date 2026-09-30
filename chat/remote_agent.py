"""
The streaming agent part of an entity chat, run on a runner replica.

An entity chat (chat/entity_chat.py: the agent definition chat, a flow's
planner, the page chat, ...) and the project planner (routes/projects.py)
keep their own turn plumbing in the backend: the transcript, the prompt, the
run record, the reply written back. Only the middle, building the agent and
streaming ``arun``, ran an agent in the backend process. This module puts
that middle on a runner replica: :func:`stream_agent_turn` writes an
``entity_turn`` job (services/jobs.py), subscribes to the run's channel
``entity:<run_id>`` before it is written so no event is missed, and relays
what the replica posts (runtime/jobs.py) onto the caller's queue exactly as
the local callback would have put it there. The closing ``entity_result``
carries what the caller needs to finish the turn: the output, the error, the
usage, the process payload, the provider and the model.

Cancelling the caller's task (the stop button) stops the run on the replica
through its run record, as the chat's stop button does.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

END_GRACE_SECONDS = 5.0
STATE_CHECK_SECONDS = 2.0


def enabled() -> bool:
    from services import jobs
    return jobs.enabled()


@dataclass
class RemoteOutcome:
    """What the replica reported for the turn, read like an ``AgentResult``."""
    ok: bool = False
    status: str = "failed"
    agent_output: str = ""
    error: Optional[str] = None
    error_code: Optional[str] = None
    usage: Dict[str, Any] = field(default_factory=dict)
    process: Dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    tool_calls: int = 0
    duration_ms: int = 0
    steps: List[Any] = field(default_factory=list)
    #: The replica that executed the turn, and its service.
    instance_id: Optional[str] = None
    service_id: Optional[str] = None

    def run_fields(self) -> Dict[str, Any]:
        """What the caller writes onto the run record so the run belongs to
        the replica that executed it (a carrier run of it, in its service)."""
        if not self.instance_id:
            return {}
        out: Dict[str, Any] = {"instance_id": self.instance_id, "carrier_run": True}
        if self.service_id:
            out["service_id"] = self.service_id
        return out

    @classmethod
    def from_event(cls, event: Dict[str, Any]) -> "RemoteOutcome":
        return cls(
            instance_id=event.get("instance_id"), service_id=event.get("service_id"),
            ok=bool(event.get("ok")), status=str(event.get("status") or "failed"),
            agent_output=str(event.get("output") or ""), error=event.get("error"),
            error_code=event.get("error_code"), usage=dict(event.get("usage") or {}),
            process=dict(event.get("process") or {}), provider=str(event.get("provider") or ""),
            model=str(event.get("model") or ""), tool_calls=int(event.get("tool_calls") or 0),
            duration_ms=int(event.get("duration_ms") or 0),
            steps=[SimpleNamespace(name=str(s.get("name") or ""), output=str(s.get("output") or ""))
                   for s in (event.get("steps") or []) if isinstance(s, dict)],
        )


def _timeouts() -> Dict[str, float]:
    from chat.routing import _timeouts as chat_timeouts
    return chat_timeouts()


async def stream_agent_turn(queue: asyncio.Queue, *, agent_id: str, run_id: str,
                            prompt: str, workspace: Optional[str],
                            workspace_path: Optional[str] = None,
                            project_id: Optional[str] = None,
                            session_id: Optional[str] = None,
                            log_file: Optional[str] = None,
                            log_lines: Optional[List[str]] = None,
                            build: Optional[Dict[str, Any]] = None,
                            user_message: Optional[str] = None,
                            emit_agent_event: bool = True) -> RemoteOutcome:
    """Run the agent part of one entity turn on a runner and relay its events
    onto ``queue``. Returns the outcome; raises ``services.jobs.JobError``
    when no replica takes the job, and propagates ``CancelledError`` after
    stopping the run."""
    from common.session_broker import broker
    from instances import inbox
    from services import jobs

    channel = f"entity:{run_id}"
    args = {
        "agent_id": agent_id, "run_id": run_id, "prompt": prompt,
        "workspace_name": workspace, "workspace_path": workspace_path,
        "project_id": project_id, "session_id": session_id,
        "log_file": str(log_file) if log_file else None,
        "log_lines": list(log_lines or []), "build": dict(build or {}),
        "user_message": user_message,
    }
    client_id, events = broker.open_client([channel])
    replica_id: Optional[str] = None
    msg_id: Optional[str] = None
    try:
        _service, replica, msg_id = await asyncio.to_thread(
            jobs.submit, workspace, "entity_turn", args, label=f"entity turn: {agent_id}")
        replica_id = str(replica["instance_id"])
        timeouts = _timeouts()
        started = False
        last_check = time.monotonic()
        deadline = last_check + timeouts["start"]
        while True:
            try:
                event = await asyncio.wait_for(events.get(), timeout=1.0)
            except asyncio.TimeoutError:
                event = None
            now = time.monotonic()
            if event is not None:
                etype = event.get("type")
                if etype == "entity_result":
                    return RemoteOutcome.from_event(event)
                started = True
                deadline = now + timeouts["idle"]
                if etype == "agent" and not emit_agent_event:
                    continue
                out = {k: v for k, v in event.items() if k not in ("channel", "id")}
                await queue.put(out)
                continue
            if now - last_check >= STATE_CHECK_SECONDS:
                last_check = now
                # The result may have landed on the row while the channel
                # event was lost (a backend restart between the two).
                msg = await asyncio.to_thread(inbox.get, msg_id)
                if msg and msg.get("finished_at"):
                    result = inbox.result_of(msg)
                    if isinstance(result, dict):
                        return RemoteOutcome.from_event(result)
                    return RemoteOutcome(ok=False, error=str(msg.get("error") or "the turn failed"))
                gone = await asyncio.to_thread(jobs._replica_gone, replica_id)
                if gone:
                    await asyncio.to_thread(inbox.mark_error, msg_id, gone)
                    return RemoteOutcome(ok=False, error=gone)
            if now >= deadline:
                if not started:
                    error = f"the replica did not pick the turn up within {int(timeouts['start'])}s"
                else:
                    error = f"the agent did not answer within {int(timeouts['idle'])}s"
                    await _stop(run_id)
                await asyncio.to_thread(inbox.mark_error, msg_id, error)
                return RemoteOutcome(ok=False, error=error)
    except asyncio.CancelledError:
        await _stop(run_id)
        raise
    finally:
        broker.close_client(client_id)


async def _stop(run_id: str) -> None:
    try:
        from managers.run_manager import stop_run_by_id
        await asyncio.to_thread(stop_run_by_id, run_id)
    except Exception:  # noqa: BLE001 - the stop is a courtesy
        log.debug("remote turn %s: stop failed", run_id, exc_info=True)


__all__ = ["RemoteOutcome", "enabled", "stream_agent_turn"]
