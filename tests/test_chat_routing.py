"""Chat turns on service replicas (chat/routing.py, chat/turns.py).

The backend hands a chat turn to a replica and relays its events; the
replica runs the pipeline and posts them back. Under test: when the routing
is on, the relay end to end against a fake replica (subscription before the
message exists, filtering by turn, the end marker, the start and idle
timeouts, a replica that dies), the blocking send over the relay, and the
replica's side: the turn context on the run record, event stamps and order,
token batching, and the mailbox handing a turn to the executor.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from chat import routing, turns
from chat.models import ChatRequest
from instances import inbox, registry, store as istore
from managers import run_manager as rm
from services import store


class _Spec(SimpleNamespace):
    def is_remote(self) -> bool:
        return False


@pytest.fixture
def fake_agent(monkeypatch):
    spec = _Spec(id="swe_agent", name="SWE")
    monkeypatch.setattr("agents.registry.get_agent",
                        lambda agent_id: spec if agent_id == "swe_agent" else None)
    monkeypatch.setattr("chat.runs.registry.get_agent",
                        lambda agent_id: spec if agent_id == "swe_agent" else None)
    return spec


def _replica(service):
    inst = registry.ensure_instance("swe_agent", kind="resident", workspace=service["workspace"],
                                    state="standby", service_id=service["service_id"])
    return istore.update(inst["instance_id"], carrier_status="running", carrier_mode="local", pid=1)


@pytest.fixture(autouse=True)
def _alive(monkeypatch):
    """No test here may spawn a process: a carrier start is a fake pid, which
    then reads as alive."""
    from instances import carrier

    class _Proc:
        pid = 424242

    # Patched on the carrier module, not on ``subprocess`` itself: the real
    # ``subprocess.run`` (git, docker probes) must keep working around it.
    monkeypatch.setattr(carrier, "subprocess",
                        SimpleNamespace(Popen=lambda cmd, **kwargs: _Proc(), STDOUT=-2))
    monkeypatch.setattr("common.config.agent_execution_mode", lambda: "local")
    monkeypatch.setattr(carrier, "_pid_exists", lambda pid: True)


# ── enabled() ────────────────────────────────────────────────────────────────

def test_routing_is_off_inside_a_replica_and_on_a_worker(monkeypatch):
    monkeypatch.setattr("common.config.chat_execution", lambda: "instances")
    monkeypatch.setattr("common.config.hub_role", lambda: "all")
    monkeypatch.delenv("AGENT_INSTANCE_ID", raising=False)
    assert routing.enabled() is True
    monkeypatch.setenv("AGENT_INSTANCE_ID", "inst_x")
    assert routing.enabled() is False
    monkeypatch.delenv("AGENT_INSTANCE_ID", raising=False)
    monkeypatch.setattr("common.config.hub_role", lambda: "worker")
    assert routing.enabled() is False
    monkeypatch.setattr("common.config.hub_role", lambda: "api")
    monkeypatch.setattr("common.config.chat_execution", lambda: "inprocess")
    assert routing.enabled() is False


# ── The relay ────────────────────────────────────────────────────────────────

def _fake_replica_answers(channel, wait_for_msg, events):
    """Play the replica: once the turn is in the mailbox, post its events on
    the conversation's channel the way chat/turns.py does."""
    from common.session_broker import broker

    async def _play():
        msg_id = await wait_for_msg()
        stamp = {"turn_msg_id": msg_id, "conversation_id": channel.split(":", 1)[1]}
        # Another turn's event on the same channel: the relay must ignore it.
        await broker.apublish(channel, {"type": "token", "token": "STRAY", "turn_msg_id": "other"})
        await broker.apublish(channel, {**stamp, "type": "turn_start", "message": "hi"})
        for ev in events:
            await broker.apublish(channel, {**stamp, **ev})
        await broker.apublish(channel, {**stamp, "type": "chat_stream_end"})
    return _play


def _claimer(instance_id):
    async def _wait():
        for _ in range(100):
            msg = await asyncio.to_thread(inbox.claim_next, instance_id)
            if msg:
                return msg["msg_id"]
            await asyncio.sleep(0.02)
        raise AssertionError("no turn arrived in the mailbox")
    return _wait


def test_relay_yields_the_replicas_events_and_stops_at_the_end_marker(monkeypatch, fake_agent):
    monkeypatch.setattr("services.routing.default_environment", lambda ws: (None, None))
    svc = store.create(name="s", agent_id="swe_agent", workspace="ws")
    rep = _replica(svc)
    request = ChatRequest(agent_id="swe_agent", message="hi", workspace="ws", conversation_id="conv-1")
    play = _fake_replica_answers("chat:conv-1", _claimer(rep["instance_id"]), [
        {"type": "meta", "run_id": "run-1", "agent_id": "swe_agent"},
        {"type": "token", "token": "hel"},
        {"type": "token", "token": "lo"},
        {"type": "done", "ok": True, "response": "hello", "run_id": "run-1"},
    ])

    async def _run():
        task = asyncio.create_task(play())
        got = [ev async for ev in routing.relay(request, "agent")]
        await task
        return got

    events = asyncio.run(_run())
    assert [e["type"] for e in events] == ["meta", "token", "token", "done"]
    assert "STRAY" not in "".join(e.get("token", "") for e in events)
    assert all("turn_msg_id" not in e for e in events)
    assert events[-1]["response"] == "hello"
    # The turn was written into the replica's mailbox with the request.
    history = inbox.history(rep["instance_id"])
    assert history[0]["kind"] == inbox.KIND_TURN
    assert inbox.payload_of(history[0])["request"]["message"] == "hi"


def test_relay_reports_a_replica_that_never_picks_the_turn_up(monkeypatch, fake_agent):
    monkeypatch.setattr("services.routing.default_environment", lambda ws: (None, None))
    monkeypatch.setattr(routing, "_timeouts", lambda: {"start": 0.3, "idle": 5.0})
    monkeypatch.setattr(routing, "STATE_CHECK_SECONDS", 0.1)
    svc = store.create(name="s", agent_id="swe_agent", workspace="ws")
    rep = _replica(svc)
    request = ChatRequest(agent_id="swe_agent", message="hi", workspace="ws", conversation_id="conv-2")

    async def _run():
        return [ev async for ev in routing.relay(request, "agent")]

    events = asyncio.run(_run())
    assert len(events) == 1 and events[0]["type"] == "done" and events[0]["ok"] is False
    assert events[0]["status"] == 504 and "did not pick the turn up" in events[0]["error"]
    pending = inbox.pending(rep["instance_id"])
    assert pending and pending[0]["error"]


def test_relay_reports_a_replica_that_died_before_answering(monkeypatch, fake_agent):
    monkeypatch.setattr("services.routing.default_environment", lambda ws: (None, None))
    monkeypatch.setattr(routing, "_timeouts", lambda: {"start": 5.0, "idle": 5.0})
    monkeypatch.setattr(routing, "STATE_CHECK_SECONDS", 0.05)
    svc = store.create(name="s", agent_id="swe_agent", workspace="ws")
    rep = _replica(svc)
    request = ChatRequest(agent_id="swe_agent", message="hi", workspace="ws", conversation_id="conv-3")

    async def _run():
        async def _kill():
            await asyncio.sleep(0.2)
            istore.update(rep["instance_id"], state="failed", carrier_status="failed", carrier_error="oom")
        asyncio.create_task(_kill())
        return [ev async for ev in routing.relay(request, "agent")]

    events = asyncio.run(_run())
    assert events[0]["type"] == "done" and events[0]["status"] == 503
    assert "oom" in events[0]["error"]


def test_relay_refuses_a_paused_service_and_an_unknown_agent(monkeypatch, fake_agent):
    from fastapi import HTTPException
    monkeypatch.setattr("services.routing.default_environment", lambda ws: (None, None))
    svc = store.create(name="s", agent_id="swe_agent", workspace="ws")
    store.pause(svc["service_id"])

    async def _run(req):
        return [ev async for ev in routing.relay(req, "agent")]

    events = asyncio.run(_run(ChatRequest(agent_id="swe_agent", message="hi", workspace="ws")))
    assert events[0]["type"] == "done" and events[0]["status"] == 503
    with pytest.raises(HTTPException):
        asyncio.run(_run(ChatRequest(agent_id="nobody", message="hi", workspace="ws")))


def test_a_refusal_of_the_relay_reaches_the_conversation_channel(monkeypatch, fake_agent):
    """A tab following the turn over the shared stream (and any other viewer)
    learns of a failure the relay decides itself, not only the caller."""
    from common.session_broker import broker

    monkeypatch.setattr("services.routing.default_environment", lambda ws: (None, None))
    svc = store.create(name="s", agent_id="swe_agent", workspace="ws")
    store.pause(svc["service_id"])
    request = ChatRequest(agent_id="swe_agent", message="hi", workspace="ws",
                          conversation_id="conv-r", client_id="tab", client_turn_id="k1")

    async def _run():
        viewer, queue = broker.open_client(["chat:conv-r"])
        events = [ev async for ev in routing.relay(request, "agent")]
        published = []
        while not queue.empty():
            published.append(queue.get_nowait())
        broker.close_client(viewer)
        return events, published

    events, published = asyncio.run(_run())
    assert events[0]["type"] == "done" and events[0]["status"] == 503
    assert "client_turn_id" not in events[0]
    assert [e["type"] for e in published] == ["done", "chat_stream_end"]
    assert all(e["client_turn_id"] == "k1" and e["origin_client"] == "tab" for e in published)
    assert published[0]["status"] == 503


def test_the_shared_stream_route_reports_a_turn_the_relay_refused(monkeypatch):
    """POST /api/chat/stream-sse returns before the turn runs; a request the
    relay raises on (an unknown agent) must still end the turn on the channel."""
    import sys
    from pathlib import Path

    from fastapi import HTTPException

    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from common.session_broker import broker
    from routes import chat as chat_routes

    async def _refusing(request):
        raise HTTPException(status_code=404, detail="Agent 'nobody' not found")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(chat_routes, "_pipeline_for", _refusing)
    monkeypatch.setattr(routing, "enabled", lambda: True)
    request = ChatRequest(agent_id="swe_agent", message="hi", conversation_id="conv-s",
                          client_turn_id="k2")

    async def _run():
        viewer, queue = broker.open_client(["chat:conv-s"])
        out = await chat_routes.stream_message_sse(request)
        for _ in range(20):
            if not chat_routes._PUMP_TASKS:
                break
            await asyncio.sleep(0.01)
        published = []
        while not queue.empty():
            published.append(queue.get_nowait())
        broker.close_client(viewer)
        return out, published

    out, published = asyncio.run(_run())
    assert out["conversation_id"] == "conv-s"
    assert [e["type"] for e in published] == ["done", "chat_stream_end"]
    assert published[0]["status"] == 404 and "nobody" in published[0]["error"]
    assert all(e["client_turn_id"] == "k2" for e in published)


def test_run_chat_pipeline_relays_when_routing_is_on(monkeypatch):
    from chat import pipelines

    seen = {}

    async def _relay(request, kind):
        seen["kind"] = kind
        yield {"type": "done", "ok": True, "response": "routed", "run_id": "r"}

    monkeypatch.setattr(routing, "enabled", lambda: True)
    monkeypatch.setattr(routing, "relay", _relay)

    async def _run():
        return [ev async for ev in pipelines.run_chat_flow_pipeline(
            ChatRequest(flow_id="f1", message="go"))]

    assert asyncio.run(_run())[0]["response"] == "routed" and seen["kind"] == "flow"


def test_send_chat_message_folds_the_relayed_turn(monkeypatch):
    from chat import pipelines
    from chat.send import ChatSendError, send_chat_message

    monkeypatch.setattr(routing, "enabled", lambda: True)

    async def _ok(request, kind):
        yield {"type": "meta", "run_id": "r1"}
        yield {"type": "handoff", "run_id": "r1", "next_run_id": "r2", "to_agent_id": "b"}
        yield {"type": "done", "ok": True, "response": "final", "run_id": "r2", "agent_id": "b",
               "handoff": {"to_agent_id": "b"}, "handoffs": [{"to_agent_id": "b"}]}

    monkeypatch.setattr(routing, "relay", _ok)
    out = asyncio.run(send_chat_message(ChatRequest(agent_id="a", message="hi")))
    assert out["response"] == "final" and out["ok"] is True and out["run_id"] == "r2"
    assert out["agent_id"] == "b" and out["handoffs"] == [{"to_agent_id": "b"}]

    async def _refused(request, kind):
        yield {"type": "done", "ok": False, "error": "no replica", "status": 503}

    monkeypatch.setattr(routing, "relay", _refused)
    with pytest.raises(ChatSendError) as exc:
        asyncio.run(send_chat_message(ChatRequest(agent_id="a", message="hi")))
    assert exc.value.status == 503
    assert pipelines.kind_of(ChatRequest(team_id="t", message="x")) == "team"


# ── The replica's side ───────────────────────────────────────────────────────

class _Posted:
    def __init__(self):
        self.calls = []

    def __call__(self, event, channels):
        self.calls.append((dict(event), list(channels)))


def test_execute_turn_runs_the_pipeline_with_the_turn_context_and_stamps_events(monkeypatch, fake_agent):
    from chat import pipelines, runs as chat_runs

    svc = store.create(name="s", agent_id="swe_agent", workspace="ws", agent_version=2)
    rep = _replica(svc)
    seen = {}

    async def _fake_pipeline(request, kind=None):
        seen["context"] = chat_runs.turn_context()
        seen["overrides"] = chat_runs.turn_overrides()
        from common import identity
        seen["user"] = identity.current_user_id()
        yield {"type": "meta", "run_id": "run-9", "agent_id": request.agent_id}
        yield {"type": "token", "token": "a"}
        yield {"type": "token", "token": "b"}
        yield {"type": "tool_start", "tool": "read_file"}
        yield {"type": "done", "ok": True, "response": "ab", "run_id": "run-9"}

    monkeypatch.setattr(pipelines, "execute_locally", _fake_pipeline)
    posted = []

    def _send(self, event, channels):
        posted.append((dict(event), list(channels or self.channels)))

    monkeypatch.setattr(turns.EventForwarder, "_send", _send)
    monkeypatch.setattr(turns, "TOKEN_FLUSH_SECONDS", 60.0)

    request = ChatRequest(agent_id="swe_agent", message="hi", workspace="ws",
                          conversation_id="conv-7", client_id="tab-3")
    msg_id = inbox.enqueue(rep["instance_id"], "hi", conversation_id="conv-7", kind=inbox.KIND_TURN,
                           payload={"kind": "agent", "request": request.model_dump(mode="json"),
                                    "user_id": "user-1", "agent_version": 2})
    message = inbox.claim_next(rep["instance_id"])
    run_id = turns.execute_turn(rep["instance_id"], None, message)

    assert run_id == "run-9"
    assert seen["context"]["instance_id"] == rep["instance_id"]
    assert seen["context"]["service_id"] == svc["service_id"]
    assert seen["context"]["msg_id"] == msg_id and seen["context"]["conversation_id"] == "conv-7"
    assert seen["overrides"] == {"definition_version": 2}
    assert seen["user"] == "user-1"
    assert inbox.get(msg_id)["run_id"] == "run-9"

    types = [e["type"] for e, _ in posted]
    assert types == ["turn_start", "meta", "token", "tool_start", "done", "chat_stream_end",
                     "instance_stream_end"]
    # Consecutive tokens were merged and flushed before the tool event.
    token = next(e for e, _ in posted if e["type"] == "token")
    assert token["token"] == "ab"
    for e, channels in posted:
        if e["type"] in ("meta", "token", "tool_start", "done"):
            assert e["turn_msg_id"] == msg_id and e["origin_client"] == "tab-3"
            assert e["conversation_id"] == "conv-7"
            assert sorted(channels) == ["chat:conv-7", f"instance:{rep['instance_id']}"]
    meta = next(e for e, _ in posted if e["type"] == "meta")
    assert meta["instance_id"] == rep["instance_id"] and meta["msg_id"] == msg_id
    assert [c for e, c in posted if e["type"] == "chat_stream_end"] == [["chat:conv-7"]]
    assert [c for e, c in posted if e["type"] == "instance_stream_end"] == [[f"instance:{rep['instance_id']}"]]


def test_execute_turn_reports_a_pipeline_failure_and_marks_the_message(monkeypatch, fake_agent):
    from chat import pipelines
    from fastapi import HTTPException

    svc = store.create(name="s", agent_id="swe_agent", workspace="ws")
    rep = _replica(svc)

    async def _boom(request, kind=None):
        raise HTTPException(status_code=402, detail="budget")
        yield  # unreachable, makes this an async generator

    monkeypatch.setattr(pipelines, "execute_locally", _boom)
    posted = []
    monkeypatch.setattr(turns.EventForwarder, "_send",
                        lambda self, event, channels: posted.append(dict(event)))
    request = ChatRequest(agent_id="swe_agent", message="hi", workspace="ws", conversation_id="c")
    msg_id = inbox.enqueue(rep["instance_id"], "hi", conversation_id="c", kind=inbox.KIND_TURN,
                           payload={"kind": "agent", "request": request.model_dump(mode="json")})
    turns.execute_turn(rep["instance_id"], None, inbox.claim_next(rep["instance_id"]))
    done = next(e for e in posted if e["type"] == "done")
    assert done["ok"] is False and done["status"] == 402 and done["error"] == "budget"
    assert posted[-2]["type"] == "chat_stream_end" and posted[-1]["type"] == "instance_stream_end"
    assert inbox.get(msg_id)["error"] == "budget"


def test_create_chat_run_under_a_turn_context_records_a_carrier_run(fake_agent):
    from chat import runs as chat_runs

    svc = store.create(name="s", agent_id="swe_agent", workspace="ws")
    rep = _replica(svc)
    request = ChatRequest(agent_id="swe_agent", message="hello there", workspace="ws",
                          conversation_id="conv-c")
    token = chat_runs.set_turn_context({
        "instance_id": rep["instance_id"], "service_id": svc["service_id"], "msg_id": "m-1",
        "conversation_id": "conv-c", "budget_usd": 1.5})
    try:
        run_id, _, _, _, _ = chat_runs.create_chat_run(request)
    finally:
        chat_runs.reset_turn_context(token)
    run = rm.get_run_by_id(run_id)
    assert run["instance_id"] == rep["instance_id"] and run["carrier_run"] is True
    assert run["service_id"] == svc["service_id"] and run["conversation_id"] == "conv-c"
    assert run["inbox_msg_id"] == "m-1" and run["budget_usd"] == 1.5
    # No chat instance of its own was opened for the conversation.
    assert istore.list_instances(workspace="ws", kind="chat")["total"] == 0
    # Without a context the old behaviour stands: a chat instance per conversation.
    run_id2, *_ = chat_runs.create_chat_run(request)
    assert rm.get_run_by_id(run_id2)["instance_id"] != rep["instance_id"]
    assert istore.list_instances(workspace="ws", kind="chat")["total"] == 1


def test_answer_message_hands_a_turn_to_the_executor_and_a_runner_takes_turns_only(monkeypatch):
    from runtime import instance_run

    handed = []
    monkeypatch.setattr(turns, "execute_turn", lambda iid, ws, message: handed.append(message["msg_id"]) or "r")
    inst = registry.ensure_instance("", kind="runner", workspace="ws", state="standby")
    iid = inst["instance_id"]
    turn_id = inbox.enqueue(iid, "hi", kind=inbox.KIND_TURN, payload={"kind": "agent", "request": {}})
    assert instance_run.answer_message(iid, "", None, inbox.claim_next(iid)) == "r"
    assert handed == [turn_id]
    plain = inbox.enqueue(iid, "plain text")
    assert instance_run.answer_message(iid, "", None, inbox.claim_next(iid)) is None
    assert "no agent" in inbox.get(plain)["error"]
    loop = instance_run.InstanceLoop(iid, "", None)
    loop.maybe_sweep_tasks({"take_tasks": True})
    assert loop.task_thread is None
    loop.pool.shutdown(wait=False)


def test_event_forwarder_batches_tokens_by_size_and_flushes_before_other_events(monkeypatch):
    sent = []
    fwd = turns.EventForwarder("inst-1", ["chat:c"], port=1)
    monkeypatch.setattr(fwd, "_send", lambda event, channels: sent.append((event, channels)))
    monkeypatch.setattr(turns, "TOKEN_FLUSH_SECONDS", 60.0)
    monkeypatch.setattr(turns, "TOKEN_FLUSH_CHARS", 5)
    fwd.post({"type": "token", "token": "abc", "run_id": "r"})
    assert sent == []
    fwd.post({"type": "token", "token": "def", "run_id": "r"})
    assert sent[0][0]["token"] == "abcdef"
    fwd.post({"type": "token", "token": "g", "run_id": "r"})
    fwd.post({"type": "thinking", "run_id": "r"}, ["instance:i"])
    assert [e["type"] for e, _ in sent] == ["token", "token", "thinking"]
    assert sent[1][0]["token"] == "g" and sent[2][1] == ["instance:i"]
    payload = json.dumps(sent[0][0])
    assert "abcdef" in payload
