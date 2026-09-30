"""The calling half of A2A: what the hub sends to a foreign agent, and reads back.

``agents.remote_agent.RemoteAgent`` owns the transport (httpx, timeouts, auth
headers, streaming). This module owns the wire format: it builds the JSON-RPC
request and turns whatever comes back into the two shapes the hub already
understands, an outcome dict for ``_map_response`` and the stream frames
``common.agent_frames`` translates. Both directions are pure functions over
dicts, so the awkward cases (a Task that completed with its answer only in the
status message, an ``input-required`` that must become ``awaiting_input``) are
tested against recorded JSON instead of against a live agent.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

JSONRPC_VERSION = "2.0"

#: Methods this client calls. Everything else in the spec is either optional or
#: server-side, see docs/a2a.md.
SEND = "message/send"
STREAM = "message/stream"
GET = "tasks/get"
CANCEL = "tasks/cancel"


def new_id() -> str:
    return uuid.uuid4().hex


def build_message(
    text: str,
    *,
    task_id: Optional[str] = None,
    context_id: Optional[str] = None,
) -> Dict[str, Any]:
    """The ``user`` Message one prompt becomes."""
    message: Dict[str, Any] = {
        "role": "user",
        "parts": [{"kind": "text", "text": text}],
        "messageId": new_id(),
        "kind": "message",
    }
    # Sending taskId is what turns a second message into a continuation of the
    # same task rather than a new one. It is how a paused remote agent is
    # answered, and the whole reason resume works over A2A.
    if task_id:
        message["taskId"] = task_id
    if context_id:
        message["contextId"] = context_id
    return message


def build_send_request(
    text: str,
    *,
    stream: bool = False,
    task_id: Optional[str] = None,
    context_id: Optional[str] = None,
    run_id: Optional[str] = None,
    workspace: Optional[str] = None,
    blocking: bool = True,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """A ``message/send`` (or ``message/stream``) JSON-RPC request.

    ``run_id`` and ``workspace`` travel in ``metadata``: the spec leaves that
    field to the application, and a remote agent that logs alongside this hub
    can then tie its own record to the run that caused it.
    """
    metadata: Dict[str, Any] = {}
    if run_id:
        metadata["run_id"] = str(run_id)
    if workspace:
        metadata["workspace"] = str(workspace)

    params: Dict[str, Any] = {
        "message": build_message(text, task_id=task_id, context_id=context_id),
    }
    if metadata:
        params["metadata"] = metadata
    if not stream:
        # Ask the remote to hold the connection until it has an answer. A
        # non-blocking send would hand back a `submitted` task and leave the hub
        # polling an agent it has no reason to poll: the run is already
        # synchronous on this side.
        params["configuration"] = {"blocking": bool(blocking)}

    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id or new_id(),
        "method": STREAM if stream else SEND,
        "params": params,
    }


def build_get_request(task_id: str, *, request_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id or new_id(),
        "method": GET,
        "params": {"id": task_id},
    }


def build_cancel_request(task_id: str, *, request_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id or new_id(),
        "method": CANCEL,
        "params": {"id": task_id},
    }


# ── reading ─────────────────────────────────────────────────────────────────

def text_of(message_or_artifact: Any) -> str:
    """Concatenate the text parts of a Message or an Artifact."""
    if not isinstance(message_or_artifact, dict):
        return ""
    parts = message_or_artifact.get("parts")
    if not isinstance(parts, list):
        return ""
    chunks: List[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("kind") in (None, "text"):
            text = part.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks)


def artifacts_text(task: Dict[str, Any]) -> str:
    """The answer: every artifact's text, in the order the agent produced it."""
    artifacts = task.get("artifacts")
    if not isinstance(artifacts, list):
        return ""
    return "\n".join(t for t in (text_of(a) for a in artifacts) if t).strip()


def status_text(task: Dict[str, Any]) -> str:
    status = task.get("status")
    if not isinstance(status, dict):
        return ""
    return text_of(status.get("message")).strip()


def task_state(task: Dict[str, Any]) -> str:
    status = task.get("status")
    if not isinstance(status, dict):
        return "unknown"
    return str(status.get("state") or "unknown")


def parse_envelope(payload: Any) -> Dict[str, Any]:
    """Split a JSON-RPC response into ``{"result": ...}`` or ``{"error": ...}``.

    A body that is not a JSON-RPC response at all is reported as an error rather
    than probed further: an agent answering 200 with something else is broken in
    a way the run record should name.
    """
    if not isinstance(payload, dict):
        return {"error": {"code": None, "message": f"the agent did not return a JSON object: {str(payload)[:300]}"}}
    if isinstance(payload.get("error"), dict):
        return {"error": payload["error"]}
    if "result" in payload:
        return {"result": payload["result"]}
    return {"error": {"code": None, "message": f"no 'result' or 'error' in the agent's reply: {str(payload)[:300]}"}}


def _error_text(error: Dict[str, Any]) -> str:
    code = error.get("code")
    message = str(error.get("message") or "the agent reported an error")
    return f"A2A error {code}: {message}" if code is not None else f"A2A error: {message}"


def outcome_from_response(payload: Any) -> Dict[str, Any]:
    """Turn a ``message/send`` reply into the fields an AgentResult needs.

    Returns ``{ok, status, output, error, key}`` where ``status`` is one of the
    hub's own words: ``done``, ``error`` or ``awaiting_input``. ``key`` carries
    the remote task id when the agent paused, because answering it means sending
    a second message with that ``taskId``. Without the id the conversation
    cannot be continued, only restarted.
    """
    envelope = parse_envelope(payload)
    if "error" in envelope:
        return {"ok": False, "status": "error", "output": "", "error": _error_text(envelope["error"]), "key": ""}

    result = envelope["result"]

    # A remote may answer a message with a Message instead of a Task when it has
    # nothing to track: a direct reply. Treat it as a finished run.
    if isinstance(result, dict) and result.get("kind") == "message":
        text = text_of(result).strip()
        if text:
            return {"ok": True, "status": "done", "output": text, "error": "", "key": ""}
        return {"ok": False, "status": "error", "output": "",
                "error": "the agent replied with an empty message", "key": ""}

    if not isinstance(result, dict):
        return {"ok": False, "status": "error", "output": "",
                "error": f"the agent's result is not a Task: {str(result)[:300]}", "key": ""}

    state = task_state(result)
    task_id = str(result.get("id") or "")
    answer = artifacts_text(result)
    remark = status_text(result)

    if state == "completed":
        # Falling back to the status message is not politeness: plenty of agents
        # answer short questions with a message and never produce an artifact,
        # and treating those as empty would lose the whole reply.
        output = answer or remark
        if not output:
            return {"ok": False, "status": "error", "output": "",
                    "error": f"the agent completed task {task_id} without any text", "key": ""}
        return {"ok": True, "status": "done", "output": output, "error": "", "key": task_id}

    if state == "input-required" or state == "auth-required":
        return {
            "ok": True,
            "status": "awaiting_input",
            "output": remark or answer,
            "error": "",
            "key": task_id,
            "question": remark or answer,
        }

    if state in ("failed", "rejected", "canceled"):
        detail = remark or answer or f"the agent's task ended as '{state}'"
        return {"ok": False, "status": "error", "output": answer,
                "error": f"the agent's task {task_id or ''} ended as '{state}': {detail}".strip(), "key": task_id}

    # submitted / working / unknown: the remote accepted the work but did not
    # wait for it. The hub's run is synchronous, so there is nothing to return.
    return {
        "ok": False,
        "status": "error",
        "output": answer,
        "error": (
            f"the agent left task {task_id or '?'} in state '{state}' instead of answering. "
            f"It may not support blocking sends; the task can be polled with tasks/get."
        ),
        "key": task_id,
    }


def frames_from_stream_event(payload: Any) -> List[Dict[str, Any]]:
    """Translate one SSE frame of ``message/stream`` into hub stream frames.

    The hub already knows how to render ``token`` / ``done`` / ``interrupt``
    (see ``common.agent_frames``), so a remote agent's A2A events are mapped
    onto that vocabulary, and everything downstream (the chat bubble, the run
    trail, the task state) works exactly as it does for a native remote.

    A frame nobody can use yields no hub frames rather than an error: an A2A
    server is free to send keep-alives and event kinds this hub does not read.
    """
    envelope = parse_envelope(payload)
    if "error" in envelope:
        return [{"type": "done", "ok": False, "output": "", "error": _error_text(envelope["error"])}]

    event = envelope["result"]
    if not isinstance(event, dict):
        return []

    kind = str(event.get("kind") or "")

    if kind == "artifact-update":
        text = text_of(event.get("artifact"))
        # Held back rather than emitted as tokens: the artifact repeats what the
        # status updates already streamed, and emitting both would print the
        # answer twice. The `done` frame below carries it as the output.
        return [{"type": "a2a_artifact", "text": text}] if text else []

    if kind == "status-update":
        status = event.get("status") if isinstance(event.get("status"), dict) else {}
        state = str(status.get("state") or "")
        text = text_of(status.get("message"))
        final = bool(event.get("final"))
        if state == "input-required":
            return [{"type": "interrupt", "question": text.strip(), "choices": [],
                     "key": str(event.get("taskId") or "")}]
        if state in ("failed", "rejected"):
            return [{"type": "done", "ok": False, "output": "", "error": text or f"the agent's task {state}"}]
        if state == "canceled":
            return [{"type": "done", "ok": False, "output": "", "error": text or "the agent's task was canceled"}]
        if state == "completed":
            return [{"type": "done", "ok": True, "output": text, "error": None}]
        if text:
            return [{"type": "token", "token": text}]
        if final:
            return [{"type": "done", "ok": True, "output": "", "error": None}]
        return []

    if kind == "task":
        # The first frame of a stream is usually the Task itself. Nothing to
        # show yet; its id is read by the caller, not by the translator.
        return []

    return []


__all__ = [
    "JSONRPC_VERSION",
    "SEND",
    "STREAM",
    "GET",
    "CANCEL",
    "new_id",
    "build_message",
    "build_send_request",
    "build_get_request",
    "build_cancel_request",
    "text_of",
    "artifacts_text",
    "status_text",
    "task_state",
    "parse_envelope",
    "outcome_from_response",
    "frames_from_stream_event",
]
