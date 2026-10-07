"""OpenAI reasoning in the chat: summaries requested, read and streamed.

OpenAI never returns the raw reasoning of o-series and gpt-5 models. Chat
completions carry only a token count, so an agent on such a model showed no
thought at all. The Responses API returns a summary of the reasoning when one is
asked for. These tests pin the three parts of that: a thinking level moves a
reasoning model to the Responses API with a summary requested, the summary is
read in every shape langchain hands it over in, and an organisation OpenAI
refuses a summary for still gets its answer.
"""
import asyncio

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult, LLMResult
from langchain_openai import ChatOpenAI

from agents import agent_utils
from agents.agent_utils import build_chat_model, is_openai_reasoning_model
from agents.callbacks.chat_stream import ChatStreamCallback
from reasoning.native_reasoning import (
    extract_reasoning_from_message, reasoning_summary_delta,
)


def _body(model, thinking_level):
    llm = build_chat_model(provider="openai", model=model, api_key="sk-test",
                           thinking_level=thinking_level)
    body = llm._get_request_payload([("human", "hi")])
    return ("responses" if llm._use_responses_api(body) else "chat"), body


@pytest.mark.parametrize("model", ["gpt-5", "gpt-5.4", "o3", "o4-mini"])
def test_a_thinking_level_asks_openai_for_a_summary(model):
    endpoint, body = _body(model, "medium")
    assert endpoint == "responses"
    assert body["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert "temperature" not in body


@pytest.mark.parametrize("model", ["gpt-5", "o3"])
def test_without_a_thinking_level_nothing_changes(model):
    endpoint, body = _body(model, None)
    assert endpoint == "chat"
    assert "reasoning" not in body and "reasoning_effort" not in body


def test_chat_aliases_of_gpt5_get_no_reasoning_parameter():
    # gpt-5-chat-latest is a chat model and 400s on any reasoning parameter.
    assert not is_openai_reasoning_model("gpt-5-chat-latest")
    assert not is_openai_reasoning_model("gpt-5.1-chat-latest")
    endpoint, body = _body("gpt-5-chat-latest", "high")
    assert endpoint == "chat"
    assert "reasoning_effort" not in body and "temperature" in body


# ── Reading the summary ──────────────────────────────────────────────────────

_ITEM = {"id": "rs_1", "type": "reasoning", "summary": [
    {"type": "summary_text", "text": "**Plan**\n\nCheck the divisors."},
    {"type": "summary_text", "text": "**Answer**\n\nIt is 391."},
]}


def test_a_summary_in_additional_kwargs_is_read():
    # langchain's default (v0) output puts the reasoning item there.
    msg = AIMessage(content="391", additional_kwargs={"reasoning": dict(_ITEM)})
    assert extract_reasoning_from_message(msg) == (
        "**Plan**\n\nCheck the divisors.\n\n**Answer**\n\nIt is 391.")


def test_a_summary_as_a_content_block_is_read():
    # The responses/v1 output keeps it in the content instead.
    msg = AIMessage(content=[dict(_ITEM), {"type": "text", "text": "391"}])
    assert extract_reasoning_from_message(msg).startswith("**Plan**")


def test_an_empty_summary_is_no_reasoning():
    # OpenAI leaves short reasoning unsummarised: the item is there, empty.
    msg = AIMessage(content="ok", additional_kwargs={
        "reasoning": {"id": "rs_1", "type": "reasoning", "summary": []}})
    assert extract_reasoning_from_message(msg) == ""


def test_stream_slices_join_with_a_break_between_parts():
    slices = [
        {"id": "rs_1", "type": "reasoning", "summary": []},
        {"summary": [{"index": 0, "type": "summary_text", "text": ""}]},
        {"summary": [{"index": 0, "type": "summary_text", "text": "First "}]},
        {"summary": [{"index": 0, "type": "summary_text", "text": "part."}]},
        {"summary": [{"index": 1, "type": "summary_text", "text": ""}]},
        {"summary": [{"index": 1, "type": "summary_text", "text": "Second."}]},
    ]
    assert "".join(reasoning_summary_delta(s) for s in slices) == "First part.\n\nSecond."


@pytest.fixture
def callback(tmp_path):
    loop = asyncio.new_event_loop()
    cb = ChatStreamCallback(loop, asyncio.Queue(), [], tmp_path / "run.log")
    cb.events = []
    cb._emit = cb.events.append
    try:
        yield cb
    finally:
        loop.close()


def _chunk(**kw):
    return ChatGenerationChunk(message=AIMessageChunk(**kw))


@pytest.mark.parametrize("shape", ["v0", "responses/v1"])
def test_a_streamed_summary_shows_once_before_the_answer(callback, shape):
    def slice_(index, text):
        item = {"type": "reasoning", "summary": [
            {"index": index, "type": "summary_text", "text": text}]}
        if shape == "v0":
            return _chunk(content=[], additional_kwargs={"reasoning": item})
        return _chunk(content=[item])

    callback.on_llm_start({}, ["prompt"])
    for index, text in ((0, "Thinking "), (0, "hard."), (1, ""), (1, "Done.")):
        callback.on_llm_new_token("", chunk=slice_(index, text))
    callback.on_llm_new_token("391", chunk=_chunk(content="391"))
    final = AIMessage(content="391", additional_kwargs={"reasoning": {
        "type": "reasoning", "summary": [{"type": "summary_text", "text": "Thinking hard."},
                                         {"type": "summary_text", "text": "Done."}]}})
    callback.on_llm_end(LLMResult(generations=[[ChatGeneration(message=final)]]))

    thinks = [e for e in callback.events if e["type"] == "think"]
    assert [t["content"] for t in thinks] == ["Thinking hard.\n\nDone."]
    kinds = [e["type"] for e in callback.events if e["type"] in ("think", "token")]
    assert kinds.index("think") < kinds.index("token")


# ── An organisation OpenAI refuses a summary for ─────────────────────────────

def _refusal():
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return openai.BadRequestError(
        "Your organization must be verified to generate reasoning summaries.",
        response=httpx.Response(400, request=request),
        body={"param": "reasoning.summary", "code": "unsupported_value"},
    )


@pytest.fixture
def no_refusals(monkeypatch):
    monkeypatch.setattr(agent_utils, "_SUMMARY_REFUSED", set())


def test_a_refused_summary_retries_without_it_and_is_remembered(monkeypatch, no_refusals):
    sent = []

    def fake_generate(self, messages, stop=None, run_manager=None, **kwargs):
        sent.append(dict(self.reasoning))
        if "summary" in self.reasoning:
            raise _refusal()
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="391"))])

    monkeypatch.setattr(ChatOpenAI, "_generate", fake_generate)
    llm = build_chat_model(provider="openai", model="gpt-5", api_key="sk-a",
                           thinking_level="low")
    assert llm.invoke("17*23?").content == "391"
    assert sent == [{"effort": "low", "summary": "auto"}, {"effort": "low"}]

    # The next model for the same key does not ask again; another key does.
    again = build_chat_model(provider="openai", model="gpt-5", api_key="sk-a",
                             thinking_level="low")
    assert again.reasoning == {"effort": "low"}
    other = build_chat_model(provider="openai", model="gpt-5", api_key="sk-b",
                             thinking_level="low")
    assert other.reasoning == {"effort": "low", "summary": "auto"}


def test_a_refused_summary_reopens_the_stream(monkeypatch, no_refusals):
    def fake_stream(self, messages, stop=None, run_manager=None, **kwargs):
        if "summary" in self.reasoning:
            raise _refusal()
        yield _chunk(content="391")

    monkeypatch.setattr(ChatOpenAI, "_stream", fake_stream)
    llm = build_chat_model(provider="openai", model="gpt-5", api_key="sk-a",
                           thinking_level="low", streaming=True)
    assert "".join(c.content for c in llm.stream("17*23?")) == "391"


def test_any_other_bad_request_is_raised(monkeypatch, no_refusals):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    other = openai.BadRequestError("Invalid tool schema", body={"param": "tools"},
                                   response=httpx.Response(400, request=request))

    def fake_generate(self, *a, **kw):
        raise other

    monkeypatch.setattr(ChatOpenAI, "_generate", fake_generate)
    llm = build_chat_model(provider="openai", model="gpt-5", api_key="sk-a",
                           thinking_level="low")
    with pytest.raises(openai.BadRequestError):
        llm.invoke("hi")
    assert llm.reasoning == {"effort": "low", "summary": "auto"}


def test_the_stream_still_reaches_the_callbacks(monkeypatch, no_refusals):
    # langchain passes the callbacks to _generate only when its signature
    # names run_manager; a wrapper without it cut the chat off from the stream.
    seen = []

    def fake_stream(self, messages, stop=None, run_manager=None, **kwargs):
        seen.append(run_manager)
        yield _chunk(content="391")

    monkeypatch.setattr(ChatOpenAI, "_stream", fake_stream)
    llm = build_chat_model(provider="openai", model="gpt-5", api_key="sk-a",
                           thinking_level="low", streaming=True)
    assert llm.invoke("17*23?").content == "391"
    assert seen and seen[0] is not None
