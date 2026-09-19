"""Which OpenAI endpoint a built chat model talks to.

Every agent here is a tool-calling agent, and some reasoning models refuse tool
calls on ``/v1/chat/completions`` altogether: they reason by default, and the
combination is only served by ``/v1/responses``. These tests pin that routing —
the model families that must be sent to the Responses API, the ones that must
stay on chat completions, and the fact that a thinking level still reaches the
request either way.
"""
import pytest

from langchain_core.tools import tool

from agents.agent_utils import build_chat_model, needs_responses_api


@tool
def ping(text: str) -> str:
    """Echo a string back."""
    return text


def payload_for(model, thinking_level=None, provider="openai"):
    """The request body a tool-calling turn would send for this model."""
    llm = build_chat_model(provider=provider, model=model, api_key="sk-test",
                           thinking_level=thinking_level)
    tools = llm.bind_tools([ping]).kwargs["tools"]
    body = llm._get_request_payload([("human", "hi")], tools=tools)
    endpoint = "responses" if llm._use_responses_api(body) else "chat"
    return endpoint, body


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-5.6-terra", "GPT-5.6-Luna"])
def test_tool_calls_on_responses_only_models_go_to_the_responses_api(model):
    # Without this, the model 400s on chat completions even with no reasoning
    # parameter of our own in the request.
    assert needs_responses_api(model)
    endpoint, body = payload_for(model)
    assert endpoint == "responses"
    # Tools are rewritten into the flat Responses shape, not nested under
    # "function", or the model sees no tools at all.
    assert body["tools"][0]["name"] == "ping"
    assert "max_output_tokens" in body


@pytest.mark.parametrize("model", ["gpt-5.4", "gpt-5", "o3", "gpt-4o"])
def test_every_other_model_stays_on_chat_completions(model):
    assert not needs_responses_api(model)
    endpoint, body = payload_for(model)
    assert endpoint == "chat"
    assert body["tools"][0]["function"]["name"] == "ping"


def test_thinking_level_reaches_both_endpoints():
    _, responses_body = payload_for("gpt-5.6-sol", thinking_level="high")
    assert responses_body["reasoning"] == {"effort": "high"}

    _, chat_body = payload_for("gpt-5.4", thinking_level="medium")
    assert chat_body["reasoning_effort"] == "medium"


def test_a_vendor_prefixed_id_is_recognised():
    # Gateways publish the same model as "openai/gpt-5.6-sol".
    assert needs_responses_api("openai/gpt-5.6-sol")
    assert not needs_responses_api("openai/gpt-4o")


def test_a_custom_backend_serving_one_routes_there_too(monkeypatch):
    from providers import adapters

    backend = {"id": "gw", "adapter": "openai", "base_url": "https://gw.example/v1",
               "api_key": "k"}
    llm = adapters.build_custom_model(backend, model="gpt-5.6-sol", temperature=None,
                                      max_tokens=None, streaming=False,
                                      thinking_level=None)
    assert llm.use_responses_api is True
