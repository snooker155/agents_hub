"""The hub's own OpenAI-protocol driver (providers/openai_driver.py).

It replaced ``langchain-openai`` under every OpenAI-compatible model: OpenAI
itself, LM Studio, the hub runtime, custom backends. These tests pin what the
rest of the hub relies on, against a mock transport on both endpoints: the
request bodies, the messages read back (tool calls, usage, the server's own
reasoning), streaming, structured output and the things the package got
wrong that the driver fixes.
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

from providers.openai_driver import OpenAIChatModel, message_to_dict, responses_input


@tool
def ping(text: str) -> str:
    """Echo a string back."""
    return text


def _sse(events: List[dict], *, named: bool = False, done: bool = True) -> httpx.Response:
    body = "".join((f"event: {e['type']}\n" if named else "") + f"data: {json.dumps(e)}\n\n" for e in events)
    if done:
        body += "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


class Server:
    """A mock OpenAI-compatible server recording what it was sent."""

    def __init__(self) -> None:
        self.sent: List[Dict[str, Any]] = []
        self.chat: Dict[str, Any] = {
            "id": "c1", "object": "chat.completion", "model": "qwen-served",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "hi", "reasoning_content": "hmm"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4,
                      "prompt_tokens_details": {"cached_tokens": 2}}}
        self.chat_stream = [
            {"choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": "think "}}]},
            {"choices": [{"index": 0, "delta": {"content": "Hel"}}]},
            {"choices": [{"index": 0, "delta": {"content": "lo", "tool_calls": [
                {"index": 0, "id": "call_1", "type": "function",
                 "function": {"name": "ping", "arguments": '{"te'}}]}}]},
            {"model": "qwen-served", "choices": [{"index": 0, "finish_reason": "tool_calls", "delta": {
                "tool_calls": [{"index": 0, "function": {"arguments": 'xt": "a"}'}}]}}]},
            {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}},
        ]
        self.responses: Dict[str, Any] = {
            "id": "resp_2", "object": "response", "created_at": 1, "model": "gpt-5-served",
            "status": "completed", "output": [
                {"id": "rs_2", "type": "reasoning", "summary": [{"type": "summary_text", "text": "Think."}]},
                {"id": "msg_2", "type": "message", "role": "assistant", "status": "completed",
                 "content": [{"type": "output_text", "text": "391", "annotations": []}]},
                {"id": "fc_2", "type": "function_call", "call_id": "call_9", "name": "ping",
                 "arguments": '{"text": "b"}', "status": "completed"}],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                      "output_tokens_details": {"reasoning_tokens": 3}}}
        self.responses_stream = [
            {"type": "response.created", "response": {"id": "resp_1", "object": "response", "created_at": 1,
                                                       "model": "gpt-5", "output": [], "status": "in_progress"}},
            {"type": "response.output_item.added", "output_index": 0,
             "item": {"id": "rs_1", "type": "reasoning", "summary": []}},
            {"type": "response.reasoning_summary_part.added", "output_index": 0, "summary_index": 0,
             "item_id": "rs_1", "part": {"type": "summary_text", "text": ""}},
            {"type": "response.reasoning_summary_text.delta", "output_index": 0, "summary_index": 0,
             "item_id": "rs_1", "delta": "Plan."},
            {"type": "response.output_item.added", "output_index": 1,
             "item": {"id": "msg_1", "type": "message", "role": "assistant", "status": "in_progress", "content": []}},
            {"type": "response.output_text.delta", "output_index": 1, "content_index": 0, "item_id": "msg_1", "delta": "39"},
            {"type": "response.output_text.delta", "output_index": 1, "content_index": 0, "item_id": "msg_1", "delta": "1"},
            {"type": "response.output_text.done", "output_index": 1, "content_index": 0, "item_id": "msg_1", "text": "391"},
            {"type": "response.output_item.added", "output_index": 2,
             "item": {"id": "fc_1", "type": "function_call", "call_id": "call_9", "name": "ping", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "output_index": 2, "item_id": "fc_1",
             "delta": '{"text": "b"}'},
            {"type": "response.completed", "response": {
                "id": "resp_1", "object": "response", "created_at": 1, "model": "gpt-5", "status": "completed",
                "output": [
                    {"id": "rs_1", "type": "reasoning", "summary": [{"type": "summary_text", "text": "Plan."}]},
                    {"id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
                     "content": [{"type": "output_text", "text": "391", "annotations": []}]},
                    {"id": "fc_1", "type": "function_call", "call_id": "call_9", "name": "ping",
                     "arguments": '{"text": "b"}', "status": "completed"}],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                          "output_tokens_details": {"reasoning_tokens": 3}}}},
        ]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.sent.append({"url": str(request.url), "headers": dict(request.headers), "body": body})
        if request.url.path.endswith("/chat/completions"):
            return _sse(self.chat_stream) if body.get("stream") else httpx.Response(200, json=self.chat)
        if request.url.path.endswith("/responses"):
            if body.get("stream"):
                return _sse(self.responses_stream, named=True, done=False)
            return httpx.Response(200, json=self.responses)
        return httpx.Response(404, json={"error": "no such route"})

    @property
    def last(self) -> Dict[str, Any]:
        return self.sent[-1]["body"]

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def async_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


@pytest.fixture
def server(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    return Server()


def _local(server: Server, **kw) -> OpenAIChatModel:
    return OpenAIChatModel(model="qwen", api_key="k", base_url="http://local:1234/v1",
                           http_client=server.client(), **kw)


def _openai(server: Server, **kw) -> OpenAIChatModel:
    return OpenAIChatModel(model="gpt-5", api_key="sk-test", http_client=server.client(), **kw)


def _merge(chunks):
    agg = chunks[0]
    for c in chunks[1:]:
        agg = agg + c
    return agg


# ── Chat completions ─────────────────────────────────────────────────────────

def test_the_request_body_and_the_answer(server):
    llm = _local(server, max_tokens=50, temperature=0.2)
    out = llm.invoke([SystemMessage(content=[{"type": "text", "text": "sys",
                                              "cache_control": {"type": "ephemeral"}}]),
                      HumanMessage(content="hi")])
    assert server.last == {"model": "qwen", "max_tokens": 50, "temperature": 0.2, "messages": [
        # Anthropic's cache marker never reaches another server.
        {"role": "system", "content": [{"type": "text", "text": "sys"}]},
        {"role": "user", "content": "hi"}]}
    assert out.content == "hi"
    # The server's own reasoning survives beside the answer.
    assert out.additional_kwargs["reasoning_content"] == "hmm"
    assert out.usage_metadata["input_tokens"] == 3
    assert out.usage_metadata["input_token_details"]["cache_read"] == 2
    assert out.response_metadata["model_name"] == "qwen-served"
    assert out.response_metadata["finish_reason"] == "stop"
    assert out.response_metadata["token_usage"]["total_tokens"] == 4


def test_the_key_and_headers_reach_the_server(server):
    llm = _local(server, default_headers={"X-Hub-Source": "agent"})
    llm.invoke("hi")
    headers = server.sent[-1]["headers"]
    assert headers["authorization"] == "Bearer k"
    assert headers["x-hub-source"] == "agent"
    assert server.sent[-1]["url"] == "http://local:1234/v1/chat/completions"


def test_a_stream_carries_tokens_tool_calls_reasoning_and_usage(server):
    chunks = list(_local(server).bind_tools([ping]).stream("go"))
    agg = _merge(chunks)
    assert agg.content == "Hello"
    assert agg.additional_kwargs["reasoning_content"] == "think "
    assert agg.tool_calls == [{"name": "ping", "args": {"text": "a"}, "id": "call_1", "type": "tool_call"}]
    assert agg.usage_metadata["total_tokens"] == 12
    assert agg.response_metadata == {"finish_reason": "tool_calls", "model_name": "qwen-served"}
    assert server.last["tools"][0]["function"]["name"] == "ping"


def test_usage_is_asked_for_on_openai_only(server):
    # OpenAI's own API: stream_options is on. Another address: left out, a
    # gateway may reject it. Never decided by the mere presence of a variable.
    list(_openai(server).stream("hi"))
    assert server.last["stream_options"] == {"include_usage": True}
    list(_local(server).stream("hi"))
    assert "stream_options" not in server.last
    list(_local(server, stream_usage=True).stream("hi"))
    assert server.last["stream_options"] == {"include_usage": True}


def test_an_empty_base_url_variable_is_never_read(server, monkeypatch):
    # The backend seeds every .env key into the environment, empty ones too;
    # the SDK would read "" as the address itself.
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    llm = _openai(server)
    assert llm.openai_api_base == "https://api.openai.com/v1"
    assert llm.is_openai_api
    llm.invoke("hi")
    assert server.sent[-1]["url"] == "https://api.openai.com/v1/chat/completions"


def test_the_forced_temperature_wins(server):
    _local(server, temperature=0.9, forced_temperature=0.4).invoke("hi")
    assert server.last["temperature"] == 0.4


def test_tool_choice_spellings(server):
    llm = _local(server)
    assert llm.bind_tools([ping], tool_choice="ping").kwargs["tool_choice"] == {
        "type": "function", "function": {"name": "ping"}}
    assert llm.bind_tools([ping], tool_choice="any").kwargs["tool_choice"] == "required"
    assert llm.bind_tools([ping], tool_choice=True).kwargs["tool_choice"] == "required"
    assert llm.bind_tools([ping], tool_choice="auto").kwargs["tool_choice"] == "auto"
    assert "tool_choice" not in llm.bind_tools([ping]).kwargs
    strict = llm.bind_tools([ping], strict=True).kwargs["tools"][0]["function"]
    assert strict["strict"] is True and strict["parameters"]["additionalProperties"] is False


def test_the_conversation_is_written_the_way_the_protocol_wants():
    turn = AIMessage(content="", tool_calls=[{"name": "ping", "args": {"text": "a"}, "id": "call_1"}])
    messages = [SystemMessage(content="s"), HumanMessage(content="hi"), turn,
                ToolMessage(content=[{"type": "text", "text": "pong"}], tool_call_id="call_1")]
    dicts = [message_to_dict(m) for m in messages]
    assert [d["role"] for d in dicts] == ["system", "user", "assistant", "tool"]
    assert dicts[2]["content"] is None
    assert dicts[2]["tool_calls"] == [{"type": "function", "id": "call_1", "function": {
        "name": "ping", "arguments": '{"text": "a"}'}}]
    assert dicts[3] == {"role": "tool", "tool_call_id": "call_1", "content": [{"type": "text", "text": "pong"}]}


def test_a_structured_answer_on_chat_completions(server):
    server.chat["choices"][0]["message"]["content"] = '{"a": 1}'

    class Out(BaseModel):
        a: int

    result = _local(server).with_structured_output(Out, method="json_schema", strict=True,
                                                   include_raw=True).invoke("x")
    assert result["parsed"] == Out(a=1) and result["parsing_error"] is None
    fmt = server.last["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False

    # An Anthropic-style tool dict works as a schema too (the repair path of
    # agents/loop_ext/structured.py), and a non-JSON answer is an error, not a crash.
    server.chat["choices"][0]["message"]["content"] = "not json"
    result = _local(server).with_structured_output(
        {"name": "structured_output", "description": "d",
         "input_schema": {"type": "object", "properties": {"a": {"type": "integer"}},
                          "required": ["a"], "additionalProperties": False}},
        method="json_schema", strict=True, include_raw=True).invoke("x")
    assert result["parsed"] is None and result["parsing_error"] is not None


def test_a_server_error_body_is_raised(server):
    server.chat = {"error": {"message": "model not loaded"}}
    with pytest.raises(ValueError, match="model not loaded"):
        _local(server).invoke("hi")


# ── Responses API ────────────────────────────────────────────────────────────

def test_a_reasoning_parameter_sends_the_request_to_responses(server):
    llm = _openai(server, reasoning={"effort": "low", "summary": "auto"}, max_tokens=100, temperature=0.3,
                  stop=["x"])
    body = llm._get_request_payload([("human", "hi")], tools=llm.bind_tools([ping]).kwargs["tools"])
    assert llm._use_responses_api(body)
    assert body["reasoning"] == {"effort": "low", "summary": "auto"}
    assert body["max_output_tokens"] == 100 and "max_tokens" not in body
    # Chat-completions-only keys never reach the endpoint; the temperature does.
    assert "stop" not in body and body["temperature"] == 0.3
    assert body["tools"] == [{"type": "function", "name": "ping", "description": "Echo a string back.",
                              "parameters": {"properties": {"text": {"type": "string"}},
                                             "required": ["text"], "type": "object"}}]
    assert body["input"] == [{"role": "user", "content": "hi"}]


def test_a_responses_answer_is_read_in_the_hubs_shape(server):
    out = _openai(server, reasoning={"effort": "low"}).invoke("hi")
    assert server.sent[-1]["url"] == "https://api.openai.com/v1/responses"
    assert out.content == [{"type": "text", "text": "391", "annotations": []}]
    # The reasoning item sits where reasoning.native_reasoning reads it.
    assert out.additional_kwargs["reasoning"]["summary"][0]["text"] == "Think."
    assert out.tool_calls == [{"name": "ping", "args": {"text": "b"}, "id": "call_9", "type": "tool_call"}]
    assert out.id == "msg_2"
    assert out.response_metadata["id"] == "resp_2" and out.response_metadata["model_name"] == "gpt-5-served"
    assert out.usage_metadata["output_token_details"]["reasoning"] == 3

    # The next request carries the turn back: reasoning, text, the call and its result.
    follow = responses_input([HumanMessage(content="hi"), out, ToolMessage(content="pong", tool_call_id="call_9")])
    assert [i["type"] if "type" in i else i["role"] for i in follow] == [
        "user", "reasoning", "message", "function_call", "function_call_output"]
    assert follow[1]["id"] == "rs_2"
    assert follow[3] == {"type": "function_call", "name": "ping", "arguments": '{"text": "b"}',
                         "call_id": "call_9", "id": "fc_2"}
    assert follow[4] == {"type": "function_call_output", "call_id": "call_9", "output": "pong"}


def test_a_responses_stream_merges_into_the_same_message(server):
    chunks = list(_openai(server, reasoning={"effort": "low"}).bind_tools([ping]).stream("hi"))
    agg = _merge(chunks)
    assert server.last["stream"] is True and "stream_options" not in server.last
    assert agg.content == [{"type": "text", "text": "391", "index": 1}]
    assert agg.additional_kwargs["reasoning"]["summary"] == [{"index": 0, "type": "summary_text", "text": "Plan."}]
    assert agg.tool_calls == [{"name": "ping", "args": {"text": "b"}, "id": "call_9", "type": "tool_call"}]
    assert agg.usage_metadata["total_tokens"] == 15
    assert agg.response_metadata["model_name"] == "gpt-5"
    # Text tokens arrive one by one for the chat stream.
    assert [c.content[0]["text"] for c in chunks if c.content and c.content[0].get("type") == "text"] == ["39", "1"]


def test_a_structured_answer_on_responses(server):
    server.responses["output"] = [server.responses["output"][1]]
    server.responses["output"][0]["content"][0]["text"] = '{"a": 1}'
    server.responses["text"] = {"format": {"type": "json_schema", "name": "Out", "schema": {}}}

    class Out(BaseModel):
        a: int

    llm = _openai(server, reasoning={"effort": "low"})
    result = llm.with_structured_output(Out, method="json_schema", strict=True, include_raw=True).invoke("x")
    assert result["parsed"] == Out(a=1)
    assert server.last["text"]["format"]["type"] == "json_schema"
    assert server.last["text"]["format"]["strict"] is True
    assert "response_format" not in server.last


def test_the_explicit_switch_decides_the_endpoint(server):
    assert _openai(server, use_responses_api=True)._use_responses_api({})
    assert not _openai(server, use_responses_api=False, reasoning={"effort": "low"})._use_responses_api({})
    assert _openai(server)._use_responses_api({"tools": [{"type": "web_search_preview"}]})


# ── Async ────────────────────────────────────────────────────────────────────

def test_the_async_paths(server):
    async def main():
        llm = OpenAIChatModel(model="qwen", api_key="k", base_url="http://local:1234/v1",
                              http_async_client=server.async_client())
        assert (await llm.ainvoke("hi")).content == "hi"
        agg = _merge([c async for c in llm.bind_tools([ping]).astream("go")])
        assert agg.content == "Hello" and agg.tool_calls[0]["name"] == "ping"
        gpt = OpenAIChatModel(model="gpt-5", api_key="sk", http_async_client=server.async_client(),
                              reasoning={"effort": "low"})
        assert (await gpt.ainvoke("hi")).tool_calls[0]["id"] == "call_9"
        agg = _merge([c async for c in gpt.astream("hi")])
        assert agg.usage_metadata["total_tokens"] == 15

    asyncio.run(main())


# ── Where the rest of the hub reads the model ────────────────────────────────

def test_the_batch_api_recognises_the_driver(server):
    from providers import batch_api
    target = batch_api.chat_model_target(_openai(server))
    assert target is not None and target.provider == "openai" and target.api_key == "sk-test"
    assert batch_api.chat_model_target(_local(server)) is None


def test_no_tokenizer_is_loaded_for_an_estimate(server):
    llm = _local(server)
    assert llm.get_num_tokens("x" * 40) == 10
    assert llm.get_num_tokens_from_messages([HumanMessage(content="x" * 40)]) == 13
