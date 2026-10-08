"""The hub as an MCP server (docs/hub-as-mcp-server.md).

``POST /v1/mcp`` speaks the Model Context Protocol's Streamable HTTP transport,
so Claude Code, Cursor and any other MCP client can call the hub's agents as
tools::

    claude mcp add --transport http agents-hub https://hub.example.com/v1/mcp \\
        --header "Authorization: Bearer ah_..."

The transport is the stateless form the specification allows: every request
is answered with one ``application/json`` body, no ``Mcp-Session-Id`` is
issued, and ``GET`` (the server's own event stream) is a 405. Nothing here
needs a session: the tools are calls, and a conversation with an agent is
carried by an opaque, signed ``conversation`` handle the client passes back.

It lives under ``/v1`` rather than at ``/mcp`` because ``/mcp`` is the
dashboard's own page for the MCP servers a workspace attaches: a GET there
must reach the app. Authentication is the middleware's, as for the rest of
``/v1`` (closed in ``common.auth.is_open_path`` although it sits outside
``/api``), so a handler always has ``request.state.principal`` (a personal API key's owner, the shared
token's principal, a session's user, the local operator in ``single`` mode).
The per-minute rate limit applies the same way, and ``ask_agent`` checks the
caller's tokens per day and a key's money cap before an agent starts, like a
``/v1`` completion does.

The tools are the same for every client; nothing here knows which one calls.
This module keeps the agent ones; files, knowledge, workflows and runs are in
``routes/mcp_server_tools.py``:

* ``list_workspaces``: the workspaces the caller can reach.
* ``list_agents``: the agents runnable in one workspace (the chat's rule,
  ``widgets.agents``), or in any the caller reaches.
* ``ask_agent``: one turn of an agent, through the web chat's pipeline
  (``widgets.relay.TurnRelay``), as a run with ``message_origin="mcp"``. Its
  tools, memory, guardrails and budget apply as in the chat; ``mcp`` counts as
  an untrusted channel (``tools.capabilities.UNTRUSTED_CHANNELS``), because
  what an IDE agent forwards may come from any file it read. The workspace and
  role checks are ``/v1``'s own (``openai_compat._agent_workspace``).
  ``file_ids`` attach workspace files (``upload_file``) to the turn.
* ``list_files``, ``read_file``, ``upload_file``, ``list_knowledge``,
  ``search_knowledge``, ``list_workflows``, ``run_workflow``, ``list_runs``,
  ``get_run``, ``stop_run``: see ``routes/mcp_server_tools.py``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from common import identity
from routes import mcp_server_tools as hub_tools
from routes.mcp_server_tools import Ctx, ToolError, ToolFn, check_budget, workspace_names

log = logging.getLogger(__name__)

router = APIRouter(tags=["mcp-server"])

#: Protocol revisions this server answers in, newest first. A client asking
#: for one of them gets it; anything else gets the newest.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "agents-hub"

#: The run origin of an MCP turn (chat/runs.py ``message_origin``).
ORIGIN = "mcp"

#: How long a conversation handle stays usable.
CONVERSATION_TTL_SECONDS = 30 * 24 * 3600
_CONVERSATION_PURPOSE = "mcp-conversation"

#: ask_agent waits at most this long by default before handing back the run
#: id; a client may ask for less or more, up to the chat's own turn timeout.
DEFAULT_WAIT_SECONDS = 600

# JSON-RPC error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

INSTRUCTIONS = (
    "Agents Hub runs AI agents with their own tools, memory and knowledge. "
    "Call list_agents to see who is available, then ask_agent with one "
    "self-contained message. Pass the returned conversation back to continue "
    "the same conversation. To give an agent a file, upload_file it and pass "
    "the file_id in file_ids. search_knowledge and read_file answer from the "
    "workspace's own documents and files without asking an agent. Teams, flows "
    "and loops (list_workflows) run with run_workflow. Work that takes longer "
    "than wait_seconds returns a run_id; call get_run with it later.")


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ── Tools ────────────────────────────────────────────────────────────────────

async def _list_workspaces(ctx: Ctx, args: Dict[str, Any]) -> Dict[str, Any]:
    return {"workspaces": workspace_names(ctx.principal)}


async def _list_agents(ctx: Ctx, args: Dict[str, Any]) -> Dict[str, Any]:
    from routes import openai_compat as v1
    from widgets.agents import usable_agents, usable_in_any

    workspace = str(args.get("workspace") or "").strip() or v1._header_workspace(ctx.request)
    if workspace:
        if workspace not in workspace_names(ctx.principal):
            raise ToolError(f"workspace '{workspace}' is not reachable with this credential")
        specs = usable_agents(workspace)
    else:
        specs = usable_in_any(workspace_names(ctx.principal))
    agents = [{"agent_id": s.id, "name": s.name or s.id,
               "description": getattr(s, "description", "") or ""}
              for s in sorted(specs, key=lambda s: s.id)]
    return {"workspace": workspace or None, "agents": agents}


def _conversation_handle(conversation_id: str, *, agent_id: str, workspace: str,
                         principal: Any) -> str:
    from common.signed_state import sign_state
    return sign_state({"purpose": _CONVERSATION_PURPOSE, "c": conversation_id,
                       "a": agent_id, "w": workspace, "u": getattr(principal, "id", None),
                       "exp": time.time() + CONVERSATION_TTL_SECONDS})


def _read_conversation(handle: str, *, agent_id: str, workspace: str, principal: Any) -> str:
    """The conversation id behind a handle this server issued to this caller,
    for this agent in this workspace. A handle cannot be forged, borrowed by
    another account, or moved to another agent or workspace."""
    from common.signed_state import StateError, read_state
    try:
        payload = read_state(handle)
    except StateError as exc:
        raise ToolError(f"conversation is not valid ({exc}); omit it to start a new one") from exc
    if (payload.get("purpose") != _CONVERSATION_PURPOSE
            or payload.get("u") != getattr(principal, "id", None)):
        raise ToolError("conversation was not issued to this caller; omit it to start a new one")
    if payload.get("a") != agent_id or payload.get("w") != workspace:
        raise ToolError("conversation belongs to another agent or workspace; omit it to start a new one")
    return str(payload.get("c") or "")


def _wait_seconds(value: Any) -> float:
    from routes import openai_compat as v1
    ceiling = v1._agent_timeout()
    if value is None:
        return min(float(DEFAULT_WAIT_SECONDS), ceiling)
    try:
        wait = float(value)
    except (TypeError, ValueError):
        raise ToolError("wait_seconds must be a number") from None
    return max(5.0, min(wait, ceiling))


def _attachments(file_ids: List[str], workspace: str) -> List[Any]:
    """Workspace files for a turn, by id. A file of another workspace, or one
    that does not exist, is refused here with its id rather than dropped by
    the pipeline later."""
    from chat.models import ChatAttachment
    from files import service
    out = []
    for file_id in dict.fromkeys(f.strip() for f in file_ids if f.strip()):
        record = service.get_file(file_id)
        if record is None or record["workspace"] != workspace:
            raise ToolError(f"no file '{file_id}' in workspace '{workspace}'; "
                            "upload_file puts one there")
        out.append(ChatAttachment(file_id=file_id, filename=record["name"]))
    return out


async def _ask_agent(ctx: Ctx, args: Dict[str, Any]) -> Dict[str, Any]:
    from chat.models import ChatRequest
    from chat.runs import build_conversation_history
    from routes import openai_compat as v1
    from widgets.relay import TurnRelay

    principal = ctx.principal
    agent_id = str(args.get("agent_id") or "").strip()
    message = str(args.get("message") or "").strip()
    if not agent_id:
        raise ToolError("agent_id is required; call list_agents to see the ids")
    if not message:
        raise ToolError("message is required")
    check_budget(principal)
    try:
        spec = v1._agent_spec(v1.AGENT_PREFIX + agent_id)
        workspace = v1._agent_workspace(ctx.request, principal, spec,
                                        named=str(args.get("workspace") or ""))
    except v1._BadRequest as exc:
        raise ToolError(exc.message) from exc
    context = str(args.get("context") or "").strip()
    if context:
        message = "\n\n".join(["Context from the caller:", context, "---", message])

    file_ids = args.get("file_ids") or []
    if not isinstance(file_ids, list) or not all(isinstance(f, str) for f in file_ids):
        raise ToolError("file_ids must be a list of file ids")
    attachments = _attachments(file_ids, workspace)

    handle = str(args.get("conversation") or "").strip()
    conversation_id = (_read_conversation(handle, agent_id=spec.id, workspace=workspace,
                                          principal=principal) if handle else None)
    history = build_conversation_history(conversation_id) if conversation_id else []
    conversation_id = conversation_id or str(uuid.uuid4())
    wait = _wait_seconds(args.get("wait_seconds"))

    chat_request = ChatRequest(
        agent_id=spec.id, message=message, workspace=workspace, history=history,
        conversation_id=conversation_id, conversation_title=message[:60], source=ORIGIN,
        attachments=attachments)
    key_id = (getattr(principal, "credential_id", None)
              if getattr(principal, "via", "") == "api_key" else None)
    relay = TurnRelay(chat_request, user_id=getattr(principal, "id", None), key_id=key_id).start()
    started = time.monotonic()
    handoff_texts: List[str] = []
    handoff_runs: List[str] = []

    async def drain() -> Optional[Dict[str, Any]]:
        async for event in relay.events():
            if event.get("type") == "handoff":
                handoff_texts.append(str(event.get("from_response") or ""))
                if event.get("run_id"):
                    handoff_runs.append(str(event["run_id"]))
        return relay.done

    conversation = _conversation_handle(conversation_id, agent_id=spec.id, workspace=workspace,
                                        principal=principal)
    served = lambda run_id: v1._agent_served(spec, run_id)  # noqa: E731 - read twice below

    try:
        done = await asyncio.wait_for(drain(), timeout=wait)
    except (asyncio.TimeoutError, TimeoutError):
        # The turn keeps running on the relay's own task; the caller picks the
        # answer up with get_run.
        return {"status": "running", "agent_id": spec.id, "workspace": workspace,
                "run_id": relay.run_id, "conversation": conversation,
                "note": f"the agent is still working after {int(wait)}s; call get_run with this run_id"}
    done = done or {}
    prompt_text = "\n".join([*(h.content for h in history), message])
    if not done.get("ok"):
        detail = str(done.get("error") or "the agent could not answer")
        v1._account(principal, served(done.get("run_id") or relay.run_id),
                    v1._turn_usage(done, handoff_runs, prompt_text, ""), started=started,
                    stream=False, status="error", error=detail, tools=0, object_type="agent",
                    workspace=workspace, path="/v1/mcp",
                    details={"run_id": done.get("run_id"), "agent_id": spec.id, "via": ORIGIN})
        raise ToolError(detail)
    text = "\n\n".join(t for t in [*handoff_texts, str(done.get("response") or "")] if t.strip())
    usage = v1._turn_usage(done, handoff_runs, prompt_text, text)
    v1._account(principal, served(done.get("run_id")), usage, started=started, stream=False,
                status="ok", error=None, tools=0, object_type="agent", workspace=workspace,
                path="/v1/mcp", details={"run_id": done.get("run_id"), "agent_id": spec.id,
                                      "via": ORIGIN})
    return {"status": "completed", "answer": text, "agent_id": spec.id,
            "answered_by": done.get("agent_id") or spec.id, "workspace": workspace,
            "run_id": done.get("run_id"), "conversation": conversation, "usage": usage}


_WORKSPACE_ARG = {"type": "string",
                  "description": "Workspace to run in. Defaults to the X-Agents-Hub-Workspace "
                                 "header, the API key's only workspace, or the agent's own."}

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "list_workspaces",
        "title": "List workspaces",
        "description": "The Agents Hub workspaces this credential can reach.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "list_agents",
        "title": "List agents",
        "description": "The agents you can ask, with what each one is for. Without a "
                       "workspace, every agent runnable in some reachable workspace.",
        "inputSchema": {"type": "object", "properties": {"workspace": _WORKSPACE_ARG},
                        "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "ask_agent",
        "title": "Ask an agent",
        "description": "Send one message to an Agents Hub agent and get its answer. The agent "
                       "runs on the hub with its own tools, memory and knowledge. Put "
                       "everything it needs in the message or context: it cannot see your "
                       "local files, so pass them as context or upload_file them and give "
                       "file_ids. Returns a conversation handle to continue the conversation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "An id from list_agents."},
                "message": {"type": "string", "description": "What to ask or do."},
                "context": {"type": "string",
                            "description": "Optional material the agent should read: code, "
                                           "a diff, an error, notes."},
                "workspace": _WORKSPACE_ARG,
                "conversation": {"type": "string",
                                 "description": "The conversation value of an earlier answer, "
                                                "to continue that conversation."},
                "file_ids": {"type": "array", "items": {"type": "string"},
                             "description": "Workspace files the agent should read, by id "
                                            "(from upload_file or list_files)."},
                "wait_seconds": {"type": "number",
                                 "description": "How long to wait for the answer before "
                                                "returning a run_id instead (default 600)."},
            },
            "required": ["agent_id", "message"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True},
    },
    *hub_tools.TOOLS,
]

_HANDLERS: Dict[str, ToolFn] = {
    "list_workspaces": _list_workspaces,
    "list_agents": _list_agents,
    "ask_agent": _ask_agent,
    **hub_tools.HANDLERS,
}


def _tool_text(name: str, result: Dict[str, Any]) -> str:
    """The text an MCP client shows its model: the answer itself for a
    finished turn (with the handle on a line of its own), a file's text as
    it is, JSON otherwise."""
    if name == "ask_agent" and result.get("status") == "completed":
        return (f"{result.get('answer') or '(no answer)'}\n\n"
                f"[agent {result.get('answered_by')}, run {result.get('run_id')}; "
                f"conversation: {result.get('conversation')}]")
    if name == "read_file":
        end = result["offset"] + len(result["text"])
        more = f"; next_offset: {result['next_offset']}" if result.get("truncated") else ""
        return (f"{result['text']}\n\n[file {result.get('name')} ({result.get('file_id')}), "
                f"characters {result['offset']}..{end} of {result['total_chars']}{more}]")
    return json.dumps(result, ensure_ascii=False, indent=2)


async def _call_tool(ctx: Ctx, params: Dict[str, Any]) -> Dict[str, Any]:
    name = params.get("name")
    args = params.get("arguments") or {}
    if not isinstance(name, str) or name not in _HANDLERS:
        raise _RpcError(INVALID_PARAMS, f"unknown tool: {name!r}")
    if not isinstance(args, dict):
        raise _RpcError(INVALID_PARAMS, "arguments must be an object")
    try:
        result = await _HANDLERS[name](ctx, args)
    except ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except Exception as exc:  # noqa: BLE001 - one tool failing is a failed result, not a dead server
        log.warning("mcp server: tool %s failed", name, exc_info=True)
        return {"content": [{"type": "text", "text": f"{name} failed: {exc}"}], "isError": True}
    return {"content": [{"type": "text", "text": _tool_text(name, result)}],
            "structuredContent": result, "isError": False}


# ── JSON-RPC ─────────────────────────────────────────────────────────────────

def _negotiate(requested: Any) -> str:
    return requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]


async def handle_message(ctx: Ctx, message: Any) -> Optional[Dict[str, Any]]:
    """One JSON-RPC message in, its response out, or None for a notification
    or a client's response (neither is answered)."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _rpc_error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 message")
    msg_id = message.get("id")
    method = message.get("method")
    if method is None:
        return None  # a response to something we never ask
    if "id" not in message:
        return None  # notifications/initialized, notifications/cancelled, ...
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _rpc_error(msg_id, INVALID_PARAMS, "params must be an object")
    try:
        if method == "initialize":
            from common.version import app_version
            result: Dict[str, Any] = {
                "protocolVersion": _negotiate(params.get("protocolVersion")),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "title": "Agents Hub",
                               "version": app_version()},
                "instructions": INSTRUCTIONS,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            result = await _call_tool(ctx, params)
        elif method in ("resources/list", "resources/templates/list"):
            result = {"resources": []} if method == "resources/list" else {"resourceTemplates": []}
        elif method == "prompts/list":
            result = {"prompts": []}
        else:
            raise _RpcError(METHOD_NOT_FOUND, f"method not found: {method}")
    except _RpcError as exc:
        return _rpc_error(msg_id, exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001 - the client gets an error, the server stays up
        log.warning("mcp server: %s failed", method, exc_info=True)
        return _rpc_error(msg_id, INTERNAL_ERROR, str(exc) or exc.__class__.__name__)
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


# ── HTTP ─────────────────────────────────────────────────────────────────────

@router.post("/v1/mcp")
async def mcp_post(request: Request):
    principal = identity.request_principal(request)
    if principal is None:
        return JSONResponse(status_code=401, content={"detail": "Invalid or missing API token"})
    try:
        body = json.loads(await request.body() or b"null")
    except ValueError:
        return JSONResponse(_rpc_error(None, PARSE_ERROR, "the body is not valid JSON"),
                            status_code=400)
    ctx = Ctx(request, principal)
    if isinstance(body, list):
        if not body:
            return JSONResponse(_rpc_error(None, INVALID_REQUEST, "empty batch"), status_code=400)
        answers = [a for a in [await handle_message(ctx, m) for m in body] if a is not None]
        return JSONResponse(answers) if answers else Response(status_code=202)
    answer = await handle_message(ctx, body)
    if answer is None:
        return Response(status_code=202)
    return JSONResponse(answer)


@router.get("/v1/mcp")
async def mcp_get():
    # No server-initiated stream: this server never sends anything unasked.
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("/v1/mcp")
async def mcp_delete():
    # Stateless: there is no session to end.
    return Response(status_code=405, headers={"Allow": "POST"})


__all__ = ["router", "handle_message", "TOOLS", "PROTOCOL_VERSIONS"]
