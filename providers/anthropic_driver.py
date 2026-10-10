"""The hub's own chat model for Anthropic's Messages API.

A ``BaseChatModel`` from ``langchain_core`` on the official ``anthropic`` SDK,
in place of ``langchain-anthropic``. The messages it produces have the shape
the rest of the hub already reads: a plain string for a text-only answer,
otherwise Anthropic's content blocks (``text``, ``thinking``, ``tool_use``) as
a list, tool calls in ``tool_calls``, usage with the cache split in
``usage_metadata``, and the response fields (``stop_reason``, ``model``,
``usage``, ``context_management``) in ``response_metadata``.

What the package did not carry and this driver does:

- ``context_management.applied_edits`` on a streamed call: the API reports
  what its context editing cleared in the ``message_delta`` event, which the
  package dropped, so the loop's compaction accounting only saw it on calls
  that were not streamed;
- unknown content blocks pass through streaming untouched (``tool_search_tool_result``
  among them), so a request that uses a server-side tool keeps its result for
  the next turn;
- ``strict`` on ``bind_tools``, sent as the per-tool ``strict`` flag of the
  Messages API.

Request-level extras (``betas``, ``context_management``, ``mcp_servers``, a
dict tool with ``defer_loading``) travel through ``bind(...)`` unchanged; a
request with betas goes to the beta endpoint.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
from operator import itemgetter
from typing import (
    Any, AsyncIterator, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple,
    Union,
)

import anthropic
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.base import LangSmithParams
from langchain_core.language_models.chat_models import (
    BaseChatModel, agenerate_from_stream, generate_from_stream,
)
from langchain_core.messages import (
    AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage, ToolMessage,
    is_data_content_block,
)
from langchain_core.messages.ai import InputTokenDetails, UsageMetadata
from langchain_core.messages.tool import tool_call as _tool_call, tool_call_chunk as _tool_call_chunk
from langchain_core.output_parsers.openai_tools import JsonOutputKeyToolsParser, PydanticToolsParser
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable, RunnableMap, RunnablePassthrough
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_core.utils.pydantic import is_basemodel_subclass
from pydantic import ConfigDict, Field, SecretStr, model_validator

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.anthropic.com"

#: Server-side tools are declared by a versioned ``type`` and pass through as given.
_BUILTIN_TOOL_PREFIXES = ("text_editor_", "computer_", "bash_", "web_search_", "web_fetch_",
                          "code_execution_", "tool_search_", "memory_")
#: Content blocks that open with ``content_block_start`` and are not text.
_BLOCK_START_TYPES = ("tool_use", "code_execution_tool_result", "document", "redacted_thinking",
                      "mcp_tool_use", "mcp_tool_result", "server_tool_use", "web_search_tool_result",
                      "web_fetch_tool_result", "tool_search_tool_result", "container_upload")
_DATA_URL = re.compile(r"^data:(?P<media_type>image/.+);base64,(?P<data>.+)$")


# ── Tools ─────────────────────────────────────────────────────────────────────

def is_builtin_tool(tool: Any) -> bool:
    """A server-side tool, declared by a versioned ``type``."""
    if not isinstance(tool, dict):
        return False
    kind = tool.get("type")
    return isinstance(kind, str) and kind.startswith(_BUILTIN_TOOL_PREFIXES)


def convert_to_anthropic_tool(tool: Union[Dict[str, Any], type, Callable, BaseTool]) -> Dict[str, Any]:
    """A tool in Anthropic's shape: ``name``, ``description``, ``input_schema``.
    A dict already in that shape passes through with its extra keys
    (``cache_control``, ``defer_loading``, ``strict``)."""
    if isinstance(tool, dict) and all(k in tool for k in ("name", "description", "input_schema")):
        return dict(tool)
    fn = convert_to_openai_tool(tool)["function"]
    out: Dict[str, Any] = {"name": fn["name"], "input_schema": fn["parameters"]}
    if "description" in fn:
        out["description"] = fn["description"]
    return out


def _tool_use_blocks(tool_calls: Sequence[Mapping[str, Any]]) -> List[dict]:
    return [{"type": "tool_use", "name": tc["name"], "input": tc["args"], "id": str(tc["id"])}
            for tc in tool_calls]


def extract_tool_calls(content: Any) -> list:
    """The ``tool_use`` blocks of a content list as LangChain tool calls."""
    if not isinstance(content, list):
        return []
    return [_tool_call(name=b["name"], args=b.get("input") or {}, id=b.get("id"))
            for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]


# ── Messages → request ────────────────────────────────────────────────────────

def _image_source(url: str) -> dict:
    m = _DATA_URL.match(url)
    if m:
        return {"type": "base64", "media_type": m.group("media_type"), "data": m.group("data")}
    if re.match(r"^https?://.*$", url):
        return {"type": "url", "url": url}
    raise ValueError("Malformed image url: expected an https URL or a base64 data URL.")


def _data_block(block: dict) -> dict:
    """A langchain_core data block (image, file) in Anthropic's shape."""
    kind, source_type = block["type"], block.get("source_type")
    if kind == "image":
        if source_type == "url":
            source = (_image_source(block["url"]) if block["url"].startswith("data:")
                      else {"type": "url", "url": block["url"]})
        elif source_type == "base64":
            source = {"type": "base64", "media_type": block["mime_type"], "data": block["data"]}
        elif source_type == "id":
            source = {"type": "file", "file_id": block["id"]}
        else:
            raise ValueError("Anthropic takes images as url, base64 or file id.")
        out: Dict[str, Any] = {"type": "image", "source": source}
    elif kind == "file":
        if source_type == "url":
            source = {"type": "url", "url": block["url"]}
        elif source_type == "base64":
            source = {"type": "base64", "media_type": block.get("mime_type") or "application/pdf",
                      "data": block["data"]}
        elif source_type == "text":
            source = {"type": "text", "media_type": block.get("mime_type") or "text/plain",
                      "data": block["text"]}
        elif source_type == "id":
            source = {"type": "file", "file_id": block["id"]}
        else:
            raise ValueError("Anthropic takes files as url, base64, text or file id.")
        out = {"type": "document", "source": source}
    else:
        raise ValueError(f"Block of type {kind} is not supported.")
    meta = block.get("metadata") or {}
    for key in ("cache_control", "citations", "title", "context"):
        if key in block:
            out[key] = block[key]
        elif key in meta:
            out[key] = meta[key]
    return out


def _merge_messages(messages: Sequence[BaseMessage]) -> List[BaseMessage]:
    """Tool results become user turns; consecutive user (or system) turns
    merge into one, the way the API requires."""
    merged: List[BaseMessage] = []
    for curr in messages:
        if isinstance(curr, ToolMessage):
            if (isinstance(curr.content, list) and curr.content and all(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in curr.content)):
                curr = HumanMessage(curr.content)
            else:
                curr = HumanMessage([{"type": "tool_result", "content": curr.content,
                                      "tool_use_id": curr.tool_call_id,
                                      "is_error": curr.status == "error"}])
        last = merged[-1] if merged else None
        if last is not None and any(isinstance(curr, c) and isinstance(last, c)
                                    for c in (SystemMessage, HumanMessage)):
            content: list = ([{"type": "text", "text": last.content}] if isinstance(last.content, str)
                             else copy.copy(list(last.content)))
            if isinstance(curr.content, str):
                content.append({"type": "text", "text": curr.content})
            else:
                content.extend(curr.content)
            merged[-1] = curr.model_copy(update={"content": content})
        else:
            merged.append(curr)
    return merged


def _format_block(block: Any, message: BaseMessage) -> Optional[dict]:
    """One content block in the request shape, or None to drop it."""
    if isinstance(block, str):
        return {"type": "text", "text": block}
    if not isinstance(block, dict):
        raise ValueError(f"Content blocks must be str or dict, got {type(block)}")
    kind = block.get("type")
    if kind is None:
        raise ValueError("A dict content block needs a type")
    if kind == "image_url":
        return {"type": "image", "source": _image_source(block["image_url"]["url"])}
    if is_data_content_block(block):
        return _data_block(block)
    if kind == "tool_use":
        if isinstance(message, AIMessage) and any(tc["id"] == block.get("id") for tc in message.tool_calls):
            return None  # the tool call is written from message.tool_calls below
        return {k: v for k, v in block.items() if k in ("type", "id", "name", "input", "cache_control")}
    if kind in ("server_tool_use", "mcp_tool_use"):
        out = {k: v for k, v in block.items()
               if k in ("type", "id", "input", "name", "server_name", "cache_control")}
        if block.get("input") == {} and "partial_json" in block:
            try:
                parsed = json.loads(block["partial_json"])
                if parsed:
                    out["input"] = parsed
            except json.JSONDecodeError:
                log.debug("anthropic: partial tool input was not JSON", exc_info=True)
        return out
    if kind == "text":
        if not str(block.get("text", "")).strip():
            return None  # the API rejects an empty text block
        return {k: v for k, v in block.items() if k in ("type", "text", "cache_control")}
    if kind == "thinking":
        return {k: v for k, v in block.items() if k in ("type", "thinking", "cache_control", "signature")}
    if kind == "redacted_thinking":
        return {k: v for k, v in block.items() if k in ("type", "cache_control", "data")}
    if kind == "tool_result":
        inner = format_messages([HumanMessage(block["content"])])[1][0]["content"]
        return {**block, "content": inner}
    if kind in ("code_execution_tool_result", "mcp_tool_result", "web_search_tool_result",
                "web_fetch_tool_result", "tool_search_tool_result"):
        return {k: v for k, v in block.items()
                if k in ("type", "content", "tool_use_id", "is_error", "cache_control")}
    if kind in ("reasoning", "reasoning_content", "function_call", "refusal"):
        return None  # another provider's blocks, meaningless here
    return block


def format_messages(messages: Sequence[BaseMessage]) -> Tuple[Union[str, List[dict], None], List[dict]]:
    """``(system, messages)`` of a Messages request for a conversation."""
    system: Union[str, List[dict], None] = None
    out: List[dict] = []
    for message in _merge_messages(messages):
        if message.type == "system":
            if system is not None:
                raise ValueError("Received multiple non-consecutive system messages.")
            if isinstance(message.content, list):
                system = [b if isinstance(b, dict) else {"type": "text", "text": b} for b in message.content]
            else:
                system = message.content
            continue
        if isinstance(message, AIMessage):
            role = "assistant"
        elif isinstance(message, HumanMessage):
            role = "user"
        else:
            role = str(getattr(message, "role", None) or message.type)
        content: Union[str, list]
        if isinstance(message.content, str):
            content = message.content
        else:
            content = []
            for block in message.content:
                formatted = _format_block(block, message)
                if formatted is not None:
                    content.append(formatted)
        if isinstance(message, AIMessage) and message.tool_calls:
            blocks: List[Any] = ([{"type": "text", "text": content}] if isinstance(content, str) and content
                                 else list(content or []))
            present = {b.get("id") for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"}
            blocks.extend(_tool_use_blocks([tc for tc in message.tool_calls if tc["id"] not in present]))
            content = blocks
        out.append({"role": role, "content": content})
    return system, out


# ── Response → messages ───────────────────────────────────────────────────────

def _usage_metadata(usage: Mapping[str, Any]) -> UsageMetadata:
    details = {"cache_read": usage.get("cache_read_input_tokens"),
               "cache_creation": usage.get("cache_creation_input_tokens")}
    # Anthropic's input_tokens exclude the cached share; the hub counts it all.
    inp = (usage.get("input_tokens") or 0) + (details["cache_read"] or 0) + (details["cache_creation"] or 0)
    outp = usage.get("output_tokens") or 0
    return UsageMetadata(input_tokens=inp, output_tokens=outp, total_tokens=inp + outp,
                         input_token_details=InputTokenDetails(
                             **{k: v for k, v in details.items() if v is not None}))


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else value.model_dump()


def _clean_block(block: dict) -> dict:
    """A content block without the SDK's unset fields (``citations: None``,
    ``caller: None``), which the API would reject when sent back."""
    return {k: v for k, v in block.items() if v is not None}


def result_from_message(data: Any, model_name: str = "") -> ChatResult:
    """A Messages API response as a chat result."""
    d = _as_dict(data)
    content: List[Any] = [_clean_block(b) for b in (d.get("content") or []) if isinstance(b, dict)]
    llm_output = {k: v for k, v in d.items() if k not in ("content", "role", "type")}
    llm_output.setdefault("model_name", llm_output.get("model") or model_name)
    if len(content) == 1 and content[0].get("type") == "text" and not content[0].get("citations"):
        message = AIMessage(content=content[0].get("text", ""))
    elif any(b.get("type") == "tool_use" for b in content):
        message = AIMessage(content=content, tool_calls=extract_tool_calls(content))
    else:
        message = AIMessage(content=content)
    if d.get("usage"):
        message.usage_metadata = _usage_metadata(d["usage"])
    return ChatResult(generations=[ChatGeneration(message=message)], llm_output=llm_output)


class _StreamState:
    """What the event converter remembers between events."""

    def __init__(self, *, coerce_text: bool, stream_usage: bool) -> None:
        self.coerce_text = coerce_text
        self.stream_usage = stream_usage
        self.open_block: Optional[dict] = None  # the content_block of the last start event


def event_to_chunk(event: Any, state: _StreamState) -> Optional[AIMessageChunk]:
    """One stream event as a message chunk, or None for events that carry
    nothing (``ping``, ``content_block_stop``, ``message_stop``)."""
    ev = _as_dict(event)
    kind = ev.get("type")
    if kind == "message_start":
        msg = ev.get("message") or {}
        usage = None
        if state.stream_usage and msg.get("usage"):
            usage = _usage_metadata(msg["usage"])
            # The final output count arrives with message_delta; count input only here.
            usage["total_tokens"] = usage["total_tokens"] - usage["output_tokens"]
            usage["output_tokens"] = 0
        meta: Dict[str, Any] = {}
        if msg.get("model"):
            meta["model_name"] = msg["model"]
        if msg.get("context_management"):
            meta["context_management"] = msg["context_management"]
        return AIMessageChunk(content="" if state.coerce_text else [], usage_metadata=usage,
                              response_metadata=meta)
    if kind == "content_block_start":
        block = _clean_block(dict(ev.get("content_block") or {}))
        if block.get("type") == "text":
            state.open_block = block
            return None
        block["index"] = ev.get("index")
        state.open_block = block
        chunks = []
        if block.get("type") == "tool_use":
            chunks = [_tool_call_chunk(index=ev.get("index"), id=block.get("id"), name=block.get("name"), args="")]
        return AIMessageChunk(content=[block], tool_call_chunks=chunks)
    if kind == "content_block_delta":
        delta = dict(ev.get("delta") or {})
        dtype = delta.get("type")
        index = ev.get("index")
        if dtype in ("text_delta", "citations_delta"):
            if state.coerce_text and "text" in delta:
                return AIMessageChunk(content=delta.get("text") or "")
            delta["index"] = index
            delta["type"] = "text"
            if "citation" in delta:
                delta["citations"] = [delta.pop("citation")]
            return AIMessageChunk(content=[delta])
        if dtype in ("thinking_delta", "signature_delta"):
            if delta.get("text", 0) is None:
                delta.pop("text")
            delta["index"] = index
            delta["type"] = "thinking"
            return AIMessageChunk(content=[delta])
        if dtype == "input_json_delta":
            delta["index"] = index
            chunks = []
            if (state.open_block or {}).get("type") == "tool_use":
                chunks = [_tool_call_chunk(index=index, id=None, name=None, args=delta.get("partial_json") or "")]
            return AIMessageChunk(content=[delta], tool_call_chunks=chunks)
        delta["index"] = index
        return AIMessageChunk(content=[delta])
    if kind == "message_delta":
        delta = ev.get("delta") or {}
        meta = {"stop_reason": delta.get("stop_reason"), "stop_sequence": delta.get("stop_sequence")}
        cm = ev.get("context_management") or delta.get("context_management")
        if cm:
            meta["context_management"] = cm
        usage = None
        if state.stream_usage and ev.get("usage"):
            outp = ev["usage"].get("output_tokens") or 0
            usage = UsageMetadata(input_tokens=0, output_tokens=outp, total_tokens=outp)
        return AIMessageChunk(content="" if state.coerce_text else [], usage_metadata=usage,
                              response_metadata=meta)
    return None


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) and b.get("type") == "text"
                       else (b if isinstance(b, str) else "") for b in content)
    return ""


# ── The model ─────────────────────────────────────────────────────────────────

class AnthropicChatModel(BaseChatModel):
    """A chat model on Anthropic's Messages API.

    Constructed with the keyword names ``ChatAnthropic`` took (``model``,
    ``api_key``, ``base_url``, ``max_tokens``, ``temperature``, ``thinking``,
    ``streaming``, ``default_request_timeout`` or ``timeout``, ``betas``,
    ``default_headers``), so the builders need no change.
    """

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True,
                              protected_namespaces=())

    model: str = Field(alias="model_name")
    anthropic_api_key: Optional[SecretStr] = Field(default=None, alias="api_key")
    anthropic_api_url: Optional[str] = Field(default=None, alias="base_url")
    max_tokens: int = 1024
    temperature: Optional[float] = None
    top_k: Optional[int] = None
    top_p: Optional[float] = None
    stop_sequences: Optional[List[str]] = Field(default=None, alias="stop")
    #: Seconds per request; None keeps the SDK default, 0 or less sends no timeout.
    default_request_timeout: Optional[float] = Field(default=None, alias="timeout")
    max_retries: int = 2
    default_headers: Optional[Mapping[str, str]] = None
    #: Beta flags; a request carrying any goes to the beta endpoint.
    betas: Optional[List[str]] = None
    mcp_servers: Optional[List[Dict[str, Any]]] = None
    model_kwargs: Dict[str, Any] = Field(default_factory=dict)
    streaming: bool = False
    stream_usage: bool = True
    #: ``{"type": "enabled", "budget_tokens": N}`` or ``{"type": "adaptive"}``.
    thinking: Optional[Dict[str, Any]] = None
    http_client: Any = Field(default=None, exclude=True)
    http_async_client: Any = Field(default=None, exclude=True)
    root_client: Any = Field(default=None, exclude=True)
    root_async_client: Any = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _build_clients(self) -> "AnthropicChatModel":
        self.anthropic_api_url = (self.anthropic_api_url or os.environ.get("ANTHROPIC_API_URL")
                                  or os.environ.get("ANTHROPIC_BASE_URL") or DEFAULT_BASE_URL)
        key = self.anthropic_api_key.get_secret_value() if self.anthropic_api_key else os.environ.get("ANTHROPIC_API_KEY", "")
        params: Dict[str, Any] = {"api_key": key, "base_url": self.anthropic_api_url,
                                  "max_retries": self.max_retries,
                                  "default_headers": self.default_headers or None}
        # None is a meaningful value for the SDK (no timeout); a positive
        # number is a limit; anything else keeps the SDK default.
        if self.default_request_timeout is None or self.default_request_timeout > 0:
            params["timeout"] = self.default_request_timeout
        if self.root_client is None:
            extra = {"http_client": self.http_client} if self.http_client is not None else {}
            self.root_client = anthropic.Client(**params, **extra)
        if self.root_async_client is None:
            extra = {"http_client": self.http_async_client} if self.http_async_client is not None else {}
            self.root_async_client = anthropic.AsyncClient(**params, **extra)
        return self

    # -- identity ------------------------------------------------------------

    @property
    def _llm_type(self) -> str:
        return "anthropic-chat"

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def _identifying_params(self) -> Dict[str, Any]:
        return {"model": self.model, "max_tokens": self.max_tokens, "temperature": self.temperature,
                "top_k": self.top_k, "top_p": self.top_p, "model_kwargs": self.model_kwargs,
                "streaming": self.streaming, "max_retries": self.max_retries,
                "default_request_timeout": self.default_request_timeout, "thinking": self.thinking}

    def _get_ls_params(self, stop: Optional[List[str]] = None, **kwargs: Any) -> LangSmithParams:
        params = LangSmithParams(ls_provider="anthropic", ls_model_name=self.model,
                                 ls_model_type="chat", ls_temperature=self.temperature)
        if self.max_tokens:
            params["ls_max_tokens"] = self.max_tokens
        stops = stop or self.stop_sequences
        if stops:
            params["ls_stop"] = list(stops)
        return params

    @property
    def is_anthropic_api(self) -> bool:
        """True when the requests go to Anthropic's own API."""
        return "api.anthropic.com" in (self.anthropic_api_url or "")

    # -- tokens --------------------------------------------------------------
    # An estimate, so no tokenizer is loaded on start; the hub prices by the
    # usage the API reports.

    def get_num_tokens(self, text: str) -> int:
        return max(1, len(text or "") // 4)

    def get_num_tokens_from_messages(self, messages: List[BaseMessage], tools: Any = None) -> int:
        return sum(3 + self.get_num_tokens(_content_text(m.content)) for m in messages)

    # -- request ---------------------------------------------------------------

    def _get_request_payload(self, input_: LanguageModelInput, *, stop: Optional[List[str]] = None,
                             **kwargs: Any) -> dict:
        """The request body a call with these arguments sends."""
        messages = self._convert_input(input_).to_messages()
        system, formatted = format_messages(messages)
        payload: Dict[str, Any] = {
            "model": self.model, "max_tokens": self.max_tokens, "messages": formatted,
            "temperature": self.temperature, "top_k": self.top_k, "top_p": self.top_p,
            "stop_sequences": stop or self.stop_sequences, "betas": self.betas,
            "mcp_servers": self.mcp_servers, "system": system, "thinking": self.thinking,
            **self.model_kwargs, **kwargs,
        }
        payload.pop("ls_structured_output_format", None)
        return {k: v for k, v in payload.items() if v is not None}

    def _create(self, payload: dict) -> Any:
        if payload.get("betas"):
            return self.root_client.beta.messages.create(**payload)
        payload.pop("betas", None)
        return self.root_client.messages.create(**payload)

    async def _acreate(self, payload: dict) -> Any:
        if payload.get("betas"):
            return await self.root_async_client.beta.messages.create(**payload)
        payload.pop("betas", None)
        return await self.root_async_client.messages.create(**payload)

    @staticmethod
    def _coerce_text(payload: Mapping[str, Any]) -> bool:
        """Whether a streamed answer can be plain text: no tools, no thinking,
        no cited documents in the request."""
        if "tools" in payload or "mcp_servers" in payload or (payload.get("extra_body") or {}).get("tools"):
            return False
        if (payload.get("thinking") or {}).get("type") in ("enabled", "adaptive"):
            return False
        for message in payload.get("messages") or []:
            if isinstance(message.get("content"), list):
                for block in message["content"]:
                    if (isinstance(block, dict) and block.get("type") == "document"
                            and (block.get("citations") or {}).get("enabled")):
                        return False
        return True

    # -- calls ---------------------------------------------------------------

    def _generate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None,
                  run_manager: Optional[CallbackManagerForLLMRun] = None, **kwargs: Any) -> ChatResult:
        if self.streaming:
            return generate_from_stream(self._stream(messages, stop=stop, run_manager=run_manager, **kwargs))
        payload = self._get_request_payload(messages, stop=stop, **kwargs)
        payload.pop("stream", None)
        return result_from_message(self._create(payload), self.model)

    async def _agenerate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None,
                         run_manager: Optional[AsyncCallbackManagerForLLMRun] = None, **kwargs: Any) -> ChatResult:
        if self.streaming:
            return await agenerate_from_stream(
                self._astream(messages, stop=stop, run_manager=run_manager, **kwargs))
        payload = self._get_request_payload(messages, stop=stop, **kwargs)
        payload.pop("stream", None)
        return result_from_message(await self._acreate(payload), self.model)

    def _stream(self, messages: List[BaseMessage], stop: Optional[List[str]] = None,
                run_manager: Optional[CallbackManagerForLLMRun] = None, *,
                stream_usage: Optional[bool] = None, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        kwargs["stream"] = True
        payload = self._get_request_payload(messages, stop=stop, **kwargs)
        state = _StreamState(coerce_text=self._coerce_text(payload),
                             stream_usage=self.stream_usage if stream_usage is None else stream_usage)
        with self._create(payload) as stream:
            for event in stream:
                msg = event_to_chunk(event, state)
                if msg is None:
                    continue
                chunk = ChatGenerationChunk(message=msg)
                if run_manager and isinstance(msg.content, str):
                    run_manager.on_llm_new_token(msg.content, chunk=chunk)
                yield chunk

    async def _astream(self, messages: List[BaseMessage], stop: Optional[List[str]] = None,
                       run_manager: Optional[AsyncCallbackManagerForLLMRun] = None, *,
                       stream_usage: Optional[bool] = None, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        kwargs["stream"] = True
        payload = self._get_request_payload(messages, stop=stop, **kwargs)
        state = _StreamState(coerce_text=self._coerce_text(payload),
                             stream_usage=self.stream_usage if stream_usage is None else stream_usage)
        async with await self._acreate(payload) as stream:
            async for event in stream:
                msg = event_to_chunk(event, state)
                if msg is None:
                    continue
                chunk = ChatGenerationChunk(message=msg)
                if run_manager and isinstance(msg.content, str):
                    await run_manager.on_llm_new_token(msg.content, chunk=chunk)
                yield chunk

    # -- tools and schemas -----------------------------------------------------

    def bind_tools(self, tools: Sequence[Union[Dict[str, Any], type, Callable, BaseTool]], *,
                   tool_choice: Optional[Union[dict, str]] = None, strict: Optional[bool] = None,
                   parallel_tool_calls: Optional[bool] = None,
                   **kwargs: Any) -> Runnable[LanguageModelInput, BaseMessage]:
        """Bind tools in Anthropic's shape. ``tool_choice`` takes a tool name,
        ``auto``, ``any`` or the API dict; ``strict`` marks every tool for the
        API's strict schema mode; a dict tool already in Anthropic's shape keeps
        its extra keys (``defer_loading``, ``cache_control``)."""
        formatted: List[Dict[str, Any]] = [dict(t) if is_builtin_tool(t) else convert_to_anthropic_tool(t)  # type: ignore[call-overload]
                                           for t in tools]
        if strict:
            formatted = [{**t, "strict": True} if "input_schema" in t else t for t in formatted]
        if not tool_choice:
            pass
        elif isinstance(tool_choice, dict):
            kwargs["tool_choice"] = tool_choice
        elif isinstance(tool_choice, str) and tool_choice in ("any", "auto"):
            kwargs["tool_choice"] = {"type": tool_choice}
        elif isinstance(tool_choice, str) and tool_choice in ("required", "true"):
            kwargs["tool_choice"] = {"type": "any"}
        elif isinstance(tool_choice, str):
            kwargs["tool_choice"] = {"type": "tool", "name": tool_choice}
        else:
            raise ValueError(f"Unrecognised tool_choice {tool_choice!r}")
        if parallel_tool_calls is not None:
            choice = kwargs.setdefault("tool_choice", {"type": "auto"})
            choice["disable_parallel_tool_use"] = not parallel_tool_calls
        return self.bind(tools=formatted, **kwargs)

    def with_structured_output(self, schema: Union[Dict[str, Any], type], *, include_raw: bool = False,
                               **kwargs: Any) -> Runnable[LanguageModelInput, Any]:
        """A runnable answering in the shape of ``schema`` through a forced
        tool call. With thinking on, the API refuses a forced tool, so the
        tool is offered and the answer is parsed when the model called it."""
        kwargs.pop("method", None)
        kwargs.pop("strict", None)
        if kwargs:
            raise ValueError(f"Received unsupported arguments {kwargs}")
        tool = convert_to_anthropic_tool(schema)
        name = tool["name"]
        thinking = (self.thinking or {}).get("type") in ("enabled", "adaptive")
        llm = self.bind_tools([tool], tool_choice=None if thinking else name,
                              ls_structured_output_format={"kwargs": {"method": "function_calling"},
                                                           "schema": tool})
        pydantic_schema = isinstance(schema, type) and is_basemodel_subclass(schema)
        parser: Runnable = (PydanticToolsParser(tools=[schema], first_tool_only=True)  # type: ignore[list-item]
                            if pydantic_schema else JsonOutputKeyToolsParser(key_name=name, first_tool_only=True))
        if include_raw:
            assign = RunnablePassthrough.assign(parsed=itemgetter("raw") | parser, parsing_error=lambda _: None)
            none = RunnablePassthrough.assign(parsed=lambda _: None)
            return RunnableMap(raw=llm) | assign.with_fallbacks([none], exception_key="parsing_error")
        return llm | parser


__all__ = [
    "DEFAULT_BASE_URL", "AnthropicChatModel", "convert_to_anthropic_tool", "event_to_chunk",
    "extract_tool_calls", "format_messages", "is_builtin_tool", "result_from_message",
]
