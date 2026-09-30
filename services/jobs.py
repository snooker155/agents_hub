"""
Jobs the backend hands to a runner replica (docs/services.md).

The backend used to run an agent in its own process for an eval case, a
replay, a task decomposition, a project graph, a playground world or
scenario, and the agent part of every entity chat. Each of those is now a
``job`` message (instances/inbox.py KIND_JOB) in a runner's mailbox: the
workspace's runner service is chosen like for a chat turn
(services/routing.py), a replica is picked, the job is written, and the
result document the replica closes the message with is read back
(runtime/jobs.py executes it there). With chat execution on ``inprocess`` the
callers keep running the agent themselves; :func:`enabled` says which.

:func:`invoke_sync` and :func:`invoke_async` are the one-call form for the
``invoke`` job; :func:`submit` and the ``wait_*`` helpers are the pieces for
callers that stream (chat/remote_agent.py) or do not wait (a decomposition).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 1800.0
POLL_SECONDS = 0.3
LIVENESS_CHECK_SECONDS = 2.0


class JobError(RuntimeError):
    """The job failed, was refused, or could not be placed."""

    def __init__(self, message: str, *, status: int = 500) -> None:
        super().__init__(message)
        self.status = status


class JobTimeout(JobError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status=504)


def enabled() -> bool:
    from chat import routing
    return routing.enabled()


def submit(workspace: Optional[str], job: str, args: Dict[str, Any], *,
           label: str = "") -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    """Write a job into a replica of the workspace's runner. Returns
    ``(service, replica, msg_id)``. Raises :class:`JobError` when no replica
    can take it."""
    from common import api_keys, identity
    from instances import inbox
    from services import routing
    from services.replicas import ServiceUnavailable

    service = routing.service_for(workspace, None)
    conversation_id = f"job:{uuid4().hex[:12]}"
    try:
        replica = routing.replicas.pick(service, conversation_id=conversation_id, allow_start=True)
    except ServiceUnavailable as exc:
        raise JobError(str(exc), status=503) from exc
    except Exception as exc:  # noqa: BLE001 - a replica that cannot start
        raise JobError(f"could not start a runner replica: {exc}", status=503) from exc
    payload = {
        "job": job, "args": args,
        "user_id": identity.current_user_id() or None,
        "key_id": api_keys.current_key_id(),
        "budget_usd": service.get("budget_usd"),
    }
    msg_id = inbox.enqueue(str(replica["instance_id"]),
                           (label or f"{job}: {args.get('agent_id') or ''}")[:4000],
                           origin="job", conversation_id=conversation_id,
                           kind=inbox.KIND_JOB, payload=payload)
    return service, replica, msg_id


def _replica_gone(replica_id: str) -> Optional[str]:
    from chat.routing import _replica_gone as gone
    return gone(replica_id)


def _poll(msg_id: str) -> Tuple[bool, Any]:
    """``(finished, result)``; raises JobError when the job ended in error."""
    from instances import inbox
    msg = inbox.get(msg_id)
    if msg is None:
        raise JobError("the job message vanished")
    if not msg.get("finished_at"):
        return False, None
    result = inbox.result_of(msg)
    if result is None and msg.get("error"):
        raise JobError(str(msg["error"]))
    return True, (result if result is not None else {})


def wait_sync(msg_id: str, replica_id: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Any:
    from instances import inbox
    deadline = time.monotonic() + max(1.0, float(timeout))
    last_check = time.monotonic()
    while True:
        done, result = _poll(msg_id)
        if done:
            return result
        now = time.monotonic()
        if now - last_check >= LIVENESS_CHECK_SECONDS:
            last_check = now
            gone = _replica_gone(replica_id)
            if gone:
                inbox.mark_error(msg_id, gone)
                raise JobError(gone, status=503)
        if now >= deadline:
            inbox.mark_error(msg_id, f"the job did not finish within {int(timeout)}s")
            raise JobTimeout(f"the runner did not finish the job within {int(timeout)}s")
        time.sleep(POLL_SECONDS)


async def wait_async(msg_id: str, replica_id: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Any:
    from instances import inbox
    deadline = time.monotonic() + max(1.0, float(timeout))
    last_check = time.monotonic()
    while True:
        done, result = await asyncio.to_thread(_poll, msg_id)
        if done:
            return result
        now = time.monotonic()
        if now - last_check >= LIVENESS_CHECK_SECONDS:
            last_check = now
            gone = await asyncio.to_thread(_replica_gone, replica_id)
            if gone:
                await asyncio.to_thread(inbox.mark_error, msg_id, gone)
                raise JobError(gone, status=503)
        if now >= deadline:
            await asyncio.to_thread(inbox.mark_error, msg_id,
                                    f"the job did not finish within {int(timeout)}s")
            raise JobTimeout(f"the runner did not finish the job within {int(timeout)}s")
        await asyncio.sleep(POLL_SECONDS)


def invoke_sync(workspace: Optional[str], args: Dict[str, Any], *,
                timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Run one agent invocation on a runner and return its result document
    (runtime/jobs.py ``invoke``): ``{run_id, ok, status, output, error,
    error_code, duration_ms, process, provider, model, steps}``."""
    _service, replica, msg_id = submit(workspace, "invoke", args)
    return wait_sync(msg_id, str(replica["instance_id"]), timeout)


async def invoke_async(workspace: Optional[str], args: Dict[str, Any], *,
                       timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Dict[str, Any]:
    _service, replica, msg_id = await asyncio.to_thread(submit, workspace, "invoke", args)
    return await wait_async(msg_id, str(replica["instance_id"]), timeout)


class InvokeResult:
    """A job's result document read like an ``AgentResult``: ``ok``,
    ``agent_output``, ``error``, ``status``, ``steps`` (each with ``name`` and
    ``output``), so a caller that inspected the agent's result keeps working."""

    def __init__(self, data: Dict[str, Any]) -> None:
        from types import SimpleNamespace
        self.data = dict(data or {})
        self.ok = bool(self.data.get("ok"))
        self.status = str(self.data.get("status") or ("completed" if self.ok else "failed"))
        self.agent_output = str(self.data.get("output") or "")
        self.error = self.data.get("error")
        self.error_code = self.data.get("error_code")
        self.run_id = self.data.get("run_id")
        self.provider = str(self.data.get("provider") or "")
        self.model = str(self.data.get("model") or "")
        self.duration_ms = int(self.data.get("duration_ms") or 0)
        self.steps = [SimpleNamespace(name=str(s.get("name") or ""), output=str(s.get("output") or ""))
                      for s in (self.data.get("steps") or []) if isinstance(s, dict)]
        self.pending_approval = self.data.get("pending_approval")
        process = self.data.get("process") or {}
        self.token_usage = dict(process.get("token_usage") or {})


__all__ = ["JobError", "JobTimeout", "InvokeResult", "enabled", "invoke_async", "invoke_sync",
           "submit", "wait_async", "wait_sync"]
