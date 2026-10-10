"""The hub's own chat model for the OpenAI protocol.

A ``BaseChatModel`` from ``langchain_core`` on top of the official ``openai``
SDK, in place of ``langchain-openai``. It speaks both endpoints of the
protocol, ``/v1/chat/completions`` and ``/v1/responses``, which covers OpenAI
itself, LM Studio, the hub's model runtime, vLLM, llama.cpp, OpenRouter and
every gateway in between.

Everything above it (``bind_tools``, the callbacks, the agent loop) sees the
same messages ``langchain-openai`` produced, so the rest of the hub needs no
change. What this driver does that the package did not:

- keeps the ``reasoning_content`` / ``reasoning`` field a local server returns
  beside the answer (gpt-oss, DeepSeek, Qwen on LM Studio or Ollama), in
  ``additional_kwargs["reasoning_content"]``, in full responses and in stream
  deltas alike;
- sends the temperature the builder decided on, including on gpt-5.x at
  reasoning effort ``none`` (``forced_temperature``), where the package
  stripped it from every gpt-5 request;
- asks for usage on streamed completions by the address the request goes to,
  not by whether a variable named ``OPENAI_BASE_URL`` exists in the
  environment, and never reads that variable when it is empty;
- repeats a Responses request once without ``reasoning.summary`` when the
  organisation is not verified for summaries, and remembers the key.

OpenAI's reasoning summary (the only form of its models' reasoning the API
returns) arrives the way the package delivered it in its default output: the
reasoning item in ``additional_kwargs["reasoning"]``, which is where
``reasoning.native_reasoning`` and the chat stream callback read it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from operator import itemgetter
from typing import (
    Any, AsyncIterator, Callable, Dict, Iterator, List, Literal, Mapping, Optional,
    Sequence, Tuple, Type, Union,
)

import openai
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.base import LangSmithParams
from langchain_core.language_models.chat_models import (
    BaseChatModel, agenerate_from_stream, generate_from_stream,
)
from langchain_core.messages import (
    AIMessage, AIMessageChunk, BaseMessage, BaseMessageChunk, ChatMessage, ChatMessageChunk,
    FunctionMessage, FunctionMessageChunk, HumanMessage, HumanMessageChunk, SystemMessage,
    SystemMessageChunk, ToolMessage, ToolMessageChunk, convert_to_openai_data_block,
    is_data_content_block,
)
from langchain_core.messages.ai import InputTokenDetails, OutputTokenDetails, UsageMetadata
from langchain_core.messages.tool import tool_call_chunk as _tool_call_chunk
from langchain_core.output_parsers import JsonOutputParser, PydanticOutputParser
from langchain_core.output_parsers.openai_tools import (
    JsonOutputKeyToolsParser, PydanticToolsParser, make_invalid_tool_call, parse_tool_call,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda, RunnableMap, RunnablePassthrough
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_function, convert_to_openai_tool
from langchain_core.utils.pydantic import is_basemodel_subclass
from pydantic import ConfigDict, Field, SecretStr, model_validator

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"

#: Request keys only the Responses API knows; one of them in a request sends
#: it there.
_RESPONSES_ONLY_KEYS = frozenset({"include", "previous_response_id", "reasoning", "text", "truncation"})
#: Chat-completions keys the Responses API rejects; dropped on the way there.
_CHAT_ONLY_KEYS = ("stop", "n", "seed", "presence_penalty", "frequency_penalty", "logit_bias",
                   "stream_options", "logprobs", "top_logprobs")
#: Output items of the Responses API that are tool results rather than text.
_BUILTIN_TOOL_ITEMS = ("web_search_call", "file_search_call", "computer_call",
                       "code_interpreter_call", "mcp_call", "mcp_list_tools",
                       "mcp_approval_request", "image_generation_call")
#: Items passed back verbatim from an earlier assistant turn.
_PASSTHROUGH_ITEMS = ("reasoning", "web_search_call", "file_search_call", "function_call",
                      "computer_call", "custom_tool_call", "code_interpreter_call", "mcp_call",
                      "mcp_list_tools", "mcp_approval_request")
_FUNCTION_CALL_IDS_KEY = "__openai_function_call_ids__"


# ── Reasoning summaries an organisation is refused ───────────────────────────
# OpenAI answers 400 to ``reasoning.summary`` for an organisation it has not
# verified. The key is remembered per process so each later model built for it
# asks without the summary instead of paying the failed request again.

_SUMMARY_REFUSED: set = set()


def summary_key(api_key: Any, base_url: Optional[str]) -> str:
    """One entry per API address and key fingerprint."""
    # The model is built with the base URL its caller passed, often none, while
    # the refusal is recorded with the one the client settled on, which may
    # come from the environment. Both sides resolve it the same way, or a
    # refusal recorded under one spelling is never found under the other.
    base = (base_url or os.getenv("OPENAI_API_BASE") or os.getenv("OPENAI_BASE_URL")
            or DEFAULT_BASE_URL).rstrip("/")
    secret = api_key.get_secret_value() if hasattr(api_key, "get_secret_value") else (api_key or "")
    return f"{base}|{hashlib.sha256(str(secret).encode()).hexdigest()[:16]}"


def summary_refused(api_key: Any, base_url: Optional[str]) -> bool:
    return summary_key(api_key, base_url) in _SUMMARY_REFUSED


def is_summary_refusal(exc: BaseException) -> bool:
    """A 400 that rejects the reasoning summary itself, nothing else."""
    if not isinstance(exc, openai.BadRequestError):
        return False
    param = str(getattr(exc, "param", "") or "")
    return param == "reasoning.summary" or "summar" in str(exc).lower()


# ── Messages → request ────────────────────────────────────────────────────────

def _format_content(content: Any) -> Any:
    """Message content in the shape chat completions take: provider-specific
    blocks (thinking, tool_use, cache markers) removed, images as image_url."""
    if not (content and isinstance(content, list)):
        return content
    out: List[Any] = []
    for block in content:
        if not isinstance(block, dict):
            out.append(block)
            continue
        kind = block.get("type")
        if kind in ("tool_use", "thinking", "reasoning_content", "reasoning", "redacted_thinking"):
            continue
        if is_data_content_block(block):
            out.append(convert_to_openai_data_block(block))
            continue
        if kind == "image" and isinstance(block.get("source"), dict):
            source = block["source"]
            if source.get("type") == "base64" and source.get("media_type") and source.get("data"):
                out.append({"type": "image_url", "image_url": {
                    "url": f"data:{source['media_type']};base64,{source['data']}"}})
            elif source.get("type") == "url" and source.get("url"):
                out.append({"type": "image_url", "image_url": {"url": source["url"]}})
            continue
        if "cache_control" in block:
            # Anthropic's prompt-cache marker; every other server rejects the key.
            block = {k: v for k, v in block.items() if k != "cache_control"}
        out.append(block)
    return out


def _tool_call_dict(tc: Mapping[str, Any]) -> dict:
    return {"type": "function", "id": tc["id"],
            "function": {"name": tc["name"],
                         "arguments": json.dumps(tc["args"], ensure_ascii=False)}}


def _invalid_tool_call_dict(tc: Mapping[str, Any]) -> dict:
    return {"type": "function", "id": tc["id"],
            "function": {"name": tc["name"], "arguments": tc["args"]}}


def message_to_dict(message: BaseMessage) -> dict:
    """A LangChain message as one chat-completions message."""
    out: Dict[str, Any] = {"content": _format_content(message.content)}
    name = message.name or message.additional_kwargs.get("name")
    if name is not None:
        out["name"] = name
    if isinstance(message, ChatMessage):
        out["role"] = message.role
    elif isinstance(message, HumanMessage):
        out["role"] = "user"
    elif isinstance(message, AIMessage):
        out["role"] = "assistant"
        if message.tool_calls or message.invalid_tool_calls:
            out["tool_calls"] = ([_tool_call_dict(tc) for tc in message.tool_calls]
                                 + [_invalid_tool_call_dict(tc) for tc in message.invalid_tool_calls])
        elif "tool_calls" in message.additional_kwargs:
            out["tool_calls"] = [{k: v for k, v in tc.items() if k in ("id", "type", "function")}
                                 for tc in message.additional_kwargs["tool_calls"]]
        elif "function_call" in message.additional_kwargs:
            out["function_call"] = message.additional_kwargs["function_call"]
        if "tool_calls" in out or "function_call" in out:
            out["content"] = out["content"] or None
        if "audio" in message.additional_kwargs:
            raw = message.additional_kwargs["audio"]
            out["audio"] = {"id": raw["id"]} if "id" in raw else raw
    elif isinstance(message, SystemMessage):
        out["role"] = message.additional_kwargs.get("__openai_role__", "system")
    elif isinstance(message, FunctionMessage):
        out["role"] = "function"
    elif isinstance(message, ToolMessage):
        out = {"role": "tool", "content": out["content"], "tool_call_id": message.tool_call_id}
    else:
        raise TypeError(f"Got unknown message type {message}")
    return out


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _responses_block(block: dict) -> dict:
    """A chat-completions content block in the Responses input spelling."""
    kind = block.get("type")
    if kind == "text":
        return {"type": "input_text", "text": block.get("text", "")}
    if kind == "image_url":
        image = block.get("image_url") or {}
        new = {"type": "input_image", "image_url": image.get("url") if isinstance(image, dict) else image}
        if isinstance(image, dict) and image.get("detail"):
            new["detail"] = image["detail"]
        return new
    if kind == "file":
        return {"type": "input_file", **(block.get("file") or {})}
    return block


def _responses_tool_output(content: Any) -> Union[str, List[dict]]:
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(
            isinstance(b, dict) and b.get("type") in (
                "input_text", "input_image", "input_file", "text", "image_url", "file")
            for b in content):
        return [_responses_block(b) for b in content]
    return _stringify(content)


def _strip_index(block: dict) -> dict:
    new = {k: v for k, v in block.items() if k != "index"}
    if isinstance(new.get("summary"), list):
        new["summary"] = [{k: v for k, v in part.items() if k != "index"}
                          if isinstance(part, dict) else part for part in new["summary"]]
    return new


def _from_v0(message: AIMessage) -> AIMessage:
    """An assistant message read back from the Responses API, with the items
    this driver kept in ``additional_kwargs`` restored into the content list
    (reasoning first, then text, refusal and function calls) so the next
    request can carry them."""
    kwargs = message.additional_kwargs
    v0 = isinstance(message.content, list) and all(isinstance(b, dict) for b in message.content) and (
        any(k in kwargs for k in ("reasoning", "tool_outputs", "refusal", _FUNCTION_CALL_IDS_KEY))
        or (isinstance(message.id, str) and message.id.startswith("msg_")
            and str(message.response_metadata.get("id") or "").startswith("resp_")))
    if not v0:
        return message
    order = ["reasoning", "code_interpreter_call", "mcp_call", "image_generation_call", "text",
             "refusal", "function_call", "computer_call", "mcp_list_tools", "mcp_approval_request"]
    buckets: Dict[str, list] = {k: [] for k in order}
    unknown: list = []
    if kwargs.get("reasoning"):
        buckets["reasoning"].append(kwargs["reasoning"])
    if kwargs.get("refusal"):
        buckets["refusal"].append({"type": "refusal", "refusal": kwargs["refusal"]})
    for block in message.content:
        if isinstance(block, dict) and block.get("type") == "text":
            copy = dict(block)
            if isinstance(message.id, str) and message.id.startswith("msg_"):
                copy["id"] = message.id
            buckets["text"].append(copy)
        else:
            unknown.append(block)
    ids = kwargs.get(_FUNCTION_CALL_IDS_KEY) or {}
    for tc in message.tool_calls:
        call = {"type": "function_call", "name": tc["name"],
                "arguments": json.dumps(tc["args"], ensure_ascii=False), "call_id": tc["id"]}
        if ids.get(tc["id"]):
            call["id"] = ids[tc["id"]]
        buckets["function_call"].append(call)
    for block in kwargs.get("tool_outputs") or []:
        if isinstance(block, dict) and block.get("type") in buckets:
            buckets[block["type"]].append(block)
        else:
            unknown.append(block)
    content: list = []
    for key in order:
        content.extend(buckets[key])
    content.extend(unknown)
    new_kwargs = {k: v for k, v in kwargs.items() if k not in ("reasoning", "refusal", "tool_outputs")}
    return message.model_copy(update={
        "content": content, "additional_kwargs": new_kwargs,
        "id": message.response_metadata.get("id", message.id)}, deep=False)


def _assistant_items(message: AIMessage, items: list) -> None:
    """Append one assistant turn to a Responses ``input`` list: its text as a
    message item, reasoning and tool items passed back as they came, and a
    ``function_call`` for every tool call not already among them."""
    content = message.content
    if isinstance(content, str):
        if content:
            items.append({"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": content, "annotations": []}]})
    else:
        for block in content:
            if isinstance(block, str):
                if block:
                    items.append({"type": "message", "role": "assistant", "content": [
                        {"type": "output_text", "text": block, "annotations": []}]})
                continue
            if not (isinstance(block, dict) and block.get("type")):
                continue
            kind = block["type"]
            if kind in ("text", "output_text", "refusal"):
                if kind == "refusal":
                    new = {"type": "refusal", "refusal": block.get("refusal", "")}
                else:
                    new = {"type": "output_text", "text": block.get("text", ""),
                           "annotations": block.get("annotations") or []}
                msg_id = block.get("id")
                for item in items:
                    if msg_id and item.get("id") == msg_id:
                        item.setdefault("content", []).append(new)
                        break
                else:
                    item = {"type": "message", "role": "assistant", "content": [new]}
                    if msg_id:
                        item["id"] = msg_id
                    items.append(item)
            elif kind in _PASSTHROUGH_ITEMS:
                items.append(_strip_index(block))
            elif kind == "image_generation_call" and block.get("id"):
                items.append({"type": "image_generation_call", "id": block["id"]})
    present = {b.get("call_id") for b in items if b.get("type") in ("function_call", "custom_tool_call")}
    for tc in message.tool_calls:
        if tc["id"] not in present:
            items.append({"type": "function_call", "name": tc["name"],
                          "arguments": json.dumps(tc["args"], ensure_ascii=False), "call_id": tc["id"]})
    for tc in message.invalid_tool_calls:
        if tc["id"] not in present:
            items.append({"type": "function_call", "name": tc["name"],
                          "arguments": tc["args"] or "", "call_id": tc["id"]})


def responses_input(messages: Sequence[BaseMessage]) -> list:
    """The ``input`` list of a Responses request for a conversation."""
    items: list = []
    for lc_msg in messages:
        if isinstance(lc_msg, AIMessage):
            _assistant_items(_from_v0(lc_msg), items)
            continue
        msg = message_to_dict(lc_msg)
        msg.pop("name", None)
        role = msg["role"]
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": msg["tool_call_id"],
                          "output": _responses_tool_output(msg["content"])})
            continue
        if role in ("user", "system", "developer") and isinstance(msg.get("content"), list):
            blocks = []
            for block in msg["content"]:
                kind = block.get("type") if isinstance(block, dict) else None
                if kind in ("text", "image_url", "file"):
                    blocks.append(_responses_block(block))
                elif kind in ("input_text", "input_image", "input_file"):
                    blocks.append(block)
                elif kind == "mcp_approval_response":
                    items.append(block)
            if blocks:
                items.append({**msg, "content": blocks})
            continue
        items.append(msg)
    return items


def _to_response_format(schema: Union[dict, type], *, strict: Optional[bool]) -> dict:
    """``response_format`` for a schema given as a JSON Schema, an OpenAI
    function, an Anthropic-style tool dict or a pydantic class."""
    if isinstance(schema, dict) and schema.get("type") == "json_schema" and "json_schema" in schema:
        return schema
    if isinstance(schema, dict) and "name" in schema and "schema" in schema:
        return {"type": "json_schema", "json_schema": schema}
    if strict is None:
        strict = schema.get("strict") if isinstance(schema, dict) and isinstance(schema.get("strict"), bool) else False
    function = convert_to_openai_function(schema, strict=strict)
    function["schema"] = function.pop("parameters")
    return {"type": "json_schema", "json_schema": function}


def _text_options(payload: Mapping[str, Any]) -> Dict[str, Any]:
    text = payload.get("text")
    return dict(text) if isinstance(text, dict) else {}


def responses_payload(messages: Sequence[BaseMessage], payload: dict) -> dict:
    """A chat-completions request rewritten for ``/v1/responses``."""
    out = dict(payload)
    for legacy in ("max_tokens", "max_completion_tokens"):
        if legacy in out:
            out["max_output_tokens"] = out.pop(legacy)
    if "reasoning_effort" in out and "reasoning" not in out:
        out["reasoning"] = {"effort": out.pop("reasoning_effort")}
    out.pop("reasoning_effort", None)
    for key in _CHAT_ONLY_KEYS:
        out.pop(key, None)
    out["input"] = responses_input(messages)
    tools = out.pop("tools", None)
    if tools:
        out["tools"] = [{"type": "function", **t["function"]}
                        if isinstance(t, dict) and t.get("type") == "function" and "function" in t else t
                        for t in tools]
    choice = out.pop("tool_choice", None)
    if choice:
        if isinstance(choice, dict) and choice.get("type") == "function" and "function" in choice:
            out["tool_choice"] = {"type": "function", **choice["function"]}
        else:
            out["tool_choice"] = choice
    schema = out.pop("response_format", None)
    out.pop("strict", None)
    if schema:
        if schema == {"type": "json_object"}:
            fmt: dict = {"type": "json_object"}
        else:
            fmt = {"type": "json_schema", **_to_response_format(schema, strict=None)["json_schema"]}
        out["text"] = {**_text_options(out), "format": fmt}
    verbosity = out.pop("verbosity", None)
    if verbosity is not None:
        out["text"] = {**_text_options(out), "verbosity": verbosity}
    return out


# ── Response → messages ───────────────────────────────────────────────────────

def _usage_from_chat(usage: Mapping[str, Any]) -> UsageMetadata:
    inp = usage.get("prompt_tokens") or 0
    outp = usage.get("completion_tokens") or 0
    in_details = {"audio": (usage.get("prompt_tokens_details") or {}).get("audio_tokens"),
                  "cache_read": (usage.get("prompt_tokens_details") or {}).get("cached_tokens")}
    out_details = {"audio": (usage.get("completion_tokens_details") or {}).get("audio_tokens"),
                   "reasoning": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")}
    return UsageMetadata(
        input_tokens=inp, output_tokens=outp,
        total_tokens=usage.get("total_tokens") or inp + outp,
        input_token_details=InputTokenDetails(**{k: v for k, v in in_details.items() if v is not None}),
        output_token_details=OutputTokenDetails(**{k: v for k, v in out_details.items() if v is not None}),
    )


def _usage_from_responses(usage: Mapping[str, Any]) -> UsageMetadata:
    inp = usage.get("input_tokens") or 0
    outp = usage.get("output_tokens") or 0
    in_details = {"cache_read": (usage.get("input_tokens_details") or {}).get("cached_tokens")}
    out_details = {"reasoning": (usage.get("output_tokens_details") or {}).get("reasoning_tokens")}
    return UsageMetadata(
        input_tokens=inp, output_tokens=outp,
        total_tokens=usage.get("total_tokens") or inp + outp,
        input_token_details=InputTokenDetails(**{k: v for k, v in in_details.items() if v is not None}),
        output_token_details=OutputTokenDetails(**{k: v for k, v in out_details.items() if v is not None}),
    )


def _server_reasoning(d: Mapping[str, Any]) -> Optional[str]:
    """The reasoning a server returns beside the answer, when it is text."""
    for key in ("reasoning_content", "reasoning"):
        value = d.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def dict_to_message(d: Mapping[str, Any]) -> BaseMessage:
    """One chat-completions message as a LangChain message."""
    role = d.get("role")
    name = d.get("name")
    id_ = d.get("id")
    if role == "user":
        return HumanMessage(content=d.get("content", ""), id=id_, name=name)
    if role == "assistant":
        kwargs: Dict[str, Any] = {}
        if d.get("function_call"):
            kwargs["function_call"] = dict(d["function_call"])
        tool_calls, invalid = [], []
        if d.get("tool_calls"):
            kwargs["tool_calls"] = d["tool_calls"]
            for raw in d["tool_calls"]:
                try:
                    tool_calls.append(parse_tool_call(raw, return_id=True))
                except Exception as e:  # noqa: BLE001 - malformed arguments are reported as an invalid call
                    invalid.append(make_invalid_tool_call(raw, str(e)))
        if d.get("audio"):
            kwargs["audio"] = d["audio"]
        reasoning = _server_reasoning(d)
        if reasoning:
            kwargs["reasoning_content"] = reasoning
        return AIMessage(content=d.get("content", "") or "", additional_kwargs=kwargs, name=name,
                         id=id_, tool_calls=tool_calls, invalid_tool_calls=invalid)
    if role in ("system", "developer"):
        kwargs = {"__openai_role__": role} if role == "developer" else {}
        return SystemMessage(content=d.get("content", ""), name=name, id=id_, additional_kwargs=kwargs)
    if role == "function":
        return FunctionMessage(content=d.get("content", ""), name=str(d.get("name")), id=id_)
    if role == "tool":
        kwargs = {"name": d["name"]} if "name" in d else {}
        return ToolMessage(content=d.get("content", ""), tool_call_id=str(d.get("tool_call_id")),
                           additional_kwargs=kwargs, name=name, id=id_)
    return ChatMessage(content=d.get("content", ""), role=str(role), id=id_)


def _delta_to_chunk(delta: Mapping[str, Any], default_cls: Type[BaseMessageChunk]) -> BaseMessageChunk:
    id_ = delta.get("id")
    role = delta.get("role")
    content = delta.get("content") or ""
    kwargs: Dict[str, Any] = {}
    if delta.get("function_call"):
        call = dict(delta["function_call"])
        if call.get("name") is None and "name" in call:
            call["name"] = ""
        kwargs["function_call"] = call
    chunks = []
    if delta.get("tool_calls"):
        kwargs["tool_calls"] = delta["tool_calls"]
        for pos, rtc in enumerate(delta["tool_calls"]):
            fn = rtc.get("function") or {}
            # Servers without a per-call index (older llama.cpp) get one by position.
            index = rtc["index"] if rtc.get("index") is not None else pos
            chunks.append(_tool_call_chunk(name=fn.get("name"), args=fn.get("arguments"),
                                           id=rtc.get("id"), index=index))
    reasoning = _server_reasoning(delta)
    if reasoning:
        kwargs["reasoning_content"] = reasoning
    if role == "user" or default_cls is HumanMessageChunk:
        return HumanMessageChunk(content=content, id=id_)
    if role == "assistant" or default_cls is AIMessageChunk:
        return AIMessageChunk(content=content, additional_kwargs=kwargs, id=id_, tool_call_chunks=chunks)
    if role in ("system", "developer") or default_cls is SystemMessageChunk:
        return SystemMessageChunk(content=content, id=id_,
                                  additional_kwargs={"__openai_role__": "developer"} if role == "developer" else {})
    if role == "function" or default_cls is FunctionMessageChunk:
        return FunctionMessageChunk(content=content, name=delta["name"], id=id_)
    if role == "tool" or default_cls is ToolMessageChunk:
        return ToolMessageChunk(content=content, tool_call_id=delta["tool_call_id"], id=id_)
    if role or default_cls is ChatMessageChunk:
        return ChatMessageChunk(content=content, role=str(role), id=id_)
    return default_cls(content=content, id=id_)  # type: ignore[call-arg]


def chunk_to_generation(chunk: Mapping[str, Any],
                        default_cls: Type[BaseMessageChunk]) -> Optional[ChatGenerationChunk]:
    """One streamed chat-completions chunk as a generation chunk, or None for
    a chunk that carries nothing."""
    usage = chunk.get("usage")
    usage_md = _usage_from_chat(usage) if usage else None
    choices = chunk.get("choices") or []
    if not choices:
        return ChatGenerationChunk(message=default_cls(content="", usage_metadata=usage_md))  # type: ignore[call-arg]
    choice = choices[0]
    if choice.get("delta") is None:
        return None
    message = _delta_to_chunk(choice["delta"], default_cls)
    info: Dict[str, Any] = {}
    if choice.get("finish_reason"):
        info["finish_reason"] = choice["finish_reason"]
        for key in ("model", "system_fingerprint", "service_tier"):
            if chunk.get(key):
                info["model_name" if key == "model" else key] = chunk[key]
    if choice.get("logprobs"):
        info["logprobs"] = choice["logprobs"]
    if usage_md and isinstance(message, AIMessageChunk):
        message.usage_metadata = usage_md
    return ChatGenerationChunk(message=message, generation_info=info or None)


def _as_dict(value: Any, *, exclude_none: bool = False) -> dict:
    if isinstance(value, dict):
        return value
    return value.model_dump(exclude_none=exclude_none, mode="json")


def _to_v0(message: AIMessage, has_reasoning: bool = False) -> AIMessage:
    """The Responses content list in the shape the rest of the hub reads:
    the reasoning item in ``additional_kwargs["reasoning"]``, built-in tool
    results in ``tool_outputs``, text blocks without their item id."""
    if not isinstance(message.content, list):
        return message
    content: list = []
    for block in message.content:
        if not isinstance(block, dict):
            content.append(block)
            continue
        kind = block.get("type")
        if kind == "reasoning":
            block = {k: v for k, v in block.items() if k != "index"}
            if has_reasoning:
                block = {k: v for k, v in block.items() if k not in ("id", "type")}
            message.additional_kwargs["reasoning"] = block
        elif kind in _BUILTIN_TOOL_ITEMS:
            message.additional_kwargs.setdefault("tool_outputs", []).append(block)
        elif kind == "function_call":
            if block.get("call_id") and block.get("id"):
                message.additional_kwargs.setdefault(_FUNCTION_CALL_IDS_KEY, {})[block["call_id"]] = block["id"]
        elif kind == "refusal" and block.get("refusal"):
            message.additional_kwargs["refusal"] = block["refusal"]
        elif kind == "text":
            if "id" in block:
                message.id = block["id"]
            content.append({k: v for k, v in block.items() if k != "id"})
        elif set(block) == {"id", "index"} and str(block["id"]).startswith("msg_"):
            content.append({"index": block["index"]})
        else:
            content.append(block)
    message.content = content
    if isinstance(message.id, str) and message.id.startswith("resp_"):
        message.id = None
    return message


def result_from_responses(response: Any, *, structured: bool = False) -> ChatResult:
    """A ``/v1/responses`` response as a chat result."""
    resp = _as_dict(response, exclude_none=True)
    if resp.get("error"):
        raise ValueError(resp["error"])
    metadata = {k: v for k, v in resp.items() if k in (
        "created_at", "id", "incomplete_details", "metadata", "object", "status", "user",
        "model", "service_tier")}
    metadata["model_name"] = metadata.get("model")
    usage = _usage_from_responses(resp["usage"]) if resp.get("usage") else None
    blocks: list = []
    tool_calls, invalid = [], []
    kwargs: Dict[str, Any] = {}
    texts: List[str] = []
    for item in resp.get("output") or []:
        kind = item.get("type")
        if kind == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    texts.append(part.get("text", ""))
                    blocks.append({"type": "text", "text": part.get("text", ""),
                                   "annotations": part.get("annotations") or [], "id": item.get("id")})
                    if "parsed" in part:
                        kwargs["parsed"] = part["parsed"]
                elif part.get("type") == "refusal":
                    blocks.append({"type": "refusal", "refusal": part.get("refusal"), "id": item.get("id")})
        elif kind == "function_call":
            blocks.append(item)
            try:
                args = json.loads(item.get("arguments") or "{}", strict=False)
                error = None
            except json.JSONDecodeError as e:
                args, error = item.get("arguments"), str(e)
            call = {"name": item.get("name"), "args": args, "id": item.get("call_id")}
            if error is None:
                tool_calls.append({"type": "tool_call", **call})
            else:
                invalid.append({"type": "invalid_tool_call", **call, "error": error})
        elif kind == "custom_tool_call":
            blocks.append(item)
            tool_calls.append({"type": "tool_call", "name": item.get("name"),
                               "args": {"__arg1": item.get("input")}, "id": item.get("call_id")})
        elif kind in _BUILTIN_TOOL_ITEMS or kind == "reasoning":
            blocks.append(item)
    output_text = "".join(texts)
    fmt = ((resp.get("text") or {}).get("format") or {}) if isinstance(resp.get("text"), dict) else {}
    if structured and "parsed" not in kwargs and output_text and fmt.get("type") == "json_schema":
        try:
            kwargs["parsed"] = json.loads(output_text)
        except json.JSONDecodeError:
            log.debug("responses: structured output was not JSON", exc_info=True)
    message = AIMessage(content=blocks, id=resp.get("id"), usage_metadata=usage,
                        response_metadata=metadata, additional_kwargs=kwargs,
                        tool_calls=tool_calls, invalid_tool_calls=invalid)
    return ChatResult(generations=[ChatGeneration(message=_to_v0(message))])


class _ResponsesStreamState:
    """Indexes langchain_core needs to merge streamed Responses items."""

    def __init__(self) -> None:
        self.index = -1
        self.output_index = -1
        self.sub_index = -1
        self.has_reasoning = False

    def advance(self, output_idx: int, sub_idx: Optional[int] = None) -> None:
        if sub_idx is None:
            if self.output_index != output_idx:
                self.index += 1
        else:
            if self.output_index != output_idx or self.sub_index != sub_idx:
                self.index += 1
            self.sub_index = sub_idx
        self.output_index = output_idx


def responses_event_to_chunk(event: Any, state: _ResponsesStreamState, *,
                             structured: bool = False) -> Optional[ChatGenerationChunk]:
    """One Responses stream event as a generation chunk, or None."""
    ev = _as_dict(event, exclude_none=True)
    kind = ev.get("type")
    content: list = []
    call_chunks: list = []
    kwargs: Dict[str, Any] = {}
    metadata: Dict[str, Any] = {}
    usage = None
    id_ = None
    item = ev.get("item") or {}
    if kind == "response.output_text.delta":
        state.advance(ev["output_index"], ev.get("content_index"))
        content.append({"type": "text", "text": ev.get("delta", ""), "index": state.index})
    elif kind == "response.output_text.annotation.added":
        state.advance(ev["output_index"], ev.get("content_index"))
        content.append({"annotations": [ev.get("annotation")], "index": state.index})
    elif kind == "response.output_text.done":
        content.append({"id": ev.get("item_id"), "index": state.index})
    elif kind == "response.created":
        id_ = (ev.get("response") or {}).get("id")
        metadata["id"] = id_
    elif kind == "response.completed":
        final = result_from_responses(ev["response"], structured=structured).generations[0].message
        if final.additional_kwargs.get("parsed") is not None:
            kwargs["parsed"] = final.additional_kwargs["parsed"]
        usage = getattr(final, "usage_metadata", None)
        metadata = {k: v for k, v in final.response_metadata.items() if k != "id"}
    elif kind == "response.output_item.added" and item.get("type") == "message":
        id_ = item.get("id")
    elif kind == "response.output_item.added" and item.get("type") == "function_call":
        state.advance(ev["output_index"])
        call_chunks.append({"type": "tool_call_chunk", "name": item.get("name"),
                            "args": item.get("arguments"), "id": item.get("call_id"), "index": state.index})
        content.append({"type": "function_call", "name": item.get("name"),
                        "arguments": item.get("arguments"), "call_id": item.get("call_id"),
                        "id": item.get("id"), "index": state.index})
    elif kind == "response.output_item.done" and item.get("type") in _BUILTIN_TOOL_ITEMS:
        state.advance(ev["output_index"])
        content.append({**item, "index": state.index})
    elif kind == "response.output_item.done" and item.get("type") == "custom_tool_call":
        state.advance(ev["output_index"])
        content.append({**item, "index": state.index})
        call_chunks.append({"type": "tool_call_chunk", "name": item.get("name"),
                            "args": json.dumps({"__arg1": item.get("input")}),
                            "id": item.get("call_id"), "index": state.index})
    elif kind == "response.function_call_arguments.delta":
        state.advance(ev["output_index"])
        call_chunks.append({"type": "tool_call_chunk", "args": ev.get("delta", ""), "index": state.index})
        content.append({"type": "function_call", "arguments": ev.get("delta", ""), "index": state.index})
    elif kind == "response.refusal.done":
        content.append({"type": "refusal", "refusal": ev.get("refusal")})
    elif kind == "response.output_item.added" and item.get("type") == "reasoning":
        state.advance(ev["output_index"])
        content.append({**item, "index": state.index})
    elif kind == "response.reasoning_summary_part.added":
        state.advance(ev["output_index"])
        content.append({"type": "reasoning", "index": state.index, "summary": [
            {"index": ev.get("summary_index"), "type": "summary_text", "text": ""}]})
    elif kind == "response.reasoning_summary_text.delta":
        state.advance(ev["output_index"])
        content.append({"type": "reasoning", "index": state.index, "summary": [
            {"index": ev.get("summary_index"), "type": "summary_text", "text": ev.get("delta", "")}]})
    else:
        return None
    message = AIMessageChunk(content=content, tool_call_chunks=call_chunks, usage_metadata=usage,
                             response_metadata=metadata, additional_kwargs=kwargs, id=id_)
    _to_v0(message, has_reasoning=state.has_reasoning)
    if "reasoning" in message.additional_kwargs:
        state.has_reasoning = True
    return ChatGenerationChunk(message=message)


def _add_usage(total: Any, new: Any) -> Any:
    if isinstance(new, (int, float)) and isinstance(total, (int, float)):
        return total + new
    if isinstance(new, dict):
        base = total if isinstance(total, dict) else {}
        return {k: _add_usage(base.get(k, 0), v) for k, v in new.items() if v is not None}
    return new


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) and b.get("type") == "text"
                       else (b if isinstance(b, str) else "") for b in content)
    return ""


def _parse_structured(message: AIMessage, schema: Any) -> Any:
    """The structured value of a json_schema answer: what the server parsed,
    else the text as JSON; a pydantic class when the schema is one."""
    parsed = message.additional_kwargs.get("parsed")
    if parsed is None:
        if message.additional_kwargs.get("refusal"):
            raise ValueError(f"the model refused: {message.additional_kwargs['refusal']}")
        if message.tool_calls:
            return None
        parsed = json.loads(_content_text(message.content))
    if isinstance(schema, type) and is_basemodel_subclass(schema) and isinstance(parsed, dict):
        return schema(**parsed)
    return parsed


# ── The model ─────────────────────────────────────────────────────────────────

class OpenAIChatModel(BaseChatModel):
    """A chat model on the OpenAI protocol, either endpoint.

    Constructed with the same keyword names ``ChatOpenAI`` took (``model``,
    ``api_key``, ``base_url``, ``timeout``, ``max_tokens``, ``streaming``,
    ``reasoning_effort``, ``reasoning``, ``use_responses_api``,
    ``default_headers``, ``stream_usage``), so the builders need no change.
    """

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True,
                              protected_namespaces=())

    model_name: str = Field(alias="model")
    openai_api_key: Optional[SecretStr] = Field(default=None, alias="api_key")
    openai_api_base: Optional[str] = Field(default=None, alias="base_url")
    openai_organization: Optional[str] = Field(default=None, alias="organization")
    request_timeout: Any = Field(default=None, alias="timeout")
    max_retries: Optional[int] = None
    default_headers: Optional[Mapping[str, str]] = None
    default_query: Optional[Mapping[str, object]] = None
    http_client: Any = Field(default=None, exclude=True)
    http_async_client: Any = Field(default=None, exclude=True)
    root_client: Any = Field(default=None, exclude=True)
    root_async_client: Any = Field(default=None, exclude=True)

    temperature: Optional[float] = None
    #: Sent whatever else the request says: the builder's decision for models
    #: whose temperature depends on the reasoning effort.
    forced_temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    seed: Optional[int] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    stop: Optional[Union[List[str], str]] = Field(default=None, alias="stop_sequences")
    streaming: bool = False
    #: Ask for usage on streamed completions. None: yes for OpenAI's own API,
    #: no for another address, where the option may be rejected.
    stream_usage: Optional[bool] = None
    reasoning_effort: Optional[str] = None
    #: The Responses API ``reasoning`` parameter; setting it sends the request there.
    reasoning: Optional[Dict[str, Any]] = None
    verbosity: Optional[str] = None
    #: True or False decides the endpoint; None lets the request decide.
    use_responses_api: Optional[bool] = None
    extra_body: Optional[Mapping[str, Any]] = None
    model_kwargs: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _build_clients(self) -> "OpenAIChatModel":
        base = self.openai_api_base or os.environ.get("OPENAI_API_BASE") or None
        if base is None and os.environ.get("OPENAI_BASE_URL") == "":
            # The SDK reads an empty variable as the address itself (``URL('')``)
            # and every call fails with a connection error. Name the default.
            base = DEFAULT_BASE_URL
        self.openai_api_base = base
        self.openai_organization = (self.openai_organization or os.getenv("OPENAI_ORG_ID")
                                    or os.getenv("OPENAI_ORGANIZATION"))
        key = self.openai_api_key.get_secret_value() if self.openai_api_key else os.environ.get("OPENAI_API_KEY")
        params: Dict[str, Any] = {
            "api_key": key, "organization": self.openai_organization, "base_url": base,
            "timeout": self.request_timeout, "default_headers": self.default_headers,
            "default_query": self.default_query,
        }
        if self.max_retries is not None:
            params["max_retries"] = self.max_retries
        if self.root_client is None:
            sync = {"http_client": self.http_client} if self.http_client is not None else {}
            self.root_client = openai.OpenAI(**params, **sync)
        if self.root_async_client is None:
            asyn = {"http_client": self.http_async_client} if self.http_async_client is not None else {}
            self.root_async_client = openai.AsyncOpenAI(**params, **asyn)
        return self

    # -- identity ------------------------------------------------------------

    @property
    def _llm_type(self) -> str:
        return "openai-chat"

    @property
    def _default_params(self) -> Dict[str, Any]:
        optional = {
            "temperature": self.temperature, "max_tokens": self.max_tokens, "top_p": self.top_p,
            "seed": self.seed, "presence_penalty": self.presence_penalty,
            "frequency_penalty": self.frequency_penalty, "stop": self.stop or None,
            "reasoning_effort": self.reasoning_effort, "reasoning": self.reasoning,
            "verbosity": self.verbosity, "extra_body": self.extra_body,
        }
        return {"model": self.model_name, "stream": self.streaming,
                **{k: v for k, v in optional.items() if v is not None}, **self.model_kwargs}

    @property
    def _identifying_params(self) -> Dict[str, Any]:
        return {"model_name": self.model_name, **self._default_params}

    def _get_ls_params(self, stop: Optional[List[str]] = None, **kwargs: Any) -> LangSmithParams:
        params = LangSmithParams(ls_provider="openai", ls_model_name=self.model_name,
                                 ls_model_type="chat", ls_temperature=self.temperature)
        if self.max_tokens:
            params["ls_max_tokens"] = self.max_tokens
        stops = stop or self.stop
        if stops:
            params["ls_stop"] = [stops] if isinstance(stops, str) else list(stops)
        return params

    @property
    def is_openai_api(self) -> bool:
        """True when the requests go to OpenAI's own API."""
        return not self.openai_api_base or "api.openai.com" in self.openai_api_base

    # -- tokens --------------------------------------------------------------
    # No tokenizer is loaded for an estimate the hub never prices by: these
    # keep the default (GPT-2 through transformers) out of every start.

    def get_num_tokens(self, text: str) -> int:
        return max(1, len(text or "") // 4)

    def get_num_tokens_from_messages(self, messages: List[BaseMessage], tools: Any = None) -> int:
        return sum(3 + self.get_num_tokens(_content_text(m.content)) for m in messages)

    # -- request ---------------------------------------------------------------

    def _use_responses_api(self, payload: Mapping[str, Any]) -> bool:
        if isinstance(self.use_responses_api, bool):
            return self.use_responses_api
        if self.reasoning is not None:
            return True
        tools = payload.get("tools") or []
        if any(isinstance(t, dict) and t.get("type") not in (None, "function") for t in tools):
            return True
        return bool(_RESPONSES_ONLY_KEYS & set(payload))

    def _should_stream_usage(self, stream_usage: Optional[bool], payload: Mapping[str, Any]) -> bool:
        for source in (stream_usage, (payload.get("stream_options") or {}).get("include_usage"),
                       (self.model_kwargs.get("stream_options") or {}).get("include_usage"),
                       self.stream_usage):
            if isinstance(source, bool):
                return source
        return self.is_openai_api

    def _prepare(self, input_: LanguageModelInput, stop: Optional[List[str]], kwargs: Dict[str, Any],
                 *, stream_usage: Optional[bool] = None) -> Tuple[dict, bool]:
        messages = self._convert_input(input_).to_messages()
        if stop is not None:
            kwargs["stop"] = stop
        payload = {**self._default_params, **kwargs}
        if self.forced_temperature is not None:
            payload["temperature"] = self.forced_temperature
        if payload.get("stream") and self._should_stream_usage(stream_usage, payload):
            payload["stream_options"] = {"include_usage": True}
        payload.pop("ls_structured_output_format", None)
        if payload.get("response_format") not in (None, {"type": "json_object"}):
            payload["response_format"] = _to_response_format(payload["response_format"],
                                                             strict=payload.pop("strict", None))
        payload.pop("strict", None)
        if self._use_responses_api(payload):
            return responses_payload(messages, payload), True
        payload["messages"] = [message_to_dict(m) for m in messages]
        return payload, False

    def _get_request_payload(self, input_: LanguageModelInput, *, stop: Optional[List[str]] = None,
                             **kwargs: Any) -> dict:
        """The request body a call with these arguments sends, either shape."""
        return self._prepare(input_, stop, dict(kwargs))[0]

    def _drop_summary_after(self, exc: BaseException) -> bool:
        """True when ``exc`` was a refusal of the reasoning summary and the
        request may be repeated without one."""
        if not (isinstance(self.reasoning, dict) and "summary" in self.reasoning
                and is_summary_refusal(exc)):
            return False
        _SUMMARY_REFUSED.add(summary_key(self.openai_api_key, self.openai_api_base))
        self.reasoning = {k: v for k, v in self.reasoning.items() if k != "summary"}
        return True

    # -- results -------------------------------------------------------------

    def _chat_result(self, response: Any, *, structured: bool = False) -> ChatResult:
        resp = _as_dict(response)
        if resp.get("error"):
            raise ValueError(resp["error"])
        choices = resp.get("choices")
        if choices is None:
            raise ValueError(f"The server answered without choices: {sorted(resp)}")
        usage = resp.get("usage")
        generations = []
        for choice in choices:
            message = dict_to_message(choice["message"])
            if usage and isinstance(message, AIMessage):
                message.usage_metadata = _usage_from_chat(usage)
            if structured and isinstance(message, AIMessage) and "parsed" not in message.additional_kwargs:
                refusal = choice["message"].get("refusal")
                if refusal:
                    message.additional_kwargs["refusal"] = refusal
                elif isinstance(message.content, str) and message.content.strip():
                    try:
                        message.additional_kwargs["parsed"] = json.loads(message.content)
                    except json.JSONDecodeError:
                        log.debug("chat: structured output was not JSON", exc_info=True)
            info: Dict[str, Any] = {"finish_reason": choice.get("finish_reason")}
            if "logprobs" in choice:
                info["logprobs"] = choice["logprobs"]
            generations.append(ChatGeneration(message=message, generation_info=info))
        llm_output = {"token_usage": usage, "model_name": resp.get("model", self.model_name),
                      "system_fingerprint": resp.get("system_fingerprint", "")}
        for key in ("id", "service_tier"):
            if key in resp:
                llm_output[key] = resp[key]
        return ChatResult(generations=generations, llm_output=llm_output)

    def _combine_llm_outputs(self, llm_outputs: List[Optional[dict]]) -> dict:
        usage: Dict[str, Any] = {}
        fingerprint = None
        for out in llm_outputs:
            if not out:
                continue
            if out.get("token_usage"):
                usage = _add_usage(usage, out["token_usage"])
            fingerprint = fingerprint or out.get("system_fingerprint")
        combined: Dict[str, Any] = {"token_usage": usage, "model_name": self.model_name}
        if fingerprint:
            combined["system_fingerprint"] = fingerprint
        return combined

    # -- calls ---------------------------------------------------------------

    def _generate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None,
                  run_manager: Optional[CallbackManagerForLLMRun] = None, **kwargs: Any) -> ChatResult:
        try:
            return self._generate_once(messages, stop=stop, run_manager=run_manager, **kwargs)
        except Exception as exc:
            if not self._drop_summary_after(exc):
                raise
            return self._generate_once(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _generate_once(self, messages: List[BaseMessage], stop: Optional[List[str]] = None,
                       run_manager: Optional[CallbackManagerForLLMRun] = None, **kwargs: Any) -> ChatResult:
        if self.streaming:
            return generate_from_stream(self._stream_once(messages, stop=stop, run_manager=run_manager, **kwargs))
        structured = kwargs.get("response_format") is not None
        payload, responses = self._prepare(messages, stop, dict(kwargs))
        payload.pop("stream", None)
        if responses:
            return result_from_responses(self.root_client.responses.create(**payload), structured=structured)
        return self._chat_result(self.root_client.chat.completions.create(**payload), structured=structured)

    async def _agenerate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None,
                         run_manager: Optional[AsyncCallbackManagerForLLMRun] = None, **kwargs: Any) -> ChatResult:
        try:
            return await self._agenerate_once(messages, stop=stop, run_manager=run_manager, **kwargs)
        except Exception as exc:
            if not self._drop_summary_after(exc):
                raise
            return await self._agenerate_once(messages, stop=stop, run_manager=run_manager, **kwargs)

    async def _agenerate_once(self, messages: List[BaseMessage], stop: Optional[List[str]] = None,
                              run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
                              **kwargs: Any) -> ChatResult:
        if self.streaming:
            return await agenerate_from_stream(
                self._astream_once(messages, stop=stop, run_manager=run_manager, **kwargs))
        structured = kwargs.get("response_format") is not None
        payload, responses = self._prepare(messages, stop, dict(kwargs))
        payload.pop("stream", None)
        if responses:
            resp = await self.root_async_client.responses.create(**payload)
            return result_from_responses(resp, structured=structured)
        resp = await self.root_async_client.chat.completions.create(**payload)
        return self._chat_result(resp, structured=structured)

    def _stream(self, messages: List[BaseMessage], stop: Optional[List[str]] = None,
                run_manager: Optional[CallbackManagerForLLMRun] = None, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        # The refusal comes before the first chunk, so nothing was yielded yet
        # when the stream is opened again.
        started = False
        try:
            for chunk in self._stream_once(messages, stop=stop, run_manager=run_manager, **kwargs):
                started = True
                yield chunk
        except Exception as exc:
            if started or not self._drop_summary_after(exc):
                raise
            yield from self._stream_once(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _stream_once(self, messages: List[BaseMessage], stop: Optional[List[str]] = None,
                     run_manager: Optional[CallbackManagerForLLMRun] = None, *,
                     stream_usage: Optional[bool] = None, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        kwargs["stream"] = True
        structured = kwargs.get("response_format") is not None
        payload, responses = self._prepare(messages, stop, kwargs, stream_usage=stream_usage)
        if responses:
            state = _ResponsesStreamState()
            with self.root_client.responses.create(**payload) as stream:
                for event in stream:
                    gen = responses_event_to_chunk(event, state, structured=structured)
                    if gen is None:
                        continue
                    if run_manager:
                        run_manager.on_llm_new_token(gen.text, chunk=gen)
                    yield gen
            return
        default_cls: Type[BaseMessageChunk] = AIMessageChunk
        with self.root_client.chat.completions.create(**payload) as stream:
            for raw in stream:
                gen = chunk_to_generation(_as_dict(raw), default_cls)
                if gen is None:
                    continue
                default_cls = gen.message.__class__
                if run_manager:
                    run_manager.on_llm_new_token(gen.text, chunk=gen,
                                                 logprobs=(gen.generation_info or {}).get("logprobs"))
                yield gen

    async def _astream(self, messages: List[BaseMessage], stop: Optional[List[str]] = None,
                       run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
                       **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        started = False
        try:
            async for chunk in self._astream_once(messages, stop=stop, run_manager=run_manager, **kwargs):
                started = True
                yield chunk
        except Exception as exc:
            if started or not self._drop_summary_after(exc):
                raise
            async for chunk in self._astream_once(messages, stop=stop, run_manager=run_manager, **kwargs):
                yield chunk

    async def _astream_once(self, messages: List[BaseMessage], stop: Optional[List[str]] = None,
                            run_manager: Optional[AsyncCallbackManagerForLLMRun] = None, *,
                            stream_usage: Optional[bool] = None,
                            **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        kwargs["stream"] = True
        structured = kwargs.get("response_format") is not None
        payload, responses = self._prepare(messages, stop, kwargs, stream_usage=stream_usage)
        if responses:
            state = _ResponsesStreamState()
            async with await self.root_async_client.responses.create(**payload) as stream:
                async for event in stream:
                    gen = responses_event_to_chunk(event, state, structured=structured)
                    if gen is None:
                        continue
                    if run_manager:
                        await run_manager.on_llm_new_token(gen.text, chunk=gen)
                    yield gen
            return
        default_cls: Type[BaseMessageChunk] = AIMessageChunk
        async with await self.root_async_client.chat.completions.create(**payload) as stream:
            async for raw in stream:
                gen = chunk_to_generation(_as_dict(raw), default_cls)
                if gen is None:
                    continue
                default_cls = gen.message.__class__
                if run_manager:
                    await run_manager.on_llm_new_token(gen.text, chunk=gen,
                                                       logprobs=(gen.generation_info or {}).get("logprobs"))
                yield gen

    # -- tools and schemas -----------------------------------------------------

    def bind_tools(self, tools: Sequence[Union[Dict[str, Any], type, Callable, BaseTool]], *,
                   tool_choice: Optional[Union[dict, str, bool]] = None, strict: Optional[bool] = None,
                   parallel_tool_calls: Optional[bool] = None,
                   **kwargs: Any) -> Runnable[LanguageModelInput, BaseMessage]:
        """Bind tools in the OpenAI shape. ``tool_choice`` takes a tool name,
        ``auto``, ``none``, ``required`` or ``any`` (both: at least one call),
        True (the same) or the OpenAI dict."""
        if parallel_tool_calls is not None:
            kwargs["parallel_tool_calls"] = parallel_tool_calls
        formatted = [convert_to_openai_tool(t, strict=strict) for t in tools]
        names = [t["function"]["name"] for t in formatted if "function" in t] + \
                [t["name"] for t in formatted if "function" not in t and "name" in t]
        if tool_choice:
            if isinstance(tool_choice, str):
                if tool_choice in names:
                    tool_choice = {"type": "function", "function": {"name": tool_choice}}
                elif tool_choice == "any":
                    tool_choice = "required"
            elif isinstance(tool_choice, bool):
                tool_choice = "required"
            elif not isinstance(tool_choice, dict):
                raise ValueError(f"Unrecognised tool_choice {tool_choice!r}")
            kwargs["tool_choice"] = tool_choice
        return super().bind(tools=formatted, **kwargs)

    def with_structured_output(self, schema: Optional[Union[Dict[str, Any], type]] = None, *,
                               method: Literal["function_calling", "json_mode", "json_schema"] = "json_schema",
                               include_raw: bool = False, strict: Optional[bool] = None,
                               **kwargs: Any) -> Runnable[LanguageModelInput, Any]:
        """A runnable answering in the shape of ``schema``.

        ``json_schema`` uses the server's structured outputs (``response_format``
        on chat completions, ``text.format`` on the Responses API);
        ``function_calling`` forces one tool call; ``json_mode`` asks for any
        JSON object. With ``include_raw`` the output is ``{"raw", "parsed",
        "parsing_error"}`` and a parse failure never raises.
        """
        if kwargs:
            raise ValueError(f"Received unsupported arguments {kwargs}")
        if schema is None and method != "json_mode":
            raise ValueError("schema must be specified when method is not 'json_mode'")
        pydantic_schema = isinstance(schema, type) and is_basemodel_subclass(schema)
        if method == "function_calling":
            tool = convert_to_openai_tool(schema, strict=strict)  # type: ignore[arg-type]
            name = tool["function"]["name"]
            llm = self.bind_tools([schema], tool_choice=name, strict=strict,  # type: ignore[list-item]
                                  ls_structured_output_format={"kwargs": {"method": method, "strict": strict},
                                                               "schema": tool})
            parser: Runnable = (PydanticToolsParser(tools=[schema], first_tool_only=True)  # type: ignore[list-item]
                                if pydantic_schema else
                                JsonOutputKeyToolsParser(key_name=name, first_tool_only=True))
        elif method == "json_mode":
            llm = self.bind(response_format={"type": "json_object"},
                            ls_structured_output_format={"kwargs": {"method": method},
                                                         "schema": schema})
            parser = (PydanticOutputParser(pydantic_object=schema)  # type: ignore[arg-type]
                      if pydantic_schema else JsonOutputParser())
        elif method == "json_schema":
            response_format = _to_response_format(schema, strict=strict)  # type: ignore[arg-type]
            llm = self.bind(response_format=response_format,
                            ls_structured_output_format={"kwargs": {"method": method, "strict": strict},
                                                         "schema": convert_to_openai_tool(schema)})  # type: ignore[arg-type]
            parser = RunnableLambda(lambda m, _s=schema: _parse_structured(m, _s))
        else:
            raise ValueError(f"Unrecognised method {method!r}")
        if include_raw:
            assign = RunnablePassthrough.assign(parsed=itemgetter("raw") | parser,
                                                parsing_error=lambda _: None)
            none = RunnablePassthrough.assign(parsed=lambda _: None)
            return RunnableMap(raw=llm) | assign.with_fallbacks([none], exception_key="parsing_error")
        return llm | parser


__all__ = [
    "DEFAULT_BASE_URL", "OpenAIChatModel", "chunk_to_generation", "dict_to_message",
    "is_summary_refusal", "message_to_dict", "responses_event_to_chunk", "responses_input",
    "responses_payload", "result_from_responses", "summary_key", "summary_refused",
]
