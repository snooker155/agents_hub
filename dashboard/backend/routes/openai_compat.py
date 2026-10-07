"""The hub as an OpenAI-compatible provider (docs/hub-as-provider.md).

``/v1/models`` lists every model the catalog enables (plus the global
default) and ``/v1/chat/completions`` answers with any of them through the
same ``build_chat_model`` the agents use, so an external client (an IDE
plugin, a script on the OpenAI SDK, another agent framework) reaches every
provider the hub is configured for through one address and one credential.

Authentication is the middleware's, not this module's: ``/v1`` is closed in
``common.auth.is_open_path`` although it sits outside ``/api``, so by the
time a handler runs ``request.state.principal`` is set (the local operator in
``single`` mode, the shared token's principal in ``token`` mode, a personal
API key's owner or a session's user in ``multi`` mode).

Every completion writes a ``serving_usage`` row (``common/serving.py``) and an
audit row (``model.serve``). Neither may break the response: both swallow
their own failures.

Errors use the OpenAI shape, ``{"error": {"message", "type", "param",
"code"}}``, because that is what the OpenAI SDKs parse into an exception
with a readable message. The one exception is the middleware's own 401,
which says ``{"detail": ...}``: an SDK still raises its authentication error
on the status code alone.

Agents are models too: ``agent:<agent_id>``. ``/v1/models`` lists the agents
the caller may run (the chat's visibility rule per workspace,
``widgets.agents``, over the workspaces a personal key reaches), and a
completion with such a model runs the agent through the web chat's own
pipeline (``chat.pipelines.run_chat_pipeline``, driven by
``widgets.relay.TurnRelay``) as a run with ``message_origin="api"``: its
tools, memory, guardrails and budget apply exactly as in the chat, and the
run shows on the Messages page. The workspace comes from the
``X-Agents-Hub-Workspace`` header, else the key's one workspace, else the
agent's owner workspace, else ``default``; the caller needs the editor role
there (``multi`` mode). Client ``tools`` are refused: an agent runs its own.
Two hub extensions of the body pin and shape that one run: ``agent_version``
(a stored version of the agent, docs/agent-versions.md) and ``overrides``
(model, system, tools, skills, mcp, tool_policy, output_schema,
agents/run_overrides.py); both are echoed under ``agents_hub``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from agents.agent_utils import build_chat_model
from common import identity
from common.config import settings
from routes import models as models_routes

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["openai-compat"])
# Usage of the served endpoint, for the Models page.
serving_router = APIRouter(prefix="/api/models/serving", tags=["serving"])

#: The alias a client may send instead of naming the global default.
DEFAULT_ALIAS = "default"

#: An agent served as a model: ``agent:<agent_id>``.
AGENT_PREFIX = "agent:"
#: ``owned_by`` of an agent model, so a client can tell agents from the
#: catalog's models without parsing ids.
AGENT_OWNER = "agents-hub"
#: The workspace an agent model runs in, when the caller names one.
WORKSPACE_HEADER = "x-agents-hub-workspace"

_ENDPOINTS = ["GET /v1/models", "GET /v1/models/{id}", "POST /v1/chat/completions"]


# ── Errors ───────────────────────────────────────────────────────────────────

def _error(status: int, message: str, *, type_: str = "invalid_request_error",
           code: Optional[str] = None, param: Optional[str] = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {
        "message": message, "type": type_, "param": param, "code": code}})


class _BadRequest(Exception):
    """A request the client has to change; becomes a 400 in the OpenAI shape."""

    def __init__(self, message: str, *, param: Optional[str] = None,
                 status: int = 400, code: Optional[str] = None,
                 type_: str = "invalid_request_error") -> None:
        super().__init__(message)
        self.message = message
        self.param = param
        self.status = status
        self.code = code
        self.type_ = type_

    def response(self) -> JSONResponse:
        return _error(self.status, self.message, type_=self.type_, param=self.param,
                      code=self.code)


# ── The served models ────────────────────────────────────────────────────────

def _served() -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Every served model as an OpenAI model object, and the global default's
    object (None when .env names no default model).

    The catalog's enabled models, in catalog order, plus the global default
    when it is not among them: the runtime falls back to it everywhere else,
    so a client may use it too.
    """
    catalog = models_routes._load_catalog()
    served: List[Dict[str, Any]] = []
    seen = set()
    for provider, entry in catalog.items():
        for m in (entry or {}).get("models", []) or []:
            model_id = str(m.get("id") or "").strip()
            if not model_id or not m.get("enabled"):
                continue
            key = f"{provider}/{model_id}"
            if key in seen:
                continue
            seen.add(key)
            served.append(_model_object(provider, model_id, m))
    default = None
    try:
        gd = models_routes._global_default()
    except Exception:  # noqa: BLE001 - an unreadable .env just means no default
        gd = {}
    if gd.get("provider") and gd.get("model"):
        key = f"{gd['provider']}/{gd['model']}"
        default = next((o for o in served if o["id"] == key), None)
        if default is None:
            default = _model_object(gd["provider"], gd["model"], {})
            served.append(default)
    return served, default


def _model_object(provider: str, model_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": f"{provider}/{model_id}",
        "object": "model",
        "owned_by": provider,
        "created": int(record.get("released_at") or 0),
        "context_window": int(record.get("context_window") or 0),
        # Kept apart so a caller never has to split the id to learn them.
        "provider": provider,
        "model": model_id,
    }


def _resolve(name: Any) -> Dict[str, Any]:
    """The served catalog model a client's ``model`` names. Raises
    :class:`_BadRequest`. (``agent:<id>`` never gets here: see
    :func:`_agent_completion`.)

    ``<provider>/<model>`` first; then a bare id among every served model
    (ambiguous when two providers serve the same id); ``default`` is the
    global default. A model id that itself contains a slash (an OpenRouter
    style ``meta-llama/llama-3``) still resolves: when the part before the
    first slash is not a provider, the whole string is looked up as a bare id.
    """
    name = name.strip() if isinstance(name, str) else ""
    if not name:
        raise _BadRequest("you must provide a model parameter", param="model")
    served, default = _served()
    if name == DEFAULT_ALIAS:
        if default is None:
            raise _not_found(name)
        return default
    by_id = {o["id"]: o for o in served}
    if name in by_id:
        return by_id[name]
    providers = {o["provider"] for o in served}
    head = name.split("/", 1)[0]
    if "/" in name and head in providers:
        raise _not_found(name)
    candidates = [o for o in served if o["model"] == name]
    if not candidates:
        raise _not_found(name)
    if len(candidates) > 1:
        ids = ", ".join(sorted(o["id"] for o in candidates))
        raise _BadRequest(
            f"The model '{name}' is served by more than one provider; name one of: {ids}",
            param="model", code="model_ambiguous")
    return candidates[0]


def _not_found(name: str) -> _BadRequest:
    return _BadRequest(
        f"The model '{name}' does not exist or is not enabled in the hub's catalog",
        param="model", status=404, code="model_not_found")


# ── Messages in ──────────────────────────────────────────────────────────────

def _text_of(content: Any, *, param: str) -> str:
    """A message's content as text: a string, or a list of text parts. Any
    other part (an image, audio, a file) is refused: the hub does not relay
    them yet, and silently dropping one would answer a different question."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces = []
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
                continue
            kind = (part or {}).get("type") if isinstance(part, dict) else None
            if kind == "text":
                pieces.append(str(part.get("text") or ""))
            else:
                raise _BadRequest(
                    f"content parts of type '{kind}' are not supported, only text", param=param)
        return "".join(pieces)
    raise _BadRequest("content must be a string or a list of text parts", param=param)


def _to_langchain(messages: Any) -> List[Any]:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    if not isinstance(messages, list) or not messages:
        raise _BadRequest("messages must be a non-empty list", param="messages")
    out: List[Any] = []
    for i, msg in enumerate(messages):
        param = f"messages[{i}]"
        if not isinstance(msg, dict):
            raise _BadRequest("each message must be an object", param=param)
        role = msg.get("role")
        text = _text_of(msg.get("content"), param=f"{param}.content")
        if role in ("system", "developer"):
            out.append(SystemMessage(content=text))
        elif role == "user":
            out.append(HumanMessage(content=text))
        elif role == "assistant":
            calls = []
            for j, call in enumerate(msg.get("tool_calls") or []):
                fn = (call or {}).get("function") or {}
                raw = fn.get("arguments")
                try:
                    args = json.loads(raw) if isinstance(raw, str) and raw.strip() else (raw or {})
                except ValueError:
                    raise _BadRequest("tool call arguments must be a JSON string",
                                      param=f"{param}.tool_calls[{j}].function.arguments")
                if not isinstance(args, dict):
                    args = {"value": args}
                calls.append({"id": call.get("id") or f"call_{secrets.token_hex(8)}",
                              "name": str(fn.get("name") or ""), "args": args,
                              "type": "tool_call"})
            out.append(AIMessage(content=text, tool_calls=calls))
        elif role == "tool":
            call_id = msg.get("tool_call_id")
            if not call_id:
                raise _BadRequest("a tool message needs tool_call_id", param=f"{param}.tool_call_id")
            out.append(ToolMessage(content=text, tool_call_id=str(call_id)))
        else:
            raise _BadRequest(f"unsupported role '{role}'", param=f"{param}.role")
    return out


def _tool_choice(value: Any) -> Any:
    """OpenAI's tool_choice in the spelling LangChain's ``bind_tools`` takes."""
    if value in (None, "auto"):
        return None
    if value == "required":
        return "any"
    if isinstance(value, dict):
        name = ((value.get("function") or {}).get("name")) if value.get("type") == "function" else None
        if name:
            return str(name)
    return None


def _json_instruction(fmt: Any) -> Optional[str]:
    """``response_format`` as an instruction. Best effort on purpose: not every
    provider has a JSON mode, but every one reads a system message."""
    if not isinstance(fmt, dict):
        return None
    kind = fmt.get("type")
    if kind == "json_object":
        return "Respond with a single valid JSON object and nothing else."
    if kind == "json_schema":
        schema = (fmt.get("json_schema") or {}).get("schema")
        return ("Respond with a single valid JSON object and nothing else, matching this "
                f"JSON schema: {json.dumps(schema, ensure_ascii=False)}")
    return None


# ── Messages out ─────────────────────────────────────────────────────────────

def _content_text(content: Any) -> str:
    """The text of a LangChain message or chunk: a string, or the ``text``
    blocks of a list (Anthropic's shape; thinking and tool-use blocks are not
    the answer)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part if isinstance(part, str) else str(part.get("text") or "")
            for part in content
            if isinstance(part, str) or (isinstance(part, dict) and part.get("type") == "text"))
    return ""


def _openai_tool_calls(message: Any) -> List[Dict[str, Any]]:
    return [{
        "id": call.get("id") or f"call_{secrets.token_hex(8)}",
        "type": "function",
        "function": {"name": call.get("name") or "",
                     "arguments": json.dumps(call.get("args") or {}, ensure_ascii=False)},
    } for call in (getattr(message, "tool_calls", None) or [])]


def _finish_reason(message: Any, has_tool_calls: bool) -> str:
    if has_tool_calls:
        return "tool_calls"
    meta = getattr(message, "response_metadata", None) or {}
    reason = str(meta.get("finish_reason") or meta.get("stop_reason") or meta.get("done_reason") or "")
    if reason in ("length", "max_tokens"):
        return "length"
    if reason == "content_filter":
        return "content_filter"
    return "stop"


def _reported_usage(message: Any) -> Optional[Tuple[int, int]]:
    """(prompt, completion) as the provider reported them, or None."""
    um = getattr(message, "usage_metadata", None) or None
    if um and (um.get("input_tokens") or um.get("output_tokens")):
        return int(um.get("input_tokens") or 0), int(um.get("output_tokens") or 0)
    meta = getattr(message, "response_metadata", None) or {}
    tu = meta.get("token_usage") or meta.get("usage") or None
    if isinstance(tu, dict):
        prompt = tu.get("prompt_tokens", tu.get("input_tokens"))
        completion = tu.get("completion_tokens", tu.get("output_tokens"))
        if prompt or completion:
            return int(prompt or 0), int(completion or 0)
    return None


def _prompt_text(messages: List[Any]) -> str:
    parts = []
    for m in messages:
        parts.append(_content_text(m.content))
        for call in getattr(m, "tool_calls", None) or []:
            parts.append(json.dumps(call.get("args") or {}))
    return "\n".join(parts)


def _usage_for(message: Any, messages: List[Any], completion_text: str) -> Dict[str, Any]:
    from common.serving import estimate_tokens
    reported = _reported_usage(message) if message is not None else None
    if reported is not None:
        prompt, completion = reported
        return {"prompt_tokens": prompt, "completion_tokens": completion,
                "total_tokens": prompt + completion}
    prompt = estimate_tokens(_prompt_text(messages))
    completion = estimate_tokens(completion_text)
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion, "estimated": True}


# ── Accounting ───────────────────────────────────────────────────────────────

def _account(principal: Any, served: Dict[str, Any], usage: Dict[str, Any], *,
             started: float, stream: bool, status: str, error: Optional[str],
             tools: int, object_type: str = "model", workspace: Optional[str] = None,
             details: Optional[Dict[str, Any]] = None) -> None:
    """One usage row and one audit row. Neither may break the response.

    An agent model is counted under the model its run actually used (so the
    Models page's per-model totals and the tokens-per-day cap include it) and
    audited as ``object_type="agent"`` with its run id in ``details``."""
    duration_ms = int((time.monotonic() - started) * 1000)
    try:
        from common import serving
        serving.record_usage(
            principal, provider=served["provider"], model=served["model"],
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            duration_ms=duration_ms, stream=stream, status=status, error=error,
            estimated=bool(usage.get("estimated")))
    except Exception:  # noqa: BLE001 - accounting must not break the completion
        log.warning("openai_compat: usage not recorded", exc_info=True)
    try:
        from common import audit
        audit.record(
            "model.serve", principal=principal, object_type=object_type, object_id=served["id"],
            workspace=workspace, method="POST", path="/v1/chat/completions",
            result="ok" if status == "ok" else "error",
            details={"prompt_tokens": usage.get("prompt_tokens", 0),
                     "completion_tokens": usage.get("completion_tokens", 0),
                     "stream": stream, "duration_ms": duration_ms, "tools": tools,
                     **(details or {}),
                     **({"error": str(error)[:500]} if error else {})})
    except Exception:  # noqa: BLE001 - an audit failure must not break the completion
        log.warning("openai_compat: audit not recorded", exc_info=True)


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("")
async def index():
    """What this address serves, for a person pointing a browser at it."""
    return {"object": "api", "endpoints": _ENDPOINTS}


@router.get("/models")
async def list_models(request: Request):
    """The catalog's models, then the agents this caller may run."""
    served, _ = _served()
    try:
        agents = _listed_agents(request, identity.request_principal(request))
    except _BadRequest as exc:
        return exc.response()
    return {"object": "list", "data": served + agents}


@router.get("/models/{model_id:path}")
async def get_model(model_id: str, request: Request):
    try:
        if model_id.startswith(AGENT_PREFIX):
            principal = identity.request_principal(request)
            spec = _agent_spec(model_id)
            _agent_workspace(request, principal, spec)
            return _agent_object(spec)
        return _resolve(model_id)
    except _BadRequest as exc:
        return exc.response()


# ── Agents as models ─────────────────────────────────────────────────────────

def _agent_object(spec: Any) -> Dict[str, Any]:
    return {
        "id": AGENT_PREFIX + spec.id,
        "object": "model",
        "owned_by": AGENT_OWNER,
        "created": 0,
        "agent_id": spec.id,
        "name": spec.name or spec.id,
        "description": getattr(spec, "description", "") or "",
    }


def _header_workspace(request: Request) -> str:
    return (request.headers.get(WORKSPACE_HEADER) or "").strip()


def _permission_error(message: str, code: str = "workspace_not_allowed") -> _BadRequest:
    return _BadRequest(message, status=403, code=code, type_="permission_error")


def _listed_agents(request: Request, principal: Any) -> List[Dict[str, Any]]:
    """The agents ``/v1/models`` offers: those usable in the named workspace
    (``X-Agents-Hub-Workspace``), else in any workspace the caller can see,
    a personal key's scope included."""
    from common import access
    from widgets.agents import usable_agents, usable_in_any

    header = _header_workspace(request)
    if header:
        if principal is not None and not principal.reaches(header):
            raise _permission_error(f"this API key does not reach workspace '{header}'")
        if not access.can_see_workspace(principal, header):
            raise _permission_error(f"workspace '{header}' is not visible to this account")
        specs = usable_agents(header)
    else:
        specs = usable_in_any(access.visible_workspaces(principal))
    return [_agent_object(s) for s in sorted(specs, key=lambda s: s.id)]


def _agent_spec(model: str) -> Any:
    from agents import registry
    agent_id = model[len(AGENT_PREFIX):].strip()
    spec = registry.get_agent(agent_id) if agent_id else None
    if spec is None:
        raise _BadRequest(f"The model '{model}' does not exist: no agent '{agent_id}'",
                          param="model", status=404, code="model_not_found")
    return spec


def _agent_workspace(request: Request, principal: Any, spec: Any) -> str:
    """Where an agent model runs, and whether this caller may run it there.

    The header, else a key's one workspace, else the agent's owner workspace,
    else ``default``. A workspace outside a key's scope, one the caller is not
    an editor of (``multi``), one that does not exist, or one the agent is
    not available in, is refused before anything runs.
    """
    from fastapi import HTTPException
    from common.auth import WS_EDITOR
    from widgets.agents import usable_in

    workspace = _header_workspace(request)
    scope = getattr(principal, "scope", None)
    if not workspace and scope is not None and len(scope) == 1:
        workspace = scope[0]
    if not workspace:
        workspace = getattr(spec, "owner_workspace", None) or "default"
    if principal is not None and not principal.reaches(workspace):
        raise _permission_error(
            f"this API key does not reach workspace '{workspace}'; name one it does in "
            "the X-Agents-Hub-Workspace header")
    try:
        identity.require_role(principal, workspace=workspace, role=WS_EDITOR)
    except HTTPException as exc:
        raise _permission_error(str(exc.detail))
    if workspace != "default":
        try:
            from workspace import get_workspace_folder
            exists = get_workspace_folder(workspace) is not None
        except Exception:  # noqa: BLE001 - an invalid name reads as a missing workspace
            exists = False
        if not exists:
            raise _BadRequest(f"workspace '{workspace}' does not exist", status=404,
                              code="workspace_not_found")
    if not usable_in(spec, workspace):
        raise _BadRequest(
            f"The model '{AGENT_PREFIX}{spec.id}' is not available in workspace '{workspace}'",
            param="model", status=404, code="model_not_found")
    return workspace


_CALLER_INSTRUCTIONS = "Instructions from the caller (system message):"


def _agent_turn(messages: Any, instruction: Optional[str]) -> Tuple[str, List[Dict[str, str]]]:
    """``(message, history)`` for a ChatRequest from OpenAI messages.

    System and developer messages are the caller's instructions: they are
    folded into this turn's message, above the user's text, because the
    agent's own system prompt stays the agent's. ``user`` and ``assistant``
    messages before the last one are the history; the last one must be the
    user's. A tool message, or an assistant message carrying tool calls,
    belongs to a client that runs tools itself, which an agent model does not
    take: its own tools run on the hub.
    """
    if not isinstance(messages, list) or not messages:
        raise _BadRequest("messages must be a non-empty list", param="messages")
    instructions: List[str] = []
    turns: List[Tuple[str, str]] = []
    for i, msg in enumerate(messages):
        param = f"messages[{i}]"
        if not isinstance(msg, dict):
            raise _BadRequest("each message must be an object", param=param)
        role = msg.get("role")
        text = _text_of(msg.get("content"), param=f"{param}.content")
        if role in ("system", "developer"):
            if text.strip():
                instructions.append(text.strip())
        elif role == "user":
            turns.append(("user", text))
        elif role == "assistant":
            if msg.get("tool_calls"):
                raise _BadRequest("an agent model runs its own tools; assistant tool_calls "
                                  "are not accepted", param=f"{param}.tool_calls")
            turns.append(("agent", text))
        elif role == "tool":
            raise _BadRequest("an agent model runs its own tools; tool messages are not "
                              "accepted", param=f"{param}.role")
        else:
            raise _BadRequest(f"unsupported role '{role}'", param=f"{param}.role")
    if not turns or turns[-1][0] != "user":
        raise _BadRequest("the last message must be the user's", param="messages")
    if instruction:
        instructions.append(instruction)
    message = turns[-1][1]
    if instructions:
        message = "\n\n".join([_CALLER_INSTRUCTIONS, *instructions, "---", message])
    history = [{"role": role, "content": text} for role, text in turns[:-1] if text.strip()]
    return message, history


def _turn_usage(done: Dict[str, Any], handoff_runs: List[str], prompt: str,
                completion: str) -> Dict[str, Any]:
    """The turn's tokens as OpenAI ``usage``: the answering run's from the
    ``done`` event, plus the first agent's from its run record when a handoff
    split the turn. Estimated from the text when no run reported any."""
    from common.serving import estimate_tokens
    usage = done.get("usage") or {}
    inbound = int(usage.get("inbound_tokens") or 0)
    outbound = int(usage.get("outbound_tokens") or 0)
    for run_id in handoff_runs:
        try:
            from managers.run_manager import get_run_process
            earlier = (get_run_process(run_id) or {}).get("token_usage") or {}
        except Exception:  # noqa: BLE001 - a missing record adds nothing, never fails the reply
            earlier = {}
        inbound += int(earlier.get("inbound_tokens") or 0)
        outbound += int(earlier.get("outbound_tokens") or 0)
    if inbound or outbound:
        return {"prompt_tokens": inbound, "completion_tokens": outbound,
                "total_tokens": inbound + outbound}
    p, c = estimate_tokens(prompt), estimate_tokens(completion)
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c, "estimated": True}


def _agent_served(spec: Any, run_id: Optional[str]) -> Dict[str, Any]:
    """What the usage row is filed under: the model the run actually used."""
    run: Dict[str, Any] = {}
    if run_id:
        try:
            from managers.run_manager import get_run_by_id
            run = get_run_by_id(run_id) or {}
        except Exception:  # noqa: BLE001 - an unreadable run is filed under the agent itself
            run = {}
    return {"id": AGENT_PREFIX + spec.id, "provider": run.get("provider") or "agent",
            "model": run.get("model") or spec.id}


def _agent_run_options(spec: Any, body: Dict[str, Any]) -> Tuple[Optional[int], Dict[str, Any]]:
    """``(agent_version, overrides)`` of an agent completion, both hub
    extensions of the OpenAI body (an SDK sends them through ``extra_body``).

    ``agent_version`` builds the run from that stored version of the agent
    (agents/versions.py) instead of the live definition; ``overrides`` is the
    per-run overrides object (agents/run_overrides.py). An unknown version is
    a 404, an invalid object a 400, a tool set the capability guard refuses
    a 409, all in the OpenAI error shape.
    """
    version = body.get("agent_version")
    if version is not None:
        if isinstance(version, bool) or not isinstance(version, int):
            raise _BadRequest("agent_version must be an integer", param="agent_version")
        from tasks.service import validate_agent_version
        try:
            validate_agent_version(spec.id, version)
        except ValueError as exc:
            raise _BadRequest(str(exc), param="agent_version", status=404,
                              code="agent_version_not_found") from exc
    overrides: Dict[str, Any] = {}
    if body.get("overrides") is not None:
        from agents import run_overrides
        from agents.capability_guard import CapabilityViolation
        try:
            overrides = run_overrides.validate_for_agent(spec.id, body.get("overrides"))
        except CapabilityViolation as exc:
            raise _BadRequest(str(exc), param="overrides", status=409,
                              code="capability_violation") from exc
        except run_overrides.OverrideError as exc:
            raise _BadRequest(str(exc), param="overrides", code="invalid_overrides") from exc
    return version, overrides


def _agent_timeout() -> float:
    """A chat turn's timeout, the same the web chat's blocking send uses."""
    chat = float(getattr(settings, "chat_request_timeout", 900) or 900)
    llm = float(getattr(settings, "llm_request_timeout", 600) or 600)
    return max(chat, llm + 60)


async def _agent_completion(request: Request, body: Dict[str, Any], principal: Any):
    """``POST /v1/chat/completions`` for ``model: "agent:<id>"``."""
    from chat.models import ChatHistoryMessage, ChatRequest
    from widgets.relay import TurnRelay

    requested = str(body.get("model"))
    try:
        spec = _agent_spec(requested)
        if body.get("tools") or body.get("functions"):
            raise _BadRequest("an agent model runs its own tools on the hub; client tools "
                              "are not accepted", param="tools", code="tools_not_supported")
        if body.get("n") not in (None, 1):
            raise _BadRequest("only n=1 is supported", param="n")
        workspace = _agent_workspace(request, principal, spec)
        message, history = _agent_turn(body.get("messages"),
                                       _json_instruction(body.get("response_format")))
        agent_version, overrides = _agent_run_options(spec, body)
    except _BadRequest as exc:
        return exc.response()

    chat_request = ChatRequest(
        agent_id=spec.id, message=message, workspace=workspace,
        history=[ChatHistoryMessage(**h) for h in history], source="api",
        agent_version=agent_version, overrides=overrides or None)
    # The run this turn creates carries key_id when the caller presented a
    # personal key, so it counts toward that key's money quota and shows up
    # attributed to it in the accounting report (docs/costs.md "Attribution").
    key_id = getattr(principal, "credential_id", None) if getattr(principal, "via", "") == "api_key" else None
    relay = TurnRelay(chat_request, user_id=getattr(principal, "id", None), key_id=key_id).start()
    stream = bool(body.get("stream"))
    include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
    completion_id = f"chatcmpl-{secrets.token_hex(12)}"
    created = int(time.time())
    started = time.monotonic()
    timeout = _agent_timeout()
    prompt_text = "\n".join([*(h["content"] for h in history), message])

    def account(done: Optional[Dict[str, Any]], usage: Dict[str, Any], status: str,
                error: Optional[str]) -> None:
        run_id = (done or {}).get("run_id") or relay.run_id
        _account(principal, _agent_served(spec, run_id), usage, started=started, stream=stream,
                 status=status, error=error, tools=0, object_type="agent", workspace=workspace,
                 details={"run_id": run_id, "agent_id": spec.id})

    # The first event says whether the turn started at all: a request the
    # pipeline refuses (an attachment, a reference) ends in a done carrying
    # the HTTP status, which is still an HTTP error here, not a 200 stream.
    try:
        first = await relay.next_event(timeout)
    except StopAsyncIteration:
        first = relay.done
    if first is None:
        relay.stop()
        return _error(504, f"the agent did not start within {int(timeout)}s", type_="api_error")
    if first.get("type") == "done" and not first.get("ok"):
        status = int(first.get("status") or 502)
        detail = str(first.get("error") or "the agent could not run")
        account(first, _turn_usage(first, [], prompt_text, ""), "error", detail)
        return _error(status, detail, type_="invalid_request_error" if status < 500 else "api_error")

    extra = {"agents_hub": {"agent_id": spec.id, "workspace": workspace,
                            **({"agent_version": agent_version} if agent_version is not None else {}),
                            **({"overrides": overrides} if overrides else {})}}

    if stream:
        return StreamingResponse(
            _agent_stream(relay, first, completion_id=completion_id, created=created,
                          requested=requested, include_usage=include_usage, timeout=timeout,
                          prompt_text=prompt_text, account=account, extra=extra),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    handoff_texts: List[str] = []
    handoff_runs: List[str] = []

    def take(event: Dict[str, Any]) -> None:
        if event.get("type") == "handoff":
            handoff_texts.append(str(event.get("from_response") or ""))
            if event.get("run_id"):
                handoff_runs.append(str(event["run_id"]))

    async def drain() -> Optional[Dict[str, Any]]:
        take(first)
        if first.get("type") == "done":
            return first
        async for event in relay.events():
            take(event)
        return relay.done

    try:
        done = await asyncio.wait_for(drain(), timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        relay.stop()
        detail = f"the agent did not answer within {int(timeout)}s"
        account(None, _turn_usage({}, handoff_runs, prompt_text, ""), "error", detail)
        return _error(504, detail, type_="api_error")
    except asyncio.CancelledError:
        relay.stop()
        raise
    done = done or {}
    if not done.get("ok"):
        detail = str(done.get("error") or "the agent could not answer")
        account(done, _turn_usage(done, handoff_runs, prompt_text, ""), "error", detail)
        return _error(502, detail, type_="api_error")
    text = "\n\n".join(t for t in [*handoff_texts, str(done.get("response") or "")] if t.strip())
    usage = _turn_usage(done, handoff_runs, prompt_text, text)
    account(done, usage, "ok", None)
    extra["agents_hub"]["run_id"] = done.get("run_id")
    return {
        "id": completion_id, "object": "chat.completion", "created": created,
        "model": requested,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "logprobs": None, "finish_reason": "stop"}],
        "usage": usage,
        **extra,
    }


async def _agent_stream(relay: Any, first: Dict[str, Any], *, completion_id: str, created: int,
                        requested: str, include_usage: bool, timeout: float, prompt_text: str,
                        account: Any, extra: Dict[str, Any]) -> AsyncIterator[str]:
    """An agent turn as ``chat.completion.chunk`` events.

    Content deltas are the agent's streamed tokens, which include what it
    says between tool calls; a handoff puts a blank line between the two
    agents. When an agent streamed nothing (a structured reply, a model that
    does not stream), its final answer is sent as one delta. A client that
    disconnects stops the run.
    """

    def chunk(delta: Dict[str, Any], finish: Optional[str] = None, **more: Any) -> str:
        return _sse({"id": completion_id, "object": "chat.completion.chunk", "created": created,
                     "model": requested,
                     "choices": [{"index": 0, "delta": delta, "logprobs": None,
                                  "finish_reason": finish}], **more})

    parts: List[str] = []
    handoff_runs: List[str] = []
    streamed_since_boundary = False
    done: Optional[Dict[str, Any]] = None
    status, error = "ok", None

    async def events() -> AsyncIterator[Dict[str, Any]]:
        yield first
        if first.get("type") != "done":
            async for event in relay.events():
                yield event

    yield chunk({"role": "assistant", "content": ""})
    try:
        async with asyncio.timeout(timeout):
            async for event in events():
                kind = event.get("type")
                if kind == "token" and not event.get("delegation") and event.get("token"):
                    streamed_since_boundary = True
                    parts.append(str(event["token"]))
                    yield chunk({"content": str(event["token"])})
                elif kind == "handoff":
                    if event.get("run_id"):
                        handoff_runs.append(str(event["run_id"]))
                    if not streamed_since_boundary and event.get("from_response"):
                        parts.append(str(event["from_response"]))
                        yield chunk({"content": str(event["from_response"])})
                    parts.append("\n\n")
                    yield chunk({"content": "\n\n"})
                    streamed_since_boundary = False
                elif kind == "done":
                    done = event
    except (asyncio.CancelledError, GeneratorExit):
        status, error = "error", "client disconnected"
        relay.stop()
        raise
    except (asyncio.TimeoutError, TimeoutError):
        relay.stop()
        status, error = "error", f"the agent did not answer within {int(timeout)}s"
        yield chunk({}, "error", error={"message": error, "type": "api_error"})
        yield "data: [DONE]\n\n"
        return
    finally:
        final = done or {}
        if status == "ok" and not final.get("ok"):
            status, error = "error", str(final.get("error") or "the agent could not answer")
        usage = _turn_usage(final, handoff_runs, prompt_text, "".join(parts))
        account(done, usage, status, error)

    if not (done or {}).get("ok"):
        yield chunk({}, "error", error={"message": error, "type": "api_error"})
        yield "data: [DONE]\n\n"
        return
    if not streamed_since_boundary and done.get("response"):
        yield chunk({"content": str(done["response"])})
    more: Dict[str, Any] = {"usage": usage} if include_usage else {}
    agents_hub = dict(extra.get("agents_hub") or {}, run_id=done.get("run_id"))
    yield chunk({}, "stop", agents_hub=agents_hub, **more)
    yield "data: [DONE]\n\n"


def _int_param(body: Dict[str, Any], *names: str) -> Optional[int]:
    for name in names:
        value = body.get(name)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise _BadRequest(f"{name} must be a positive integer", param=name)
        return int(value)
    return None


@router.post("/chat/completions")
async def chat_completions(request: Request):
    principal = identity.request_principal(request)
    if principal is None:
        # The middleware answers first in every mode; kept for a mounting that
        # bypasses it (a test app, a future router reuse).
        return _error(401, "Invalid or missing API key", type_="authentication_error",
                      code="invalid_api_key")
    # Tokens per day (common/rate_limit.py): a caller that already used its
    # day's tokens is refused before the model is touched, in the OpenAI
    # error shape clients already retry on. Only successful calls count.
    from common import rate_limit
    within, retry_after = rate_limit.check_tokens_per_day(principal)
    if not within:
        response = _error(429, "Daily token limit reached; it resets at 00:00 UTC",
                          type_="rate_limit_error", code="tokens_per_day_exceeded")
        response.headers["Retry-After"] = str(retry_after)
        return response
    # A personal key's own money cap (common/api_keys.py
    # budget_usd_per_month, docs/api-keys.md "Money quota"): refused before
    # the model is touched, the same shape as the token-per-day cap above.
    within_budget, budget_retry_after = rate_limit.check_key_budget(principal)
    if not within_budget:
        response = _error(429, "This API key has reached its monthly budget",
                          type_="rate_limit_error", code="key_budget_exceeded")
        response.headers["Retry-After"] = str(budget_retry_after)
        return response
    try:
        body = await request.json()
    except ValueError:
        return _error(400, "the request body is not valid JSON")
    if not isinstance(body, dict):
        return _error(400, "the request body must be a JSON object")

    model_name = body.get("model")
    if isinstance(model_name, str) and model_name.strip().startswith(AGENT_PREFIX):
        body["model"] = model_name.strip()
        return await _agent_completion(request, body, principal)

    try:
        served = _resolve(body.get("model"))
        messages = _to_langchain(body.get("messages"))
        n = body.get("n")
        if n not in (None, 1):
            raise _BadRequest("only n=1 is supported", param="n")
        temperature = body.get("temperature")
        if temperature is not None and not isinstance(temperature, (int, float)):
            raise _BadRequest("temperature must be a number", param="temperature")
        max_tokens = _int_param(body, "max_completion_tokens", "max_tokens")
        stop = body.get("stop")
        if isinstance(stop, str):
            stop = [stop]
        if stop is not None and not (isinstance(stop, list) and all(isinstance(s, str) for s in stop)):
            raise _BadRequest("stop must be a string or a list of strings", param="stop")
        tools = body.get("tools") or []
        if not isinstance(tools, list):
            raise _BadRequest("tools must be a list", param="tools")
        for i, tool in enumerate(tools):
            if not (isinstance(tool, dict) and tool.get("type") == "function"
                    and isinstance(tool.get("function"), dict) and tool["function"].get("name")):
                raise _BadRequest("each tool must be {type: function, function: {name, ...}}",
                                  param=f"tools[{i}]")
    except _BadRequest as exc:
        return exc.response()

    instruction = _json_instruction(body.get("response_format"))
    if instruction:
        from langchain_core.messages import SystemMessage
        messages = [SystemMessage(content=instruction)] + messages

    stream = bool(body.get("stream"))
    include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
    tool_choice = body.get("tool_choice")

    from providers.local_models import runtime_source
    try:
        with runtime_source("endpoint"):
            model = build_chat_model(provider=served["provider"], model=served["model"],
                                     temperature=float(temperature) if temperature is not None else None,
                                     max_tokens=max_tokens, streaming=stream)
        if tools and tool_choice != "none":
            choice = _tool_choice(tool_choice)
            try:
                model = model.bind_tools(tools, tool_choice=choice) if choice else model.bind_tools(tools)
            except TypeError:
                model = model.bind_tools(tools)
    except Exception as exc:  # noqa: BLE001 - a misconfigured provider is the server's problem, reported to the client
        log.warning("openai_compat: could not build %s", served["id"], exc_info=True)
        return _error(503, f"the model {served['id']} is not available: {exc}", type_="server_error")

    completion_id = f"chatcmpl-{secrets.token_hex(12)}"
    created = int(time.time())
    requested = str(body.get("model"))
    timeout = float(getattr(settings, "llm_request_timeout", 600) or 600)
    started = time.monotonic()
    invoke_kwargs = {"stop": stop} if stop else {}

    if not stream:
        try:
            reply = await asyncio.wait_for(
                asyncio.to_thread(model.invoke, messages, **invoke_kwargs), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - any provider failure becomes one OpenAI error
            timed_out = isinstance(exc, (asyncio.TimeoutError, TimeoutError))
            detail = f"the model did not answer within {int(timeout)}s" if timed_out else str(exc)
            _account(principal, served, _usage_for(None, messages, ""), started=started,
                     stream=False, status="error", error=detail, tools=len(tools))
            return _error(504 if timed_out else 502, detail, type_="api_error")
        text = _content_text(reply.content)
        calls = _openai_tool_calls(reply)
        usage = _usage_for(reply, messages, text + "".join(c["function"]["arguments"] for c in calls))
        _account(principal, served, usage, started=started, stream=False, status="ok",
                 error=None, tools=len(tools))
        message: Dict[str, Any] = {"role": "assistant", "content": text if (text or not calls) else None}
        if calls:
            message["tool_calls"] = calls
        return {
            "id": completion_id, "object": "chat.completion", "created": created,
            "model": requested,
            "choices": [{"index": 0, "message": message, "logprobs": None,
                         "finish_reason": _finish_reason(reply, bool(calls))}],
            "usage": usage,
        }

    return StreamingResponse(
        _stream(model, messages, invoke_kwargs, principal=principal, served=served,
                completion_id=completion_id, created=created, requested=requested,
                include_usage=include_usage, timeout=timeout, started=started,
                tools=len(tools)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(payload: Any) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stream(model: Any, messages: List[Any], invoke_kwargs: Dict[str, Any], *,
                  principal: Any, served: Dict[str, Any], completion_id: str, created: int,
                  requested: str, include_usage: bool, timeout: float, started: float,
                  tools: int) -> AsyncIterator[str]:
    """chat.completion.chunk events, then ``[DONE]``. The chunks are merged as
    they arrive so the final one knows the tool calls, the finish reason and
    whatever usage the provider reported on its last chunk."""

    def chunk(delta: Dict[str, Any], finish: Optional[str] = None, **extra: Any) -> str:
        return _sse({"id": completion_id, "object": "chat.completion.chunk", "created": created,
                     "model": requested,
                     "choices": [{"index": 0, "delta": delta, "logprobs": None,
                                  "finish_reason": finish}], **extra})

    merged = None
    text_parts: List[str] = []
    status, error = "ok", None
    # Stable OpenAI indexes for the tool calls, in the order they first appear.
    call_index: Dict[Any, int] = {}
    yield chunk({"role": "assistant", "content": ""})
    try:
        async with asyncio.timeout(timeout):
            async for piece in model.astream(messages, **invoke_kwargs):
                merged = piece if merged is None else merged + piece
                text = _content_text(getattr(piece, "content", ""))
                if text:
                    text_parts.append(text)
                    yield chunk({"content": text})
                deltas = []
                for tc in getattr(piece, "tool_call_chunks", None) or []:
                    key = tc.get("index") if tc.get("index") is not None else tc.get("id")
                    if key not in call_index:
                        call_index[key] = len(call_index)
                    delta: Dict[str, Any] = {"index": call_index[key]}
                    if tc.get("id"):
                        delta["id"] = tc["id"]
                        delta["type"] = "function"
                    fn: Dict[str, Any] = {}
                    if tc.get("name"):
                        fn["name"] = tc["name"]
                    fn["arguments"] = tc.get("args") or ""
                    delta["function"] = fn
                    deltas.append(delta)
                if deltas:
                    yield chunk({"tool_calls": deltas})
    except (asyncio.CancelledError, GeneratorExit):
        # The client went away. Nothing left to send; account for what ran.
        status, error = "error", "client disconnected"
        raise
    except Exception as exc:  # noqa: BLE001 - reported in-band: the headers are already sent
        timed_out = isinstance(exc, (asyncio.TimeoutError, TimeoutError))
        status = "error"
        error = f"the model did not answer within {int(timeout)}s" if timed_out else str(exc)
        yield chunk({}, "error", error={"message": error, "type": "api_error"})
        yield "data: [DONE]\n\n"
        return
    finally:
        calls = (getattr(merged, "tool_calls", None) or []) if merged is not None else []
        completion_text = "".join(text_parts) + "".join(
            json.dumps(c.get("args") or {}) for c in calls)
        usage = _usage_for(merged, messages, completion_text)
        _account(principal, served, usage, started=started, stream=True, status=status,
                 error=error, tools=tools)

    extra = {"usage": usage} if include_usage else {}
    yield chunk({}, _finish_reason(merged, bool(calls)), **extra)
    yield "data: [DONE]\n\n"


@router.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                  include_in_schema=False)
async def not_served(rest: str):
    """Everything else under /v1 (embeddings, images, the Responses API):
    a 404 an OpenAI client can read, rather than the SPA or a bare 404."""
    if not rest.strip("/"):
        return await index()
    return _error(404, f"/v1/{rest} is not served by this hub; see GET /v1",
                  code="unknown_url")


# ── Serving info and usage, for the Models page ──────────────────────────────

@serving_router.get("/info")
async def serving_info(request: Request):
    host = request.headers.get("x-forwarded-host")
    proto = request.headers.get("x-forwarded-proto")
    if host:
        origin = f"{proto or request.url.scheme}://{host}"
    else:
        origin = str(request.base_url).rstrip("/")
    mode = identity.current_mode()
    auth = {"multi": "api_key", "token": "token"}.get(mode, "none")
    served, _ = _served()
    try:
        agents = len(_listed_agents(request, identity.request_principal(request)))
    except _BadRequest:
        agents = 0
    return {"base_url": f"{origin}/v1", "models": len(served), "agents": agents, "auth": auth}


@serving_router.get("/usage")
async def serving_usage(request: Request, since: Optional[str] = None,
                        until: Optional[str] = None, limit: int = 50):
    """Tokens per served model. In ``multi`` mode a non-administrator sees
    only their own calls: who called what is not every member's business."""
    from common import serving
    principal = identity.request_principal(request)
    user_id = None
    if identity.current_mode() == "multi" and not getattr(principal, "is_admin", False):
        user_id = getattr(principal, "id", None) or "-"
    return serving.usage(since=since, until=until, limit_recent=limit, user_id=user_id)
