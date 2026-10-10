"""
Steering mode ``system`` (docs/steering.md): an operator's addition to a
running run's instructions, appended to the system prompt for the rest of the
run (common/steering.py, agents/loop_ext/steering.py, the steer route).

No provider is called: the loop runs the same recording fake chat model as
tests/test_steering.py.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, List

import pytest
from langchain.agents import AgentExecutor
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

from agents import agent_loop
from agents.agent_loop import LoopState, build_agent_runnable
from agents.loop_ext import steering as steer_ext
from common import steering


def _rid() -> str:
    return f"run-{uuid.uuid4().hex[:10]}"


# ── the store ────────────────────────────────────────────────────────────────

def test_a_system_message_is_claimed_and_read_back_like_an_inject():
    run_id = _rid()
    msg = steering.post(run_id, "answer in German", mode="system")
    assert msg["mode"] == "system"
    assert steering.pending_count(run_id) == 1
    [claimed] = steering.claim_pending(run_id, 3)
    assert claimed["mode"] == "system" and claimed["delivered_step"] == 3
    # A resumed run reads it back, so the addition survives a checkpoint resume.
    assert [m["body"] for m in steering.delivered(run_id)] == ["answer in German"]


def test_an_unclaimed_system_message_is_not_handed_back_as_a_chat_turn():
    run_id = _rid()
    steering.post(run_id, "spoken words")
    steering.post(run_id, "an instruction", mode="system")
    settled = steering.settle_turn([run_id])
    assert [m["body"] for m in settled["undelivered"]] == ["spoken words"]
    assert {m["status"] for m in steering.list_for_run(run_id)} == {"expired"}


def test_a_single_chat_turn_does_not_hand_an_instruction_back_either():
    from chat.pipelines import _settle_steering

    run_id = _rid()
    steering.post(run_id, "say it again")
    steering.post(run_id, "never mention prices", mode="system")
    assert [m["body"] for m in _settle_steering(run_id)] == ["say it again"]


def test_a_flow_node_hands_a_waiting_system_message_to_the_next_node():
    first, second = _rid(), _rid()
    steering.post(first, "be brief", mode="system")
    assert steering.retarget_pending([first], second) == 1
    assert [m["mode"] for m in steering.undelivered(second)] == ["system"]


# ── the loop ─────────────────────────────────────────────────────────────────

class _ToolModel(GenericFakeChatModel):
    seen: List[Any] = []

    def bind_tools(self, tools, **kwargs):
        return self.bind(tools=[getattr(t, "name", t) for t in tools])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        type(self).seen.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


_DURING_TOOL: dict = {}


@tool
def echo(text: str) -> str:
    """Echo the text back."""
    pending = _DURING_TOOL.pop(text, None)
    if pending:
        steering.post(pending[0], pending[1], mode="system")
    return f"echo:{text}"


def _executor(replies, system: Any = "sys"):
    _ToolModel.seen = []
    model = _ToolModel(disable_streaming=True, messages=iter(replies))
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content=system),
        MessagesPlaceholder(variable_name="chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    runnable = build_agent_runnable(model, [echo], prompt, [steer_ext.SteeringExtension()])
    return AgentExecutor(agent=runnable, tools=[echo], return_intermediate_steps=True)


def _replies():
    return [
        AIMessage(content="", tool_calls=[{"name": "echo", "args": {"text": "one"}, "id": "c1"}]),
        AIMessage(content="", tool_calls=[{"name": "echo", "args": {"text": "two"}, "id": "c2"}]),
        AIMessage(content="done"),
    ]


def _run(executor, state):
    token = agent_loop.set_state(state)
    try:
        return executor.invoke({"input": "go"})
    finally:
        agent_loop.reset_state(token)


def test_the_addition_goes_into_the_system_prompt_from_the_next_call_on(monkeypatch):
    monkeypatch.delenv("AGENT_RUN_ID", raising=False)
    run_id = _rid()
    _DURING_TOOL["one"] = (run_id, "never delete files")
    state = LoopState(run_id=run_id)

    result = _run(_executor(_replies()), state)

    assert result["output"] == "done"
    first, second, third = _ToolModel.seen
    assert first[0].content == "sys"
    for call in (second, third):
        system = call[0]
        assert isinstance(system, SystemMessage)
        assert system.content.startswith("sys")
        assert steer_ext.SYSTEM_HEADER in system.content and "never delete files" in system.content
        # One system message, nothing added to the conversation itself.
        assert sum(isinstance(m, SystemMessage) for m in call) == 1
        assert not any(isinstance(m, HumanMessage) and steer_ext.is_steering_text(m.content) for m in call)
    assert state.injections == []
    [entry] = state.summary()["system_messages"]
    assert entry["text"] == "never delete files" and entry["after_step"] == 1 and entry["mode"] == "system"


def test_a_cached_block_list_system_prompt_keeps_its_blocks():
    cached = [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
    out = steer_ext.append_system_addition(
        [SystemMessage(content=cached), HumanMessage(content="hi")],
        [{"text": "use metric units"}])
    blocks = out[0].content
    assert blocks[0] == cached[0]
    assert blocks[1]["type"] == "text" and "use metric units" in blocks[1]["text"]
    assert "cache_control" not in blocks[1]


def test_the_append_form_is_valid_for_anthropic_and_openai(monkeypatch):
    monkeypatch.delenv("AGENT_RUN_ID", raising=False)
    run_id = _rid()
    _DURING_TOOL["one"] = (run_id, "cite every source")
    _run(_executor(_replies()), LoopState(run_id=run_id))
    messages = _ToolModel.seen[1]

    from providers.anthropic_driver import format_messages as _format_messages
    system, formatted = _format_messages(messages)
    assert "cite every source" in str(system)
    assert [m["role"] for m in formatted] == ["user", "assistant", "user"]

    from providers.openai_driver import message_to_dict as _convert_message_to_dict
    roles = [_convert_message_to_dict(m)["role"] for m in messages]
    assert roles == ["system", "user", "assistant", "tool"]


def test_a_run_picking_up_again_keeps_the_addition(monkeypatch):
    monkeypatch.delenv("AGENT_RUN_ID", raising=False)
    run_id = _rid()
    steering.post(run_id, "stay in the docs folder", mode="system")
    steering.claim_pending(run_id, 4)
    state = LoopState(run_id=run_id)
    ext = steer_ext.SteeringExtension()
    ext.shape_messages(state, {"intermediate_steps": []}, [])
    assert [m["text"] for m in state.system_messages] == ["stay in the docs folder"]
    assert state.injections == []
    shaped = ext.shape_prompt(state, [SystemMessage(content="sys"), HumanMessage(content="go")])
    assert "stay in the docs folder" in shaped[0].content


def test_delivery_of_a_system_message_is_announced_with_its_mode(monkeypatch):
    monkeypatch.delenv("AGENT_RUN_ID", raising=False)
    from common import stream_sink

    events = []
    token = stream_sink.set_emitter(events.append)
    try:
        run_id = _rid()
        msg = steering.post(run_id, "shorter", mode="system")
        steer_ext.SteeringExtension().shape_messages(
            LoopState(run_id=run_id), {"intermediate_steps": []}, [])
    finally:
        stream_sink.reset_emitter(token)
    assert events == [{"type": "steer_delivered", "msg_id": msg["msg_id"],
                       "after_step": 0, "run_id": run_id, "mode": "system"}]


class _PostingModel(_ToolModel):
    calls: int = 0
    post_on_call: int = 0
    post_args: tuple = ()

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        type(self).calls += 1
        if type(self).calls == type(self).post_on_call:
            steering.post(*type(self).post_args, mode="system")
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_a_system_message_during_the_final_answer_gets_one_more_pass(monkeypatch):
    from agents import agent_base
    from agents.standard_agent import StandardAgent

    run_id = _rid()
    monkeypatch.setenv("AGENT_TASK_ID", "task-1")
    _PostingModel.seen, _PostingModel.calls = [], 0
    model = _PostingModel(disable_streaming=True, messages=iter([
        AIMessage(content="draft"), AIMessage(content="draft, in German"),
    ]))
    monkeypatch.setattr(agent_base, "build_chat_model", lambda **kw: model)
    _PostingModel.post_on_call, _PostingModel.post_args = 1, (run_id, "answer in German")
    agent = StandardAgent(agent_id="steered", name="steered", system_prompt="sys", tools=[echo])

    result = agent.run("write it", run_id=run_id)

    assert result.agent_output == "draft, in German"
    assert "injections" not in result.loop
    assert [m["text"] for m in result.loop["system_messages"]] == ["answer in German"]
    last = _PostingModel.seen[-1]
    assert "answer in German" in last[0].content
    assert last[-1].content == StandardAgent.SYSTEM_FOLLOWUP_TEXT


# ── the route ────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from common.auth import Principal
    from routes import steering as steering_routes

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


def _steer(client, run_id, principal=None, **body):
    headers = {"x-test-principal": principal} if principal else {}
    return client.post(f"/api/runs/{run_id}/steer",
                       json={"message": "keep answers short", "mode": "system", **body}, headers=headers)


def test_the_operator_sends_a_system_message(client):
    run_id = _rid()
    _open(run_id, session_type="task")
    resp = _steer(client, run_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["next"] == "wait"
    assert resp.json()["message"]["mode"] == "system"
    assert steering.pending_count(run_id) == 1


def test_an_agents_own_credential_may_not_send_one(client):
    run_id = _rid()
    _open(run_id, session_type="task")
    resp = _steer(client, run_id, principal="service:svc:admin")
    assert resp.status_code == 403
    assert steering.pending_count(run_id) == 0


@pytest.fixture
def multi(monkeypatch):
    from common import identity
    monkeypatch.setattr(identity, "current_mode", lambda: "multi")
    monkeypatch.setattr(identity, "membership_role", lambda ws, uid: "editor")
    from common import chat_store
    monkeypatch.setattr(chat_store, "get_chat",
                        lambda cid: {"id": cid, "owner": "u-owner"} if cid.startswith("conv-") else None)


def test_in_multi_mode_only_the_owner_or_an_admin_may(client, multi):
    run_id = _rid()
    _open(run_id, session_type="chat", task_id=f"conv-{uuid.uuid4().hex[:6]}")
    assert _steer(client, run_id, principal="user:u-other:member").status_code == 403
    assert _steer(client, run_id, principal="user:u-owner:member").status_code == 200
    assert _steer(client, run_id, principal="user:u-admin:admin").status_code == 200
    # Inject stays open to any editor.
    assert _steer(client, run_id, principal="user:u-other:member", mode="inject").status_code == 200


def test_a_run_with_no_known_owner_is_left_to_admins(client, multi):
    run_id = _rid()
    _open(run_id, session_type="delegation")
    assert _steer(client, run_id, principal="user:u-owner:member").status_code == 403
    assert _steer(client, run_id, principal="user:u-admin:admin").status_code == 200


def test_a_team_run_takes_no_system_message(client, monkeypatch):
    from routes import steering as steering_routes
    team = SimpleNamespace(team_run_id="trun-1", workspace=None, status="running",
                           team_id="t", conversation_id="c")
    monkeypatch.setattr(steering_routes, "_team_run", lambda rid: team)
    assert _steer(client, "trun-1").status_code == 400


def test_a_remote_agent_takes_no_system_message(client, monkeypatch):
    from agents import registry
    monkeypatch.setattr(registry, "get_agent", lambda agent_id: SimpleNamespace(is_remote=lambda: True))
    run_id = _rid()
    _open(run_id)
    assert _steer(client, run_id).status_code == 409
