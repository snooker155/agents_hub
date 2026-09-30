"""The serving half of A2A: JSON-RPC envelopes and the hub-to-spec mapping.

Everything here is a pure function over dicts. The router (``routes/a2a.py``)
does the I/O (it creates tasks, starts runs and streams) and calls into this
module for every decision about *shape*: what a request means, what a Task looks
like, which A2A state a hub run is in. Keeping the two apart is what lets the
protocol be tested without a server, which matters because the protocol is the
part an external client depends on and the part we cannot see failing.

The vocabularies being bridged:

    hub run status      A2A task state
    ------------------  ------------------
    pending, assigned   submitted
    running, stop       working
    completed, done     completed
    failed              failed
    stopped             canceled

    hub task status     A2A task state
    ------------------  ------------------
    awaiting_input      input-required   (the question becomes the status message)
    awaiting_approval   input-required   (the tool call waiting for a yes)

A task waiting on a person is ``input-required`` in both hub cases because from
the caller's side they are the same situation: nothing more will happen until an
answer arrives, and the answer goes back the same way.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

JSONRPC_VERSION = "2.0"

# Spec error codes. The first four are JSON-RPC's own; the -32001/-32002 pair is
# A2A's, and a client that knows the protocol branches on them.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
TASK_NOT_FOUND = -32001
TASK_NOT_CANCELABLE = -32002
UNSUPPORTED_OPERATION = -32004

#: The methods this hub answers. ``tasks/pushNotificationConfig/*`` and
#: ``tasks/resubscribe`` are deliberately absent, see docs/a2a.md.
METHODS = ("message/send", "message/stream", "tasks/get", "tasks/cancel")

# A2A task states.
SUBMITTED = "submitted"
WORKING = "working"
INPUT_REQUIRED = "input-required"
COMPLETED = "completed"
CANCELED = "canceled"
FAILED = "failed"
REJECTED = "rejected"
AUTH_REQUIRED = "auth-required"
UNKNOWN = "unknown"

#: States in which nothing more will happen on its own.
TERMINAL_STATES = frozenset({COMPLETED, CANCELED, FAILED, REJECTED})

_RUN_STATES = {
    "pending": SUBMITTED,
    "assigned": SUBMITTED,
    "starting": SUBMITTED,
    "awaiting_approval": INPUT_REQUIRED,
    "running": WORKING,
    "stop": WORKING,
    "completed": COMPLETED,
    "done": COMPLETED,
    "failed": FAILED,
    "error": FAILED,
    "stopped": CANCELED,
    "canceled": CANCELED,
    "cancelled": CANCELED,
}

_TASK_STATES = {
    "todo": SUBMITTED,
    "ready": SUBMITTED,
    "pending": SUBMITTED,
    "in_progress": WORKING,
    "reviewing": WORKING,
    "awaiting_input": INPUT_REQUIRED,
    "awaiting_approval": INPUT_REQUIRED,
    "blocked": FAILED,
    "stopped": CANCELED,
    "resolved": COMPLETED,
    "reviewed": COMPLETED,
    "done": COMPLETED,
}


class JsonRpcError(Exception):
    """A protocol-level refusal, carrying the code the spec assigns it."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


# ── envelopes ───────────────────────────────────────────────────────────────

def result_response(request_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def error_response(request_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def error_from(exc: JsonRpcError, request_id: Any = None) -> Dict[str, Any]:
    return error_response(request_id, exc.code, exc.message, exc.data)


def parse_request(body: Any) -> tuple[str, Dict[str, Any], Any]:
    """Validate one JSON-RPC request and return ``(method, params, id)``.

    Raises :class:`JsonRpcError` with the code the spec assigns, so the caller
    turns any refusal into a well-formed error response instead of a 500 whose
    body an A2A client cannot read.
    """
    if not isinstance(body, dict):
        raise JsonRpcError(INVALID_REQUEST, "a JSON-RPC request must be an object")

    request_id = body.get("id")
    if body.get("jsonrpc") != JSONRPC_VERSION:
        raise JsonRpcError(INVALID_REQUEST, "'jsonrpc' must be exactly '2.0'")

    method = body.get("method")
    if not isinstance(method, str) or not method:
        raise JsonRpcError(INVALID_REQUEST, "'method' is missing")
    if method not in METHODS:
        raise JsonRpcError(
            METHOD_NOT_FOUND,
            f"method '{method}' is not supported",
            {"supported": list(METHODS)},
        )

    params = body.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise JsonRpcError(INVALID_PARAMS, "'params' must be an object")
    return method, params, request_id


# ── messages and parts ──────────────────────────────────────────────────────

def text_part(text: str) -> Dict[str, Any]:
    return {"kind": "text", "text": text}


def message_text(message: Any) -> str:
    """Concatenate the text parts of a Message, ignoring what we cannot read.

    A client may send a file or data part alongside the text; the hub takes a
    prompt, so the non-text parts are dropped rather than rendered as JSON into
    an agent's instruction.
    """
    if not isinstance(message, dict):
        return ""
    parts = message.get("parts")
    if not isinstance(parts, list):
        return ""
    chunks: List[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("kind") in (None, "text") and isinstance(part.get("text"), str):
            chunks.append(part["text"])
    return "".join(chunks).strip()


def build_message(
    role: str,
    text: str,
    *,
    message_id: Optional[str] = None,
    task_id: Optional[str] = None,
    context_id: Optional[str] = None,
) -> Dict[str, Any]:
    message: Dict[str, Any] = {
        "role": role,
        "parts": [text_part(text)],
        "messageId": message_id or new_id(),
        "kind": "message",
    }
    if task_id:
        message["taskId"] = task_id
    if context_id:
        message["contextId"] = context_id
    return message


def require_message(params: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the ``message`` out of ``message/send`` params, or refuse."""
    message = params.get("message")
    if not isinstance(message, dict):
        raise JsonRpcError(INVALID_PARAMS, "'message' is required")
    if not message_text(message):
        raise JsonRpcError(INVALID_PARAMS, "'message' carries no text part")
    return message


def require_task_id(params: Dict[str, Any]) -> str:
    task_id = str(params.get("id") or "").strip()
    if not task_id:
        raise JsonRpcError(INVALID_PARAMS, "'id' is required")
    return task_id


def blocking_requested(params: Dict[str, Any]) -> bool:
    """Whether the caller asked ``message/send`` to wait for the answer."""
    configuration = params.get("configuration")
    return bool(isinstance(configuration, dict) and configuration.get("blocking"))


def metadata_of(params: Dict[str, Any]) -> Dict[str, Any]:
    metadata = params.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


# ── state mapping ───────────────────────────────────────────────────────────

def state_for(run_status: str = "", task_status: str = "") -> str:
    """The A2A state for a hub run, with the task's own status as context.

    The task wins when it is parked on a person: a run record may still read
    ``running`` for a moment after the agent asked its question, and answering
    "working" to a client that is in fact being waited on is the one mapping
    error that deadlocks a conversation.
    """
    task = (task_status or "").strip().lower()
    if task in ("awaiting_input", "awaiting_approval"):
        return INPUT_REQUIRED

    run = (run_status or "").strip().lower()
    if run in _RUN_STATES:
        return _RUN_STATES[run]
    if task in _TASK_STATES:
        return _TASK_STATES[task]
    return UNKNOWN


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


# ── Task objects and stream events ──────────────────────────────────────────

def build_task(
    *,
    task_id: str,
    context_id: str,
    state: str,
    status_text: str = "",
    artifact_text: str = "",
    artifact_name: str = "result",
    history: Optional[Sequence[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """An A2A Task object.

    ``status_text`` is what the agent is saying right now: the question it
    stopped on, or the error that ended it. ``artifact_text`` is the answer
    itself, which the spec carries as an artifact rather than as a message so a
    client can tell a result from a remark about one.
    """
    status: Dict[str, Any] = {"state": state, "timestamp": now_iso()}
    if status_text:
        status["message"] = build_message("agent", status_text, task_id=task_id, context_id=context_id)

    task: Dict[str, Any] = {
        "id": task_id,
        "contextId": context_id,
        "kind": "task",
        "status": status,
        "artifacts": [],
    }
    if artifact_text:
        task["artifacts"].append({
            "artifactId": f"{task_id}-{artifact_name}",
            "name": artifact_name,
            "parts": [text_part(artifact_text)],
        })
    if history:
        task["history"] = list(history)
    if metadata:
        task["metadata"] = dict(metadata)
    return task


def status_event(
    task_id: str,
    context_id: str,
    state: str,
    *,
    text: str = "",
    final: bool = False,
) -> Dict[str, Any]:
    """A ``TaskStatusUpdateEvent``, the frame a streamed run reports progress in."""
    status: Dict[str, Any] = {"state": state, "timestamp": now_iso()}
    if text:
        status["message"] = build_message("agent", text, task_id=task_id, context_id=context_id)
    return {
        "taskId": task_id,
        "contextId": context_id,
        "kind": "status-update",
        "status": status,
        "final": bool(final),
    }


def artifact_event(
    task_id: str,
    context_id: str,
    text: str,
    *,
    name: str = "result",
    last_chunk: bool = True,
) -> Dict[str, Any]:
    """A ``TaskArtifactUpdateEvent`` carrying the finished answer."""
    return {
        "taskId": task_id,
        "contextId": context_id,
        "kind": "artifact-update",
        "artifact": {
            "artifactId": f"{task_id}-{name}",
            "name": name,
            "parts": [text_part(text)],
        },
        "append": False,
        "lastChunk": bool(last_chunk),
    }


def events_for_hub_frame(
    frame: Dict[str, Any],
    *,
    task_id: str,
    context_id: str,
) -> List[Dict[str, Any]]:
    """Translate one event off a run's session channel into A2A events.

    The hub's stream vocabulary is richer than A2A's: tokens, thinking, tool
    starts and ends, node transitions. A2A has one channel for "here is more of
    what the agent is saying", so the text-bearing frames become ``working``
    status updates and the rest are dropped rather than invented into artifacts
    a client would have to guess the meaning of.

    Returns a list because the closing frame produces two events: the artifact
    with the answer, then the final status update that ends the stream.
    """
    kind = str(frame.get("type") or "").strip()

    if kind == "token":
        text = str(frame.get("token") or frame.get("content") or "")
        return [status_event(task_id, context_id, WORKING, text=text)] if text else []

    if kind == "interrupt":
        question = str(frame.get("question") or "").strip()
        return [status_event(task_id, context_id, INPUT_REQUIRED, text=question, final=True)]

    if kind == "error":
        detail = str(frame.get("error") or frame.get("message") or "the run failed")
        return [status_event(task_id, context_id, FAILED, text=detail, final=True)]

    if kind in ("done", "session_done"):
        ok = frame.get("ok")
        output = str(frame.get("response") or frame.get("output") or "")
        if ok is False:
            detail = str(frame.get("error") or "the run failed")
            return [status_event(task_id, context_id, FAILED, text=detail, final=True)]
        events: List[Dict[str, Any]] = []
        if output:
            events.append(artifact_event(task_id, context_id, output))
        events.append(status_event(task_id, context_id, COMPLETED, final=True))
        return events

    return []


def sse_frame(payload: Dict[str, Any], request_id: Any) -> str:
    """One SSE frame: a JSON-RPC response envelope, as ``message/stream`` sends.

    Every frame of an A2A stream is a full JSON-RPC response carrying the same
    request id, not a bare event. A client correlating by id would otherwise
    have nothing to correlate on.
    """
    import json

    return "data: " + json.dumps(result_response(request_id, payload)) + "\n\n"


__all__ = [
    "JSONRPC_VERSION",
    "PARSE_ERROR",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "INVALID_PARAMS",
    "INTERNAL_ERROR",
    "TASK_NOT_FOUND",
    "TASK_NOT_CANCELABLE",
    "UNSUPPORTED_OPERATION",
    "METHODS",
    "SUBMITTED",
    "WORKING",
    "INPUT_REQUIRED",
    "COMPLETED",
    "CANCELED",
    "FAILED",
    "REJECTED",
    "AUTH_REQUIRED",
    "UNKNOWN",
    "TERMINAL_STATES",
    "JsonRpcError",
    "now_iso",
    "new_id",
    "result_response",
    "error_response",
    "error_from",
    "parse_request",
    "text_part",
    "message_text",
    "build_message",
    "require_message",
    "require_task_id",
    "blocking_requested",
    "metadata_of",
    "state_for",
    "is_terminal",
    "build_task",
    "status_event",
    "artifact_event",
    "events_for_hub_frame",
    "sse_frame",
]
