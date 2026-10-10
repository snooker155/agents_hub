"""
Steering a running run (docs/steering.md): the store (common/steering.py),
the loop extension that places messages before the next model call
(agents/loop_ext/steering.py), the state transports, the routes
(dashboard/backend/routes/steering.py) and the chat pipeline's part.

No provider is called: the loop runs a fake chat model the way
tests/test_agent_loop.py does, and the launcher is replaced where a route
would start a run.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any, List

import pytest
from langchain.agents import AgentExecutor
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

from agents import agent_loop
from agents.agent_loop import LoopState, build_agent_runnable
from agents.loop_ext import steering as steer_ext
from common import steering


def _rid() -> str:
    return f"run-{uuid.uuid4().hex[:10]}"


# ── the store ────────────────────────────────────────────────────────────────

def test_post_list_and_claim_in_order():
    run_id = _rid()
    a = steering.post(run_id, "first", author={"id": "u1", "name": "ann"})
    b = steering.post(run_id, "second")
    assert a["status"] == "pending" and a["author_name"] == "ann"
    assert steering.pending_count(run_id) == 2
    assert [m["body"] for m in steering.undelivered(run_id)] == ["first", "second"]

    claimed = steering.claim_pending(run_id, 3)
    assert [m["msg_id"] for m in claimed] == [a["msg_id"], b["msg_id"]]
    assert all(m["status"] == "delivered" and m["delivered_step"] == 3 for m in claimed)
    # Taken once: a second claimer gets nothing.
    assert steering.claim_pending(run_id, 4) == []
    assert steering.pending_count(run_id) == 0
    listed = steering.list_for_run(run_id)
    assert [m["status"] for m in listed] == ["delivered", "delivered"]
    assert [m["body"] for m in steering.delivered(run_id)] == ["first", "second"]


def test_claim_ignores_interrupts_and_other_runs():
    run_id = _rid()
    steering.post(run_id, "stop and do this", mode="interrupt")
    steering.post(_rid(), "someone else's")
    assert steering.claim_pending(run_id, 0) == []
    assert steering.pending_count(run_id) == 0


def test_claim_with_nothing_pending_opens_no_transaction(monkeypatch):
    from common import db

    run_id = _rid()
    steering.post(run_id, "taken")
    steering.claim_pending(run_id, 0)

    def _no_tx():
        raise AssertionError("a write transaction was opened for an empty check")

    monkeypatch.setattr(db, "transaction", _no_tx)
    assert steering.claim_pending(run_id, 1) == []
    assert steering.claim_pending(_rid(), 0) == []


def test_mark_expired_only_touches_pending_injects():
    run_id = _rid()
    done = steering.post(run_id, "took this")
    steering.claim_pending(run_id, 1)
    left = steering.post(run_id, "never taken")
    expired = steering.mark_expired(run_id)
    assert [m["msg_id"] for m in expired] == [left["msg_id"]]
    assert steering.get(done["msg_id"])["status"] == "delivered"
    assert steering.get(left["msg_id"])["status"] == "expired"
    # Idempotent: nothing is returned twice.
    assert steering.mark_expired(run_id) == []


def test_post_validates():
    with pytest.raises(ValueError):
        steering.post(_rid(), "   ")
    with pytest.raises(ValueError):
        steering.post(_rid(), "hi", mode="shout")
    with pytest.raises(ValueError):
        steering.post("", "hi")


def test_mark_records_the_next_run():
    msg = steering.post(_rid(), "go another way", mode="interrupt")
    row = steering.mark(msg["msg_id"], steering.STATUS_INTERRUPTED, next_run_id="run-next")
    assert row["status"] == "interrupted" and row["next_run_id"] == "run-next"
    assert row["delivered_at"]


# ── the loop extension ───────────────────────────────────────────────────────

class _ToolModel(GenericFakeChatModel):
    """A fake chat model that records what it was sent."""

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
    # A person typing while the tool runs: the message lands between two
    # model calls.
    pending = _DURING_TOOL.pop(text, None)
    if pending:
        steering.post(*pending)
    return f"echo:{text}"


def _prompt():
    return ChatPromptTemplate.from_messages([
        SystemMessage(content="sys"),
        MessagesPlaceholder(variable_name="chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])


def _executor(replies):
    _ToolModel.seen = []
    model = _ToolModel(disable_streaming=True, messages=iter(replies))
    runnable = build_agent_runnable(model, [echo], _prompt(), [steer_ext.SteeringExtension()])
    return AgentExecutor(agent=runnable, tools=[echo], return_intermediate_steps=True)


def _two_tool_calls():
    return [
        AIMessage(content="", tool_calls=[{"name": "echo", "args": {"text": "one"}, "id": "c1"}]),
        AIMessage(content="", tool_calls=[{"name": "echo", "args": {"text": "two"}, "id": "c2"}]),
        AIMessage(content="done"),
    ]


def _kinds(messages):
    out = []
    for m in messages:
        if isinstance(m, HumanMessage):
            out.append("steer" if steer_ext.is_steering_text(m.content) else "human")
        elif isinstance(m, AIMessage):
            out.append("ai")
        elif isinstance(m, ToolMessage):
            out.append("tool")
        elif isinstance(m, SystemMessage):
            out.append("system")
    return out


def _run(executor, state):
    token = agent_loop.set_state(state)
    try:
        return executor.invoke({"input": "go"})
    finally:
        agent_loop.reset_state(token)


def test_messages_are_placed_after_their_step_and_stay_there(monkeypatch):
    monkeypatch.delenv("AGENT_RUN_ID", raising=False)
    run_id = _rid()
    before = steering.post(run_id, "use metric units")
    _DURING_TOOL["one"] = (run_id, "and keep it short")
    state = LoopState(run_id=run_id)

    result = _run(_executor(_two_tool_calls()), state)

    assert result["output"] == "done"
    first, second, third = _ToolModel.seen
    # Taken before the first call: right after the person's own turn.
    assert _kinds(first) == ["system", "human", "steer"]
    # The one typed during the first tool: after that tool's result.
    assert _kinds(second) == ["system", "human", "steer", "ai", "tool", "steer"]
    # Both still in place on the next call, the scratchpad rebuilt around them.
    assert _kinds(third) == ["system", "human", "steer", "ai", "tool", "steer", "ai", "tool"]
    assert "use metric units" in third[2].content
    assert "and keep it short" in third[5].content
    assert [(i["after_step"], i["text"]) for i in state.injections] == [
        (0, "use metric units"), (1, "and keep it short")]
    assert state.summary()["injections"][0]["msg_id"] == before["msg_id"]
    assert steering.pending_count(run_id) == 0


def test_no_run_id_or_no_message_leaves_the_chain_alone(monkeypatch):
    monkeypatch.delenv("AGENT_RUN_ID", raising=False)
    state = LoopState(run_id=_rid())
    _run(_executor(_two_tool_calls()), state)
    assert _kinds(_ToolModel.seen[-1]) == ["system", "human", "ai", "tool", "ai", "tool"]
    assert state.injections == []

    anonymous = LoopState(run_id="")
    _run(_executor(_two_tool_calls()), anonymous)
    assert anonymous.injections == []


def test_injected_sequence_is_valid_for_openai_and_anthropic(monkeypatch):
    monkeypatch.delenv("AGENT_RUN_ID", raising=False)
    run_id = _rid()
    _DURING_TOOL["one"] = (run_id, "check the second file too")
    _run(_executor(_two_tool_calls()), LoopState(run_id=run_id))
    messages = _ToolModel.seen[1]

    from providers.openai_driver import message_to_dict as _convert_message_to_dict
    roles = [_convert_message_to_dict(m)["role"] for m in messages]
    assert roles == ["system", "user", "assistant", "tool", "user"]

    from langchain_anthropic.chat_models import _format_messages
    _system, formatted = _format_messages(messages)
    assert [m["role"] for m in formatted] == ["user", "assistant", "user"]
    last = formatted[-1]["content"]
    # The tool result first, then the person's words, in one user turn.
    assert last[0]["type"] == "tool_result"
    assert last[-1]["type"] == "text" and "check the second file too" in last[-1]["text"]


def test_a_nested_run_with_the_same_id_does_not_take_the_parents_messages(monkeypatch):
    monkeypatch.delenv("AGENT_RUN_ID", raising=False)
    run_id = _rid()
    parent = LoopState(run_id=run_id)
    child = LoopState(run_id=run_id)
    ext = steer_ext.SteeringExtension()
    ext.shape_messages(parent, {"intermediate_steps": []}, [])
    steering.post(run_id, "for the parent")
    ext.shape_messages(child, {"intermediate_steps": []}, [])
    assert child.injections == []
    ext.shape_messages(parent, {"intermediate_steps": []}, [])
    assert [i["text"] for i in parent.injections] == ["for the parent"]


def test_a_run_picking_up_again_restores_what_it_took(monkeypatch):
    monkeypatch.delenv("AGENT_RUN_ID", raising=False)
    run_id = _rid()
    steering.post(run_id, "earlier words")
    steering.claim_pending(run_id, 2)
    state = LoopState(run_id=run_id)
    steer_ext.SteeringExtension().shape_messages(state, {"intermediate_steps": []}, [])
    # Restored where the run is now (it has no steps yet), not at step 2.
    assert [(i["after_step"], i["text"]) for i in state.injections] == [(0, "earlier words")]


class _FakeTransport:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def delivered_steering(self, run_id):
        return []

    def claim_steering(self, run_id, step):
        self.calls += 1
        return self.replies.pop(0) if self.replies else []


def test_own_process_run_claims_through_the_state_transport(monkeypatch):
    run_id = _rid()
    monkeypatch.setenv("AGENT_RUN_ID", run_id)
    fake = _FakeTransport([[{"msg_id": "m1", "body": "via http", "mode": "inject"}]])
    import common.state_transport as st
    monkeypatch.setattr(st, "get_state_transport", lambda: fake)
    state = LoopState(run_id=run_id)
    out = steer_ext.SteeringExtension().shape_messages(state, {"intermediate_steps": []}, [])
    assert fake.calls == 1
    assert steer_ext.is_steering_text(out[0].content) and "via http" in out[0].content


def test_transport_failures_stop_the_checks(monkeypatch):
    run_id = _rid()
    monkeypatch.setenv("AGENT_RUN_ID", run_id)
    fake = _FakeTransport([None] * 10)
    import common.state_transport as st
    monkeypatch.setattr(st, "get_state_transport", lambda: fake)
    state = LoopState(run_id=run_id)
    ext = steer_ext.SteeringExtension()
    for _ in range(6):
        ext.shape_messages(state, {"intermediate_steps": []}, [])
    assert fake.calls == steer_ext.MAX_TRANSPORT_FAILURES


def test_delivery_is_announced_on_the_chat_stream(monkeypatch):
    monkeypatch.delenv("AGENT_RUN_ID", raising=False)
    from common import stream_sink

    events = []
    token = stream_sink.set_emitter(events.append)
    try:
        run_id = _rid()
        msg = steering.post(run_id, "hello mid-turn")
        steer_ext.SteeringExtension().shape_messages(
            LoopState(run_id=run_id), {"intermediate_steps": []}, [])
    finally:
        stream_sink.reset_emitter(token)
    assert events == [{"type": "steer_delivered", "msg_id": msg["msg_id"],
                       "after_step": 0, "run_id": run_id}]


def test_extension_applies_to_standard_agents_only():
    from agents.standard_agent import StandardAgent

    fake = StandardAgent.__new__(StandardAgent)
    assert isinstance(steer_ext.extension_for(fake), steer_ext.SteeringExtension)
    assert steer_ext.extension_for(SimpleNamespace()) is None


# ── state transports and the run-state route ─────────────────────────────────

def test_direct_transport_claims_from_the_store():
    from common.state_transport import DirectStateTransport

    run_id = _rid()
    steering.post(run_id, "direct")
    got = DirectStateTransport().claim_steering(run_id, 5)
    assert [(m["body"], m["delivered_step"]) for m in got] == [("direct", 5)]
    assert [m["body"] for m in DirectStateTransport().delivered_steering(run_id)] == ["direct"]


def test_http_transport_posts_the_claim(monkeypatch):
    import requests
    from common.state_transport import HttpStateTransport

    sent = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"messages": [{"msg_id": "m1", "body": "x"}]}

    def _request(method, url, json=None, headers=None, timeout=None):
        sent.append((method, url, json, timeout))
        return _Resp()

    monkeypatch.setattr(requests, "request", _request)
    got = HttpStateTransport().claim_steering("run-9", 2)
    assert got == [{"msg_id": "m1", "body": "x"}]
    method, url, body, timeout = sent[0]
    assert method == "POST" and url.endswith("/api/run-state/runs/run-9/steering/claim")
    assert body == {"step": 2} and timeout <= HttpStateTransport.STEERING_TIMEOUT


def test_http_transport_reports_a_failed_call_as_none(monkeypatch):
    import requests
    from common.state_transport import HttpStateTransport

    def _boom(*a, **kw):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "request", _boom)
    assert HttpStateTransport().claim_steering("run-9", 0) is None


def test_run_state_claim_route():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import run_state

    app = FastAPI()
    app.include_router(run_state.router)
    client = TestClient(app)
    run_id = _rid()
    steering.post(run_id, "over the wire")
    resp = client.post(f"/api/run-state/runs/{run_id}/steering/claim", json={"step": 4})
    assert resp.status_code == 200
    assert [(m["body"], m["delivered_step"]) for m in resp.json()["messages"]] == [("over the wire", 4)]
    again = client.get(f"/api/run-state/runs/{run_id}/steering/delivered")
    assert [m["body"] for m in again.json()["messages"]] == ["over the wire"]


# ── the steer routes ─────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import steering as steering_routes

    app = FastAPI()
    app.include_router(steering_routes.router)
    return TestClient(app)


def _open(run_id, **kw):
    from managers import run_manager
    kw.setdefault("status", "running")
    run_manager.open_run(run_id, kw.pop("agent_id", "helper"), link_to_session=False, **kw)


def test_inject_is_stored_for_a_running_run(client):
    run_id = _rid()
    _open(run_id, session_type="task")
    resp = client.post(f"/api/runs/{run_id}/steer", json={"message": "  also do X  "})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["next"] == "wait"
    assert data["message"]["body"] == "also do X" and data["message"]["mode"] == "inject"
    listed = client.get(f"/api/runs/{run_id}/steer").json()
    assert [m["status"] for m in listed["messages"]] == ["pending"]


def test_steer_validation(client):
    run_id = _rid()
    _open(run_id)
    assert client.post(f"/api/runs/{run_id}/steer", json={"message": ""}).status_code == 400
    assert client.post(f"/api/runs/{run_id}/steer",
                       json={"message": "x", "mode": "rollback"}).status_code == 400
    assert client.post(f"/api/runs/{_rid()}/steer", json={"message": "x"}).status_code == 404


def test_a_finished_run_answers_409_with_its_status(client):
    run_id = _rid()
    _open(run_id, status="completed")
    resp = client.post(f"/api/runs/{run_id}/steer", json={"message": "too late"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["status"] == "completed"


def test_inject_to_a_remote_agent_is_refused(client, monkeypatch):
    from agents import registry
    monkeypatch.setattr(registry, "get_agent",
                        lambda agent_id: SimpleNamespace(is_remote=lambda: True))
    run_id = _rid()
    _open(run_id)
    resp = client.post(f"/api/runs/{run_id}/steer", json={"message": "hi"})
    assert resp.status_code == 409


def test_reading_a_finished_task_run_expires_what_it_never_took(client):
    from managers import run_manager
    run_id = _rid()
    _open(run_id, session_type="task")
    client.post(f"/api/runs/{run_id}/steer", json={"message": "late"})
    run_manager.update_run(run_id, {"status": "completed"})
    listed = client.get(f"/api/runs/{run_id}/steer").json()
    assert listed["status"] == "completed"
    assert [m["status"] for m in listed["messages"]] == ["expired"]


def test_interrupt_of_a_chat_turn_stops_it_and_hands_the_message_back(client):
    from managers import run_manager
    run_id = _rid()
    _open(run_id, session_type="chat", task_id="conv-1")
    resp = client.post(f"/api/runs/{run_id}/steer",
                       json={"message": "stop, do Y", "mode": "interrupt"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["next"] == "send"
    assert data["message"]["status"] == "interrupted"
    assert run_manager.get_run_by_id(run_id)["status"] == "stopped"


def test_interrupt_of_a_task_run_relaunches_with_the_message(client, monkeypatch):
    from agents import agent_launcher
    from common import session_service
    from managers import run_manager
    from tasks import service as tasks_service
    from tasks.models import TaskStatus

    task = tasks_service.create_task("Write the report", "about Q3")
    old_run = _rid()
    tasks_service.assign_agent(task.id, "helper", {"description": "Draft section two"}, run_id=old_run)
    tasks_service.update_task(task.id, status=TaskStatus.in_progress)
    _open(old_run, session_type="task", task_id=str(task.id))
    session_service.register_continuation("sess-1", str(task.id), agent_id="orchestrator",
                                          run_id=old_run)

    def _fake_stop(run_id):
        # What _stop_run_record does to a task run: the run is marked, the
        # task stopped and its assignment and continuations cleared.
        run_manager.update_run(run_id, {"status": "stop"})
        tasks_service.clear_agent(task.id)
        tasks_service.stop_task(task.id)
        session_service.drop_continuations_for_task(str(task.id))
        return True

    launched = []

    def _fake_start(task_id, agent_id, params=None, run_id=None):
        launched.append((task_id, agent_id, dict(params or {})))
        return "run-relaunched", "sess-x"

    monkeypatch.setattr(run_manager, "stop_run_by_id", _fake_stop)
    monkeypatch.setattr(agent_launcher, "start_run", _fake_start)

    resp = client.post(f"/api/runs/{old_run}/steer",
                       json={"message": "Use the new numbers", "mode": "interrupt"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["next"] == "relaunched" and data["next_run_id"] == "run-relaunched"
    assert data["message"]["status"] == "interrupted"
    assert data["message"]["next_run_id"] == "run-relaunched"

    (task_id, agent_id, params), = launched
    assert task_id == str(task.id) and agent_id == "helper"
    assert params["description"].startswith("Draft section two")
    assert "Use the new numbers" in params["description"]

    after = tasks_service.get_task(task.id)
    assert after.status == TaskStatus.in_progress
    assert str(after.assigned_agent_run_id) == "run-relaunched"
    # The orchestrator waiting on the task now waits on the new run.
    popped = session_service.pop_continuations_for_task(str(task.id), run_id="run-relaunched")
    assert [c["session_id"] for c in popped] == ["sess-1"]


def test_interrupt_that_cannot_stop_reports_409(client, monkeypatch):
    from managers import run_manager
    monkeypatch.setattr(run_manager, "stop_run_by_id", lambda run_id: False)
    run_id = _rid()
    _open(run_id, session_type="task")
    resp = client.post(f"/api/runs/{run_id}/steer", json={"message": "x", "mode": "interrupt"})
    assert resp.status_code == 409
    assert steering.list_for_run(run_id)[0]["status"] == "failed"


# ── the chat pipeline ────────────────────────────────────────────────────────

def test_chat_pipeline_passes_run_id_and_returns_undelivered(monkeypatch):
    import agents.registry as registry
    import chat.pipelines as pipelines
    from chat.models import ChatRequest
    from managers import run_manager

    monkeypatch.setattr(registry, "get_agent",
                        lambda agent_id: SimpleNamespace(tools=[], model_overrides=lambda: {}))
    seen = {}

    class _Agent:
        provider, model, system_prompt = "openai", "gpt-4o", "sys"

        async def arun(self, prompt, history=None, callbacks=None, **kwargs):
            seen.update(kwargs)
            # Written while the turn works, never taken by a model call.
            steering.post(kwargs["run_id"], "one more thing")
            return SimpleNamespace(ok=True, agent_output="answer", error=None, response=None,
                                   status="done", loop={"injections": [{"after_step": 1, "text": "t",
                                                                        "msg_id": "m0", "mode": "inject"}]})

    monkeypatch.setattr(pipelines, "create_agent",
                        lambda agent_id, workspace=None, streaming=False, **kw: _Agent())
    request = ChatRequest(agent_id="a", message="hello", conversation_id="conv-steer")

    async def _drive():
        return [event async for event in pipelines._run_chat_pipeline(request)]

    events = asyncio.run(_drive())
    done = events[-1]
    assert done["type"] == "done" and done["ok"] is True
    run_id = done["run_id"]
    assert seen["run_id"] == run_id
    assert [m["body"] for m in done["undelivered"]] == ["one more thing"]
    assert steering.list_for_run(run_id)[0]["status"] == "expired"
    record = run_manager.get_run_by_id(run_id)
    assert record["loop"]["injections"][0]["msg_id"] == "m0"


def test_prompt_split_ignores_a_steering_message():
    from chat.streaming import _without_steering

    history = [
        {"role": "user", "content": "earlier turn"},
        {"role": "assistant", "content": "earlier answer"},
        {"role": "user", "content": "the turn's own message"},
        {"role": "assistant", "content": "working on it"},
    ]
    msg, hist = _without_steering(steer_ext.format_injection("mid-turn"), history, "fallback")
    assert msg == "the turn's own message"
    assert hist == history[:2]
    assert _without_steering("plain", history, "x") == ("plain", history)


# ── a message that arrives while the model writes its final answer ───────────

class _PostingModel(_ToolModel):
    """Posts a steering message while it produces its N-th answer."""

    calls: int = 0
    post_on_call: int = 0
    post_args: tuple = ()

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        type(self).calls += 1
        if type(self).calls == type(self).post_on_call:
            steering.post(*type(self).post_args)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _standard_agent(monkeypatch, replies):
    from agents import agent_base
    from agents.standard_agent import StandardAgent

    _PostingModel.seen, _PostingModel.calls = [], 0
    model = _PostingModel(disable_streaming=True, messages=iter(replies))
    monkeypatch.setattr(agent_base, "build_chat_model", lambda **kw: model)
    return StandardAgent(agent_id="steered", name="steered", system_prompt="sys", tools=[echo])


def test_a_task_run_takes_a_message_sent_during_its_final_answer(monkeypatch):
    run_id = _rid()
    monkeypatch.setenv("AGENT_TASK_ID", "task-1")
    agent = _standard_agent(monkeypatch, [
        AIMessage(content="", tool_calls=[{"name": "echo", "args": {"text": "one"}, "id": "c1"}]),
        AIMessage(content="done"),
        AIMessage(content="done, with the changelog line"),
    ])
    _PostingModel.post_on_call, _PostingModel.post_args = 2, (run_id, "also add a changelog line")

    result = agent.run("add a flag", run_id=run_id)

    assert result.ok and result.agent_output == "done, with the changelog line"
    [inj] = result.loop["injections"]
    assert inj["text"] == "also add a changelog line" and inj["final"] is True
    assert steering.undelivered(run_id) == []
    # The extra pass saw its own earlier answer and then the message.
    last = _PostingModel.seen[-1]
    assert any(isinstance(m, AIMessage) and m.content == "done" for m in last)
    assert steer_ext.is_steering_text(last[-1].content)
    assert [s.name for s in result.steps] == ["echo"]


def test_a_chat_turn_leaves_such_a_message_to_the_client(monkeypatch):
    run_id = _rid()
    monkeypatch.delenv("AGENT_TASK_ID", raising=False)
    agent = _standard_agent(monkeypatch, [AIMessage(content="done"), AIMessage(content="unused")])
    _PostingModel.post_on_call, _PostingModel.post_args = 1, (run_id, "one more thing")

    result = agent.run("hello", run_id=run_id)

    assert result.agent_output == "done"
    assert [m["body"] for m in steering.undelivered(run_id)] == ["one more thing"]


# ── a message to a busy instance reaches its running task run ────────────────

@pytest.fixture
def busy_task_instance(monkeypatch):
    from agents import registry as agent_registry
    from instances import store as instance_store
    from managers import run_manager as rm

    monkeypatch.setattr(agent_registry, "get_agent",
                        lambda aid: SimpleNamespace(is_remote=lambda: False) if aid == "worker" else None)
    run_id = rm.new_unique_run_id()
    rm.open_run(run_id, "worker", task_id="task-9", status="running", link_to_session=False)
    instance = instance_store.create("worker", kind="task", state="active", current_run_id=run_id)
    return instance, run_id


def test_a_busy_task_copy_gets_the_message_before_its_next_step(busy_task_instance):
    from instances import delivery, inbox

    instance, run_id = busy_task_instance
    msg_id = inbox.enqueue(instance["instance_id"], "use the staging database")
    assert delivery.steer_running_task(instance, "use the staging database", msg_id) == run_id

    [taken] = steering.claim_pending(run_id, 2)
    assert taken["body"] == "use the staging database"
    # Taken once: the mailbox no longer holds it, so the idle drain cannot answer it again.
    assert inbox.claim_next(instance["instance_id"]) is None


def test_the_mailbox_answers_it_when_the_run_ended_first(busy_task_instance):
    from instances import delivery, inbox

    instance, run_id = busy_task_instance
    msg_id = inbox.enqueue(instance["instance_id"], "one more thing")
    delivery.steer_running_task(instance, "one more thing", msg_id)
    assert inbox.claim_next(instance["instance_id"])["msg_id"] == msg_id   # the idle drain got it

    assert steering.claim_pending(run_id, 3) == []
    [row] = steering.list_for_run(run_id)
    assert row["status"] == steering.STATUS_EXPIRED


def test_a_chat_or_idle_copy_is_not_steered(busy_task_instance):
    from instances import delivery, inbox
    from managers import run_manager as rm

    instance, run_id = busy_task_instance
    msg_id = inbox.enqueue(instance["instance_id"], "hi")
    assert delivery.steer_running_task({**instance, "state": "idle"}, "hi", msg_id) is None
    chat_run = rm.new_unique_run_id()
    rm.open_run(chat_run, "worker", status="running", link_to_session=False)
    assert delivery.steer_running_task({**instance, "current_run_id": chat_run}, "hi", msg_id) is None
    assert steering.list_for_run(run_id) == []


# ── flows and teams in chat, and a chat turn carried on from the run page ────

def test_a_message_a_finished_node_never_took_goes_to_the_next_node():
    first, second = _rid(), _rid()
    kept = steering.post(first, "use metric units")
    assert steering.retarget_pending([first], second) == 1
    [moved] = steering.claim_pending(second, 0)
    assert moved["msg_id"] == kept["msg_id"]           # the client still follows it by its id
    assert steering.claim_pending(first, 0) == []


def test_a_multi_run_turn_settles_what_was_and_was_not_delivered():
    node_a, node_b = _rid(), _rid()
    taken = steering.post(node_a, "shorter please")
    steering.claim_pending(node_a, 2)
    left = steering.post(node_b, "and in German")
    settled = steering.settle_turn([node_a, node_b])
    assert settled["delivered"] == [{"msg_id": taken["msg_id"], "after_step": 2}]
    assert settled["undelivered"] == [{"msg_id": left["msg_id"], "body": "and in German"}]


def _team_run(status="running"):
    from teams import store as team_store
    from teams.models import TeamRun
    run = TeamRun(team_id="team-1", workspace=None, status=status, goal="write a plan",
                  conversation_id="conv-team")
    team_store.save_run(run)
    return run


def test_a_team_board_posts_a_steered_message_for_the_next_member():
    from teams.runner import STEER_KIND, STEER_SENDER, _Board

    run = _team_run()
    board = _Board(run)
    board.post(sender="(request)", content="write a plan", round_no=0, kind="goal")
    msg = steering.post(run.team_run_id, "keep it under a page")
    view = board.view("writer")
    assert "keep it under a page" in view and f"{STEER_SENDER}:" in view
    posted = board.messages[-1]
    assert posted.kind == STEER_KIND and posted.run_id == msg["msg_id"]
    board.view("writer")
    assert sum(1 for m in board.messages if m.kind == STEER_KIND) == 1   # taken once


def test_the_route_steers_a_running_team(client, monkeypatch):
    run = _team_run()
    resp = client.post(f"/api/runs/{run.team_run_id}/steer", json={"message": "add costs"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["next"] == "wait" and resp.json()["team"] is True
    listed = client.get(f"/api/runs/{run.team_run_id}/steer").json()
    assert [m["body"] for m in listed["messages"]] == ["add costs"]

    stopped = []
    import teams.launcher as tl
    monkeypatch.setattr(tl, "stop_team_run", lambda rid: stopped.append(rid) or True)
    resp = client.post(f"/api/runs/{run.team_run_id}/steer",
                       json={"message": "stop, do Y", "mode": "interrupt"})
    assert resp.json()["next"] == "send" and stopped == [run.team_run_id]
    done = _team_run(status="completed")
    assert client.post(f"/api/runs/{done.team_run_id}/steer", json={"message": "x"}).status_code == 409


def test_the_run_page_interrupt_sends_the_next_chat_turn_itself(client, monkeypatch):
    from routes import steering as steering_routes

    sent = []
    monkeypatch.setattr(steering_routes, "_send_next_turn", lambda req: sent.append(req))
    run_id = _rid()
    _open(run_id, session_type="chat", task_id="conv-7", agent_id="helper")
    resp = client.post(f"/api/runs/{run_id}/steer",
                       json={"message": "answer in German", "mode": "interrupt", "send": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["next"] == "sent" and resp.json()["conversation_id"] == "conv-7"
    [req] = sent
    assert req.agent_id == "helper" and req.conversation_id == "conv-7"
    assert req.message == "answer in German" and req.source == "steer"


def test_an_interrupted_flow_node_carries_the_flow_on(client, monkeypatch):
    from routes import steering as steering_routes

    sent = []
    monkeypatch.setattr(steering_routes, "_send_next_turn", lambda req: sent.append(req))
    run_id = _rid()
    _open(run_id, session_type="chat", task_id="conv-8", channel="chat_flow", flow_id="flow-9")
    resp = client.post(f"/api/runs/{run_id}/steer",
                       json={"message": "skip the review", "mode": "interrupt", "send": True})
    assert resp.json()["next"] == "sent"
    assert sent[0].flow_id == "flow-9" and sent[0].agent_id is None
