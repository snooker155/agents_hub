"""The hub's own Anthropic driver (providers/anthropic_driver.py).

It replaced ``langchain-anthropic`` under every Claude model. These tests pin,
against a mock transport, the request body (system, merged turns, tool
results, cache markers, thinking), the messages read back (text, thinking,
tool calls, usage with the cache split, stop reason), streaming, the beta
endpoint for ``betas`` and what the package dropped: the server's
``applied_edits`` report on a streamed call.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from providers.anthropic_driver import AnthropicChatModel, convert_to_anthropic_tool, format_messages


@tool
def ping(text: str) -> str:
    """Echo a string back."""
    return text


def _sse(events: List[dict]) -> httpx.Response:
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


class Server:
    def __init__(self) -> None:
        self.sent: List[Dict[str, Any]] = []
        self.reply: Dict[str, Any] = {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-4-6-served",
            "content": [{"type": "thinking", "thinking": "hmm", "signature": "sig"},
                        {"type": "text", "text": "ok"},
                        {"type": "tool_use", "id": "tu_1", "name": "ping", "input": {"text": "a"}}],
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 5,
                      "cache_creation_input_tokens": 1},
            "context_management": {"applied_edits": [
                {"type": "clear_tool_uses_20250919", "cleared_tool_uses": 2, "cleared_input_tokens": 300}]}}
        self.events: List[dict] = [
            {"type": "message_start", "message": {
                "id": "msg_2", "type": "message", "role": "assistant", "model": "claude-opus-4-6",
                "content": [], "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 50, "output_tokens": 1}}},
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "let me "}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Hel"}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "lo"}},
            {"type": "content_block_stop", "index": 1},
            {"type": "content_block_start", "index": 2,
             "content_block": {"type": "tool_use", "id": "tu_2", "name": "ping", "input": {}}},
            {"type": "content_block_delta", "index": 2,
             "delta": {"type": "input_json_delta", "partial_json": '{"text": '}},
            {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '"b"}'}},
            {"type": "content_block_stop", "index": 2},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
             "usage": {"output_tokens": 20},
             "context_management": {"applied_edits": [{"type": "clear_tool_uses_20250919", "cleared_tool_uses": 1}]}},
            {"type": "message_stop"},
        ]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.sent.append({"url": str(request.url), "headers": dict(request.headers), "body": body})
        if body.get("stream"):
            return _sse(self.events)
        return httpx.Response(200, json=self.reply)

    @property
    def last(self) -> Dict[str, Any]:
        return self.sent[-1]["body"]

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def async_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


@pytest.fixture
def server(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    return Server()


def _model(server: Server, **kw) -> AnthropicChatModel:
    return AnthropicChatModel(model="claude-opus-4-6", api_key="ak-test", max_tokens=100,
                              http_client=server.client(), **kw)


def _merge(chunks):
    agg = chunks[0]
    for c in chunks[1:]:
        agg = agg + c
    return agg


# ── Request ──────────────────────────────────────────────────────────────────

def test_the_request_body(server):
    llm = _model(server, temperature=0.3, thinking={"type": "adaptive"}, default_headers={"X-Hub": "1"})
    llm.bind_tools([ping]).invoke([
        SystemMessage(content=[{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]),
        HumanMessage(content="hi")])
    body = server.last
    assert body["model"] == "claude-opus-4-6" and body["max_tokens"] == 100 and body["temperature"] == 0.3
    # The cache marker stays: this is the provider that reads it.
    assert body["system"] == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["thinking"] == {"type": "adaptive"}
    assert body["tools"] == [{"name": "ping", "description": "Echo a string back.",
                              "input_schema": {"properties": {"text": {"type": "string"}},
                                               "required": ["text"], "type": "object"}}]
    assert "betas" not in body and "stream" not in body
    headers = server.sent[-1]["headers"]
    assert headers["x-api-key"] == "ak-test" and headers["x-hub"] == "1"
    assert server.sent[-1]["url"] == "https://api.anthropic.com/v1/messages"


def test_the_conversation_is_written_the_way_the_api_wants():
    turn = AIMessage(content="", tool_calls=[{"name": "ping", "args": {"text": "a"}, "id": "tu_1"}])
    system, messages = format_messages([
        SystemMessage(content="s"), HumanMessage(content="hi"), turn,
        ToolMessage(content="pong", tool_call_id="tu_1"), HumanMessage(content="and this")])
    assert system == "s"
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[1]["content"] == [{"type": "tool_use", "name": "ping", "input": {"text": "a"}, "id": "tu_1"}]
    # The tool result and the person's words share one user turn, result first.
    assert messages[2]["content"] == [
        {"type": "tool_result", "content": "pong", "tool_use_id": "tu_1", "is_error": False},
        {"type": "text", "text": "and this"}]


def test_other_providers_blocks_and_empty_text_are_dropped():
    message = AIMessage(content=[{"type": "reasoning", "summary": []}, {"type": "text", "text": "  "},
                                 {"type": "text", "text": "kept"},
                                 {"type": "thinking", "thinking": "t", "signature": "s", "extra": 1}])
    _system, messages = format_messages([HumanMessage(content="hi"), message])
    assert messages[1]["content"] == [{"type": "text", "text": "kept"},
                                      {"type": "thinking", "thinking": "t", "signature": "s"}]


def test_tool_choice_strict_and_pass_through_dicts(server):
    llm = _model(server)
    assert llm.bind_tools([ping], tool_choice="ping").kwargs["tool_choice"] == {"type": "tool", "name": "ping"}
    assert llm.bind_tools([ping], tool_choice="any").kwargs["tool_choice"] == {"type": "any"}
    assert llm.bind_tools([ping], tool_choice="required").kwargs["tool_choice"] == {"type": "any"}
    assert llm.bind_tools([ping], parallel_tool_calls=False).kwargs["tool_choice"] == {
        "type": "auto", "disable_parallel_tool_use": True}
    assert llm.bind_tools([ping], strict=True).kwargs["tools"][0]["strict"] is True
    deferred = {**convert_to_anthropic_tool(ping), "defer_loading": True}
    assert llm.bind_tools([deferred]).kwargs["tools"][0]["defer_loading"] is True
    builtin = {"type": "web_search_20250305", "name": "web_search"}
    assert llm.bind_tools([builtin]).kwargs["tools"][0] == builtin


def test_betas_go_to_the_beta_endpoint(server):
    llm = _model(server)
    llm.bind(betas=["context-management-2025-06-27"],
             context_management={"edits": [{"type": "clear_tool_uses_20250919"}]}).invoke("hi")
    assert server.sent[-1]["url"].endswith("/v1/messages?beta=true")
    assert server.sent[-1]["headers"]["anthropic-beta"] == "context-management-2025-06-27"
    assert server.last["context_management"] == {"edits": [{"type": "clear_tool_uses_20250919"}]}
    assert "betas" not in server.last


# ── Response ─────────────────────────────────────────────────────────────────

def test_the_answer_is_read_in_the_hubs_shape(server):
    out = _model(server).bind_tools([ping]).invoke("hi")
    assert out.content == [{"type": "thinking", "thinking": "hmm", "signature": "sig"},
                           {"type": "text", "text": "ok"},
                           {"type": "tool_use", "id": "tu_1", "name": "ping", "input": {"text": "a"}}]
    assert out.tool_calls == [{"name": "ping", "args": {"text": "a"}, "id": "tu_1", "type": "tool_call"}]
    # Cached tokens count as input, the way the hub prices them.
    assert out.usage_metadata["input_tokens"] == 16 and out.usage_metadata["output_tokens"] == 2
    assert out.usage_metadata["input_token_details"] == {"cache_read": 5, "cache_creation": 1}
    assert out.response_metadata["stop_reason"] == "tool_use"
    assert out.response_metadata["model_name"] == "claude-opus-4-6-served"
    assert out.response_metadata["usage"]["cache_read_input_tokens"] == 5
    assert out.response_metadata["context_management"]["applied_edits"][0]["cleared_tool_uses"] == 2


def test_a_text_only_answer_is_a_string(server):
    server.reply["content"] = [{"type": "text", "text": "plain", "citations": None}]
    assert _model(server).invoke("hi").content == "plain"


def test_a_stream_carries_thinking_text_tool_calls_usage_and_applied_edits(server):
    chunks = list(_model(server).bind_tools([ping]).stream("hi"))
    agg = _merge(chunks)
    assert server.last["stream"] is True
    assert agg.content[0]["type"] == "thinking" and agg.content[0]["thinking"] == "let me "
    assert agg.content[0]["signature"] == "sig"
    assert [b for b in agg.content if b.get("type") == "text"][0]["text"] == "Hello"
    assert agg.tool_calls == [{"name": "ping", "args": {"text": "b"}, "id": "tu_2", "type": "tool_call"}]
    assert agg.usage_metadata["input_tokens"] == 50 and agg.usage_metadata["output_tokens"] == 20
    assert agg.response_metadata["stop_reason"] == "tool_use"
    assert agg.response_metadata["model_name"] == "claude-opus-4-6"
    # What the server cleared, on a streamed call too.
    assert agg.response_metadata["context_management"]["applied_edits"][0]["cleared_tool_uses"] == 1


def test_a_plain_stream_is_text_tokens(server):
    server.events = [server.events[0]] + [e for e in server.events[5:9]] + server.events[-2:]
    chunks = list(_model(server).stream("hi"))
    assert "".join(c.content for c in chunks) == "Hello"
    assert all(isinstance(c.content, str) for c in chunks)


def test_structured_output_is_a_forced_tool_call(server):
    class Out(BaseModel):
        a: int

    server.reply["content"] = [{"type": "tool_use", "id": "tu_9", "name": "Out", "input": {"a": 1}}]
    result = _model(server).with_structured_output(Out, include_raw=True).invoke("x")
    assert result["parsed"] == Out(a=1) and result["parsing_error"] is None
    assert server.last["tool_choice"] == {"type": "tool", "name": "Out"}
    assert server.last["tools"][0]["name"] == "Out"


def test_the_async_paths(server):
    async def main():
        llm = AnthropicChatModel(model="claude-opus-4-6", api_key="ak", http_async_client=server.async_client())
        out = await llm.bind_tools([ping]).ainvoke("hi")
        assert out.tool_calls[0]["id"] == "tu_1"
        agg = _merge([c async for c in llm.bind_tools([ping]).astream("hi")])
        assert agg.tool_calls[0]["id"] == "tu_2"

    asyncio.run(main())


# ── Where the rest of the hub reads the model ────────────────────────────────

def test_the_batch_api_and_the_loop_recognise_the_driver(server):
    from agents.loop_ext import anthropic_native as an
    from providers import batch_api
    llm = _model(server)
    assert an.is_anthropic_client(llm) and an.model_id(llm) == "claude-opus-4-6"
    target = batch_api.chat_model_target(llm)
    assert target is not None and target.provider == "anthropic" and target.api_key == "ak-test"
    body = batch_api.request_body(llm, [("system", "sys"), ("human", "hi")], [ping])
    assert body["system"] == "sys" and body["tools"][0]["name"] == "ping" and "stream" not in body


def test_the_base_url_comes_from_the_environment_when_not_given(server, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example")
    llm = AnthropicChatModel(model="claude-opus-4-6", api_key="ak")
    assert llm.anthropic_api_url == "https://proxy.example" and not llm.is_anthropic_api
