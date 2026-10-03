"""
Steering mode ``switch_model`` (docs/steering.md): a person moves a running
run to another catalog model. The loop takes the message before its next
model call, binds the same tools onto the new model, keeps the trail, and
records each call the new model answered so the run's cost prices it at the
new model's rates.

No provider is called: both models are recording fakes, and the Anthropic to
OpenAI case runs the real LangChain converters over what the second model is
sent.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, List

import pytest
from langchain.agents import AgentExecutor
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

from agents import agent_loop
from agents.agent_loop import LoopState, build_agent_runnable
from agents.loop_ext import fallback as fallback_ext
from agents.loop_ext import steering as steer_ext
from common import steering
from common.pricing import run_cost_usd


def _rid() -> str:
    return f"run-{uuid.uuid4().hex[:10]}"


CATALOG = [
    {"id": "anthropic/claude-a", "provider": "anthropic", "model": "claude-a"},
    {"id": "openai/gpt-b", "provider": "openai", "model": "gpt-b"},
]


#: What every fake model was sent and bound with, in call order (module level:
#: pydantic copies a list handed to a model field).
SEEN: List[Any] = []
BOUND: List[Any] = []


class _Recording(GenericFakeChatModel):
    """A fake chat model that remembers what it was sent and what it was bound with."""
    label: str = ""

    def bind_tools(self, tools, **kwargs):
        BOUND.append((self.label, [getattr(t, "name", t) for t in tools]))
        return self.bind(tools=[getattr(t, "name", t) for t in tools])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        SEEN.append((self.label, list(messages)))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


_DURING_TOOL: dict = {}


@tool
def echo(text: str) -> str:
    """Echo the text back."""
    pending = _DURING_TOOL.pop(text, None)
    if pending:
        steering.post(pending[0], pending[1], mode="switch_model", author="anton")
    return f"echo:{text}"


def _usage(n_in, n_out):
    return {"input_tokens": n_in, "output_tokens": n_out, "total_tokens": n_in + n_out}


def _models():
    first = _Recording(label="a", disable_streaming=True, messages=iter([
        # Anthropic's own shape: text and tool_use blocks in the content.
        AIMessage(content=[{"type": "text", "text": "looking"},
                           {"type": "tool_use", "id": "toolu_1", "name": "echo", "input": {"text": "one"}}],
                  tool_calls=[{"name": "echo", "args": {"text": "one"}, "id": "toolu_1"}],
                  usage_metadata=_usage(1000, 100)),
    ]))
    second = _Recording(label="b", disable_streaming=True, messages=iter([
        AIMessage(content="", tool_calls=[{"name": "echo", "args": {"text": "two"}, "id": "call_2"}],
                  usage_metadata=_usage(2000, 200)),
        AIMessage(content="done", usage_metadata=_usage(3000, 300)),
    ]))
    return first, second


@pytest.fixture
def switching(monkeypatch):
    """The catalog and the model builder the switch resolves through."""
    from agents import agent_utils
    from tools import delegation

    monkeypatch.delenv("AGENT_RUN_ID", raising=False)
    monkeypatch.setattr(delegation, "enabled_models", lambda: list(CATALOG))
    SEEN.clear()
    BOUND.clear()
    first, second = _models()
    built: list = []

    def build(**kw):
        built.append(kw)
        return second

    monkeypatch.setattr(agent_utils, "build_chat_model", build)
    return SimpleNamespace(first=first, second=second, seen=SEEN, bound=BOUND, built=built)


def _executor(model, extensions, system: Any = "sys"):
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content=system),
        MessagesPlaceholder(variable_name="chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    runnable = build_agent_runnable(model, [echo], prompt, extensions)
    return AgentExecutor(agent=runnable, tools=[echo], return_intermediate_steps=True)


def _run(executor, state):
    token = agent_loop.set_state(state)
    try:
        return executor.invoke({"input": "go"})
    finally:
        agent_loop.reset_state(token)


# ── the store ────────────────────────────────────────────────────────────────

def test_a_switch_is_claimed_by_the_loop_and_never_handed_back_as_a_turn():
    run_id = _rid()
    steering.post(run_id, "openai/gpt-b", mode="switch_model")
    assert steering.pending_count(run_id) == 1
    [claimed] = steering.claim_pending(run_id, 2)
    assert claimed["mode"] == "switch_model"
    other = _rid()
    steering.post(other, "openai/gpt-b", mode="switch_model")
    steering.post(other, "words")
    assert [m["body"] for m in steering.settle_turn([other])["undelivered"]] == ["words"]


def test_a_single_chat_turn_does_not_send_a_switch_as_its_next_turn():
    from chat.pipelines import _settle_steering
    run_id = _rid()
    steering.post(run_id, "openai/gpt-b", mode="switch_model")
    assert _settle_steering(run_id) == []


# ── the loop ─────────────────────────────────────────────────────────────────

def test_the_run_moves_to_the_new_model_with_the_same_tools_and_trail(switching):
    run_id = _rid()
    _DURING_TOOL["one"] = (run_id, "openai/gpt-b")
    state = LoopState(run_id=run_id)
    cached = [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]

    result = _run(_executor(switching.first, [steer_ext.SteeringExtension()], system=cached), state)

    assert result["output"] == "done"
    assert [label for label, _ in switching.seen] == ["a", "b", "b"]
    # Same tools bound on both models.
    assert {tuple(tools) for _label, tools in switching.bound} == {("echo",)}
    assert switching.built[0]["provider"] == "openai" and switching.built[0]["model"] == "gpt-b"
    # The second model sees the whole trail: the first call and its result.
    _label, messages = switching.seen[1]
    assert any(getattr(m, "tool_call_id", "") == "toolu_1" for m in messages)
    # OpenAI takes no cache_control markers: they are gone for the new model.
    assert messages[0].content == [{"type": "text", "text": "sys"}]

    [switch] = state.summary()["model_switches"]
    assert switch["to"] == "openai/gpt-b" and switch["after_step"] == 1 and switch["by"] == "anton"
    assert "error" not in switch
    answered = state.answered_by
    assert len(answered) == 2 and all(a["switched"] and a["fallback"] for a in answered)
    assert answered[0]["input_tokens"] == 2000 and answered[0]["price_model"] == "gpt-b"


def test_anthropic_history_converts_for_openai_after_the_switch(switching):
    from langchain_openai.chat_models.base import _convert_message_to_dict

    run_id = _rid()
    _DURING_TOOL["one"] = (run_id, "openai/gpt-b")
    _run(_executor(switching.first, [steer_ext.SteeringExtension()]), LoopState(run_id=run_id))
    _label, messages = switching.seen[1]
    converted = [_convert_message_to_dict(m) for m in messages]
    assert [m["role"] for m in converted] == ["system", "user", "assistant", "tool"]
    assistant = converted[2]
    assert assistant["tool_calls"][0]["function"]["name"] == "echo"
    # Anthropic's tool_use block is not sent to OpenAI as content.
    assert all(b.get("type") != "tool_use" for b in assistant["content"])
    assert converted[3]["tool_call_id"] == "toolu_1"


def test_the_switched_calls_are_priced_at_the_new_model(switching):
    run_id = _rid()
    _DURING_TOOL["one"] = (run_id, "openai/gpt-b")
    state = LoopState(run_id=run_id)
    _run(_executor(switching.first, [steer_ext.SteeringExtension()]), state)
    run = {"provider": "anthropic", "model": "claude-a", "loop": state.summary(),
           "process": {"token_usage": {"inbound_tokens": 6000, "outbound_tokens": 600}}}
    prices = {("anthropic", "claude-a"): (1.0, 1.0, 0.0), ("openai", "gpt-b"): (10.0, 10.0, 0.0)}
    expected = (1000 + 100) / 1e6 * 1.0 + (5000 + 500) / 1e6 * 10.0
    assert abs(run_cost_usd(run, prices) - expected) < 1e-12


def test_a_switch_to_a_model_no_longer_enabled_keeps_the_run_on_its_model(switching, monkeypatch):
    from tools import delegation
    monkeypatch.setattr(delegation, "enabled_models", lambda: [CATALOG[0]])
    run_id = _rid()
    _DURING_TOOL["one"] = (run_id, "openai/gpt-b")
    state = LoopState(run_id=run_id)
    first = _Recording(label="a", disable_streaming=True, messages=iter([
                           AIMessage(content="", tool_calls=[{"name": "echo", "args": {"text": "one"}, "id": "c1"}]),
                           AIMessage(content="still a"),
                       ]))
    result = _run(_executor(first, [steer_ext.SteeringExtension()]), state)
    assert result["output"] == "still a"
    assert [label for label, _ in switching.seen] == ["a", "a"]
    [switch] = state.model_switches
    assert "not an enabled catalog model" in switch["error"]


def test_a_run_picking_up_again_stays_on_the_switched_model(switching):
    run_id = _rid()
    steering.post(run_id, "openai/gpt-b", mode="switch_model")
    steering.claim_pending(run_id, 3)
    state = LoopState(run_id=run_id)
    ext = steer_ext.SteeringExtension()
    ext.shape_messages(state, {"intermediate_steps": []}, [])
    assert state.scratch[steer_ext.SWITCH_SCRATCH]["model"] == "gpt-b"
    assert state.injections == [] and state.system_messages == []


def test_the_latest_switch_wins(switching, monkeypatch):
    run_id = _rid()
    steering.post(run_id, "openai/gpt-b", mode="switch_model")
    steering.post(run_id, "anthropic/claude-a", mode="switch_model")
    state = LoopState(run_id=run_id)
    steer_ext.SteeringExtension().shape_messages(state, {"intermediate_steps": []}, [])
    assert state.scratch[steer_ext.SWITCH_SCRATCH]["provider"] == "anthropic"
    assert [s["to"] for s in state.model_switches] == ["openai/gpt-b", "anthropic/claude-a"]


def test_with_fallbacks_the_switched_answer_is_recorded_once(switching):
    run_id = _rid()
    _DURING_TOOL["one"] = (run_id, "openai/gpt-b")
    state = LoopState(run_id=run_id)
    spare = _Recording(label="spare", disable_streaming=True, messages=iter([]))
    fb = fallback_ext.FallbackExtension({"provider": "anthropic", "model": "claude-a"},
                                        [({"provider": "openai", "model": "spare"}, spare)])
    _run(_executor(switching.first, [steer_ext.SteeringExtension(), fb]), state)
    kinds = [(a["model"], bool(a.get("switched"))) for a in state.answered_by]
    assert kinds == [("claude-a", False), ("gpt-b", True), ("gpt-b", True)]


def test_a_switch_during_the_final_answer_is_no_reason_for_another_pass():
    run_id = _rid()
    steering.post(run_id, "openai/gpt-b", mode="switch_model")
    state = LoopState(run_id=run_id)
    assert steer_ext.claim_after_answer(state, 2) == []
    assert [s["to"] for s in state.model_switches] == ["openai/gpt-b"]


def test_delivery_of_a_switch_is_announced_with_the_model(switching):
    from common import stream_sink

    events = []
    token = stream_sink.set_emitter(events.append)
    try:
        run_id = _rid()
        msg = steering.post(run_id, "openai/gpt-b", mode="switch_model")
        steer_ext.SteeringExtension().shape_messages(LoopState(run_id=run_id), {"intermediate_steps": []}, [])
    finally:
        stream_sink.reset_emitter(token)
    assert events == [{"type": "steer_delivered", "msg_id": msg["msg_id"], "after_step": 0,
                       "run_id": run_id, "mode": "switch_model", "model": "openai/gpt-b"}]


# ── the route ────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from common.auth import Principal
    from routes import steering as steering_routes
    from tools import delegation

    monkeypatch.setattr(delegation, "enabled_models", lambda: list(CATALOG))
    app = FastAPI()

    @app.middleware("http")
    async def _principal(request: Request, call_next):
        who = request.headers.get("x-test-principal")
        if who:
            kind, _, rest = who.partition(":")
            uid, _, role = rest.partition(":")
            request.state.principal = Principal(id=uid, username=uid, role=role or "member", kind=kind)
        return await call_next(request)

    app.include_router(steering_routes.router)
    return TestClient(app)


def _open(run_id, **kw):
    from managers import run_manager
    kw.setdefault("status", "running")
    run_manager.open_run(run_id, kw.pop("agent_id", "helper"), link_to_session=False, **kw)


def _switch(client, run_id, model, principal=None):
    headers = {"x-test-principal": principal} if principal else {}
    return client.post(f"/api/runs/{run_id}/steer", json={"message": model, "mode": "switch_model"},
                       headers=headers)


def test_the_route_stores_the_canonical_catalog_id(client):
    run_id = _rid()
    _open(run_id, session_type="task")
    resp = _switch(client, run_id, "gpt-b")
    assert resp.status_code == 200, resp.text
    assert resp.json()["next"] == "wait"
    assert resp.json()["message"]["body"] == "openai/gpt-b"


def test_the_route_refuses_a_model_that_is_not_enabled(client):
    run_id = _rid()
    _open(run_id, session_type="task")
    resp = _switch(client, run_id, "openai/gpt-zzz")
    assert resp.status_code == 400 and "not an enabled catalog model" in resp.json()["detail"]
    assert steering.pending_count(run_id) == 0


def test_an_agent_may_not_switch_its_own_run(client):
    run_id = _rid()
    _open(run_id, session_type="task")
    assert _switch(client, run_id, "openai/gpt-b", principal="service:svc:admin").status_code == 403


def test_a_team_run_takes_no_switch(client, monkeypatch):
    from routes import steering as steering_routes
    team = SimpleNamespace(team_run_id="trun-1", workspace=None, status="running",
                           team_id="t", conversation_id="c")
    monkeypatch.setattr(steering_routes, "_team_run", lambda rid: team)
    assert _switch(client, "trun-1", "openai/gpt-b").status_code == 400
