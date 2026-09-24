"""The hub as an OpenAI-compatible provider, over real HTTP (routes/openai_compat.py).

The chat model is a fake that answers from a script and records what it was
asked, so nothing here reaches a provider. The catalog is seeded through
providers.catalog.save_catalog_raw and the global default is pinned, so the
operator's own .env never leaks into a test.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

PASSWORD = "hunter2-but-longer"

CATALOG = {
    "openai": {"default": "gpt-4o", "models": [
        {"id": "gpt-4o", "enabled": True, "context_window": 128000, "released_at": 1700000000},
        {"id": "shared-id", "enabled": True},
        {"id": "gpt-old", "enabled": False},
    ]},
    "anthropic": {"default": "", "models": [
        {"id": "claude-x", "enabled": True, "context_window": 200000},
        {"id": "shared-id", "enabled": True},
    ]},
}


class FakeModel:
    """Enough of a LangChain chat model for the route: invoke, astream, bind_tools."""

    def __init__(self, reply=None, chunks=None, fail=None):
        self.reply = reply or AIMessage(
            content="Hello there",
            usage_metadata={"input_tokens": 11, "output_tokens": 3, "total_tokens": 14})
        self.chunks = chunks
        self.fail = fail
        self.seen = []
        self.tools = None
        self.tool_choice = None
        self.kwargs = {}

    def bind_tools(self, tools, tool_choice=None):
        self.tools = tools
        self.tool_choice = tool_choice
        return self

    def invoke(self, messages, **kwargs):
        self.seen.append(messages)
        self.kwargs = kwargs
        if self.fail:
            raise RuntimeError(self.fail)
        return self.reply

    async def astream(self, messages, **kwargs):
        self.seen.append(messages)
        for chunk in self.chunks or []:
            yield chunk
        if self.fail:
            raise RuntimeError(self.fail)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture
def single(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def served(monkeypatch):
    """The catalog above, a pinned global default, and a fake model factory."""
    from providers.catalog import save_catalog_raw
    from routes import models as models_routes
    from routes import openai_compat
    save_catalog_raw(CATALOG)
    monkeypatch.setattr(models_routes, "_global_default",
                        lambda: {"provider": "openai", "model": "gpt-4o"})
    state = {"model": FakeModel(), "built": []}

    def fake_build(**kwargs):
        state["built"].append(kwargs)
        return state["model"]

    monkeypatch.setattr(openai_compat, "build_chat_model", fake_build)
    return state


def _rows(table: str):
    from common import db
    return db.get_conn().execute(f"SELECT * FROM {table}").fetchall()


def _events(text: str):
    lines = [ln[len("data: "):] for ln in text.splitlines() if ln.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    return [json.loads(ln) for ln in lines[:-1]]


# ── /v1/models ───────────────────────────────────────────────────────────────

def test_models_lists_enabled_models_only(single, served, client):
    body = client.get("/v1/models").json()
    assert body["object"] == "list"
    ids = [m["id"] for m in body["data"]]
    assert set(ids) == {"openai/gpt-4o", "openai/shared-id", "anthropic/claude-x",
                        "anthropic/shared-id"}
    gpt = next(m for m in body["data"] if m["id"] == "openai/gpt-4o")
    assert gpt == {"id": "openai/gpt-4o", "object": "model", "owned_by": "openai",
                   "created": 1700000000, "context_window": 128000,
                   "provider": "openai", "model": "gpt-4o"}


def test_the_global_default_is_served_even_when_not_enabled(single, served, client, monkeypatch):
    from routes import models as models_routes
    monkeypatch.setattr(models_routes, "_global_default",
                        lambda: {"provider": "ollama", "model": "llama3"})
    ids = [m["id"] for m in client.get("/v1/models").json()["data"]]
    assert "ollama/llama3" in ids
    assert client.get("/v1/models/default").json()["id"] == "ollama/llama3"


def test_one_model_by_full_or_bare_id(single, served, client):
    assert client.get("/v1/models/openai/gpt-4o").json()["id"] == "openai/gpt-4o"
    assert client.get("/v1/models/claude-x").json()["id"] == "anthropic/claude-x"
    missing = client.get("/v1/models/openai/gpt-old")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "model_not_found"


def test_index_and_unknown_paths(single, served, client):
    assert client.get("/v1").json()["object"] == "api"
    other = client.post("/v1/embeddings", json={})
    assert other.status_code == 404
    assert other.json()["error"]["type"] == "invalid_request_error"


# ── resolution ───────────────────────────────────────────────────────────────

def test_bare_id_resolves_to_its_one_provider(single, served, client):
    response = client.post("/v1/chat/completions", json={
        "model": "claude-x", "messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 200, response.text
    assert served["built"][-1]["provider"] == "anthropic"
    assert served["built"][-1]["model"] == "claude-x"
    assert response.json()["model"] == "claude-x"


def test_ambiguous_bare_id_is_400_naming_the_candidates(single, served, client):
    response = client.post("/v1/chat/completions", json={
        "model": "shared-id", "messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 400
    message = response.json()["error"]["message"]
    assert "anthropic/shared-id" in message and "openai/shared-id" in message


@pytest.mark.parametrize("name", ["nope", "openai/nope", "openai/gpt-old", "gpt-old"])
def test_unknown_or_disabled_model_is_404(single, served, client, name):
    response = client.post("/v1/chat/completions", json={
        "model": name, "messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["code"] == "model_not_found"
    assert served["built"] == []


def test_image_parts_are_refused(single, served, client):
    response = client.post("/v1/chat/completions", json={
        "model": "openai/gpt-4o", "messages": [{"role": "user", "content": [
            {"type": "text", "text": "what is this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]}]})
    assert response.status_code == 400
    assert "image_url" in response.json()["error"]["message"]


# ── completions ──────────────────────────────────────────────────────────────

def test_non_stream_completion_shape_and_usage(single, served, client):
    response = client.post("/v1/chat/completions", json={
        "model": "openai/gpt-4o", "temperature": 0.2, "max_tokens": 50, "stop": "END",
        "messages": [{"role": "system", "content": "be brief"},
                     {"role": "user", "content": [{"type": "text", "text": "hi"}]}]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"].startswith("chatcmpl-")
    assert body["object"] == "chat.completion"
    assert body["model"] == "openai/gpt-4o"
    choice = body["choices"][0]
    assert choice["message"] == {"role": "assistant", "content": "Hello there"}
    assert choice["finish_reason"] == "stop"
    assert body["usage"] == {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}
    built = served["built"][-1]
    assert built == {"provider": "openai", "model": "gpt-4o", "temperature": 0.2,
                     "max_tokens": 50, "streaming": False}
    assert served["model"].kwargs == {"stop": ["END"]}
    assert _rows("serving_usage")[0]["total_tokens"] == 14


def test_usage_is_estimated_when_the_provider_reports_none(single, served, client):
    served["model"] = FakeModel(reply=AIMessage(content="x" * 40))
    body = client.post("/v1/chat/completions", json={
        "model": "openai/gpt-4o", "messages": [{"role": "user", "content": "y" * 8}]}).json()
    assert body["usage"] == {"prompt_tokens": 2, "completion_tokens": 10,
                             "total_tokens": 12, "estimated": True}
    assert _rows("serving_usage")[0]["estimated"] == 1


def test_json_object_adds_an_instruction(single, served, client):
    client.post("/v1/chat/completions", json={
        "model": "openai/gpt-4o", "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": "hi"}]})
    first = served["model"].seen[-1][0]
    assert "JSON" in first.content


def test_tool_calls_round_trip(single, served, client):
    served["model"] = FakeModel(reply=AIMessage(
        content="", tool_calls=[{"id": "call_1", "name": "get_weather",
                                 "args": {"city": "Bonn"}}],
        usage_metadata={"input_tokens": 20, "output_tokens": 7, "total_tokens": 27}))
    tools = [{"type": "function", "function": {
        "name": "get_weather", "description": "weather",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}]
    body = client.post("/v1/chat/completions", json={
        "model": "openai/gpt-4o", "tools": tools, "tool_choice": "required",
        "messages": [{"role": "user", "content": "weather in Bonn?"}]}).json()
    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None
    call = choice["message"]["tool_calls"][0]
    assert call["id"] == "call_1" and call["type"] == "function"
    assert call["function"]["name"] == "get_weather"
    assert json.loads(call["function"]["arguments"]) == {"city": "Bonn"}
    assert served["model"].tools == tools
    assert served["model"].tool_choice == "any"

    # The client runs the tool and sends the call and its result back.
    served["model"].reply = AIMessage(content="It is sunny.")
    response = client.post("/v1/chat/completions", json={
        "model": "openai/gpt-4o", "tools": tools,
        "messages": [{"role": "user", "content": "weather in Bonn?"},
                     {"role": "assistant", "content": None, "tool_calls": [call]},
                     {"role": "tool", "tool_call_id": "call_1", "content": "sunny"}]})
    assert response.json()["choices"][0]["message"]["content"] == "It is sunny."
    seen = served["model"].seen[-1]
    assert isinstance(seen[0], HumanMessage)
    assert seen[1].tool_calls[0]["args"] == {"city": "Bonn"}
    assert isinstance(seen[2], ToolMessage) and seen[2].tool_call_id == "call_1"


def test_stream_chunks_and_done(single, served, client):
    served["model"] = FakeModel(chunks=[
        AIMessageChunk(content="Hel"),
        AIMessageChunk(content="lo", usage_metadata={"input_tokens": 5, "output_tokens": 2,
                                                     "total_tokens": 7}),
    ])
    response = client.post("/v1/chat/completions", json={
        "model": "openai/gpt-4o", "stream": True, "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    assert all(e["object"] == "chat.completion.chunk" for e in events)
    assert events[0]["choices"][0]["delta"]["role"] == "assistant"
    text = "".join(e["choices"][0]["delta"].get("content") or "" for e in events)
    assert text == "Hello"
    assert events[-1]["choices"][0]["finish_reason"] == "stop"
    assert events[-1]["usage"] == {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}
    assert served["built"][-1]["streaming"] is True
    row = _rows("serving_usage")[0]
    assert row["stream"] == 1 and row["total_tokens"] == 7


def test_stream_tool_call_deltas(single, served, client):
    served["model"] = FakeModel(chunks=[
        AIMessageChunk(content="", tool_call_chunks=[
            {"name": "get_weather", "args": '{"city": ', "id": "call_9", "index": 0}]),
        AIMessageChunk(content="", tool_call_chunks=[
            {"name": None, "args": '"Bonn"}', "id": None, "index": 0}]),
    ])
    response = client.post("/v1/chat/completions", json={
        "model": "openai/gpt-4o", "stream": True,
        "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {}}}],
        "messages": [{"role": "user", "content": "hi"}]})
    events = _events(response.text)
    deltas = [d for e in events for d in e["choices"][0]["delta"].get("tool_calls") or []]
    assert deltas[0]["id"] == "call_9" and deltas[0]["function"]["name"] == "get_weather"
    assert "".join(d["function"]["arguments"] for d in deltas) == '{"city": "Bonn"}'
    assert events[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert "usage" not in events[-1]


def test_stream_error_mid_way(single, served, client):
    served["model"] = FakeModel(chunks=[AIMessageChunk(content="par")], fail="provider down")
    response = client.post("/v1/chat/completions", json={
        "model": "openai/gpt-4o", "stream": True,
        "messages": [{"role": "user", "content": "hi"}]})
    events = _events(response.text)
    assert events[-1]["choices"][0]["finish_reason"] == "error"
    assert "provider down" in events[-1]["error"]["message"]
    row = _rows("serving_usage")[0]
    assert row["status"] == "error" and "provider down" in row["error"]


def test_provider_failure_is_502_and_recorded(single, served, client):
    served["model"] = FakeModel(fail="rate limited")
    response = client.post("/v1/chat/completions", json={
        "model": "openai/gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 502
    assert "rate limited" in response.json()["error"]["message"]
    assert _rows("serving_usage")[0]["status"] == "error"


def test_audit_row_is_written(single, served, client):
    client.post("/v1/chat/completions", json={
        "model": "openai/gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
    from common import audit
    items = audit.query(action="model.serve")["items"]
    assert len(items) == 1
    entry = items[0]
    assert entry["object_type"] == "model" and entry["object_id"] == "openai/gpt-4o"
    assert entry["result"] == "ok"
    assert entry["details"]["prompt_tokens"] == 11
    assert entry["details"]["stream"] is False
    assert entry["details"]["tools"] == 0


# ── authentication ───────────────────────────────────────────────────────────

def test_multi_mode_needs_a_key_and_accepts_a_personal_one(multi, served, client):
    request = {"model": "openai/gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    assert client.get("/v1/models").status_code == 401
    assert client.post("/v1/chat/completions", json=request).status_code == 401

    admin = client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    assert admin.status_code == 200, admin.text
    from common import api_keys, identity
    user = identity.get_user_by_username("root")
    key, record = api_keys.create_key(user["id"], name="sdk")
    headers = {"Authorization": f"Bearer {key}"}
    assert client.get("/v1/models", headers=headers).status_code == 200
    response = client.post("/v1/chat/completions", json=request, headers=headers)
    assert response.status_code == 200, response.text
    row = _rows("serving_usage")[0]
    assert row["actor_name"] == "root" and row["actor_kind"] == "api_key"
    assert row["key_id"] == record["id"]


def test_serving_info_and_usage(single, served, client):
    info = client.get("/api/models/serving/info").json()
    assert info["base_url"].endswith("/v1")
    assert info["models"] == 4
    assert info["auth"] == "none"
    client.post("/v1/chat/completions", json={
        "model": "openai/gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
    usage = client.get("/api/models/serving/usage").json()
    assert usage["totals"]["requests"] == 1
    assert usage["rows"][0]["model"] == "gpt-4o"
