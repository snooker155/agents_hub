"""
A turn belongs to the conversation, not to the window that asked for it.

Until now a chat turn's events went down the response body of one request, so a
second tab, another device and the run's own page all sat still until the answer
was finished. These tests cover the two halves of the fix: the pipeline putting
every event on the conversation's channel (``chat.broadcast``), and the short
term memory that lets someone arriving mid-answer catch up before following it
(``common.live_runs``).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


class FakeRequest:
    """The parts of a ChatRequest the broadcaster reads."""

    def __init__(self, **over):
        self.conversation_id = over.get("conversation_id", "conv-1")
        self.message = over.get("message", "how do I ship this?")
        self.client_id = over.get("client_id", "tab-a")
        self.agent_id = over.get("agent_id", "assistant")
        self.flow_id = None
        self.team_id = None
        self.source = over.get("source")


async def _pipeline(events):
    for event in events:
        yield event


def _drain(queue: asyncio.Queue):
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


@pytest.fixture(autouse=True)
def clean_live_runs():
    from common import live_runs

    live_runs.reset()
    yield
    live_runs.reset()


@pytest.fixture
def broker():
    from common.session_broker import broker as _broker

    yield _broker


# ── the broadcast ────────────────────────────────────────────────────────────

async def _body_test_every_event_reaches_the_conversation_channel(broker):
    from chat.broadcast import broadcast_turn

    client_id, queue = broker.open_client(["chat:conv-1"])
    events = [
        {"type": "meta", "run_id": "run-1", "session_id": "sess-1"},
        {"type": "token", "token": "he"},
        {"type": "token", "token": "llo"},
        {"type": "done", "ok": True, "response": "hello", "run_id": "run-1"},
    ]

    seen = [e async for e in broadcast_turn(FakeRequest(), _pipeline(events))]
    broker.close_client(client_id)

    # The caller's own stream is untouched: same events, same order.
    assert [e["type"] for e in seen] == ["meta", "token", "token", "done"]
    published = [e["type"] for e in _drain(queue)]
    assert published == ["turn_start", "meta", "token", "token", "done", "chat_stream_end"]


async def _body_test_published_events_name_the_tab_that_started_the_turn(broker):
    """Without this the sending tab renders every token twice: once from its own
    stream and once from the echo."""
    from chat.broadcast import broadcast_turn

    client_id, queue = broker.open_client(["chat:conv-1"])
    async for _ in broadcast_turn(FakeRequest(client_id="tab-a"),
                                  _pipeline([{"type": "token", "token": "hi"}])):
        pass
    broker.close_client(client_id)

    published = _drain(queue)
    assert all(e["origin_client"] == "tab-a" for e in published)
    assert all(e["conversation_id"] == "conv-1" for e in published)


async def _body_test_a_conversationless_turn_is_not_broadcast(broker):
    """A turn with no conversation (an eval, a one-off call) has no channel to
    go on, and must still run."""
    from chat.broadcast import broadcast_turn

    seen = [e async for e in broadcast_turn(FakeRequest(conversation_id=None),
                                            _pipeline([{"type": "token", "token": "x"}]))]
    assert [e["type"] for e in seen] == ["token"]


async def _body_test_a_failing_pipeline_still_tells_the_channel(broker):
    from chat.broadcast import broadcast_turn

    client_id, queue = broker.open_client(["chat:conv-1"])

    async def exploding():
        yield {"type": "token", "token": "par"}
        raise RuntimeError("model died")

    with pytest.raises(RuntimeError):
        async for _ in broadcast_turn(FakeRequest(), exploding()):
            pass
    broker.close_client(client_id)

    published = _drain(queue)
    assert [e["type"] for e in published] == ["turn_start", "token", "done", "chat_stream_end"]
    assert published[-2]["ok"] is False


async def _body_test_a_viewer_is_told_when_the_asker_walks_away(broker):
    """The browser that started the turn navigates off mid-answer, which closes
    the generator. Everyone else is still watching, and a mirror that is never
    told the turn ended stays live for good."""
    from chat.broadcast import broadcast_turn

    broker.set_loop(asyncio.get_running_loop())
    client_id, queue = broker.open_client(["chat:conv-1"])

    async def slow():
        yield {"type": "token", "token": "half"}
        yield {"type": "token", "token": " an answer"}

    gen = broadcast_turn(FakeRequest(), slow())
    async for _ in gen:
        break  # the consumer goes away after the first event
    await gen.aclose()
    # The sentinel is handed to the loop rather than awaited, so let it run.
    await asyncio.sleep(0)
    broker.close_client(client_id)

    assert [e["type"] for e in _drain(queue)] == ["turn_start", "token", "chat_stream_end"]


# ── catching up ──────────────────────────────────────────────────────────────

async def _body_test_a_turn_in_flight_can_be_read_before_it_ends():
    """The point of the whole thing: a page that opens mid-answer asks what has
    been said so far instead of joining in the middle of a word."""
    from chat.broadcast import broadcast_turn
    from common import live_runs

    async def slow():
        yield {"type": "meta", "run_id": "run-1", "session_id": "sess-1"}
        yield {"type": "think", "content": "checking the docs"}
        yield {"type": "tool_start", "step": 1, "tool": "shell", "input": "ls"}
        yield {"type": "token", "token": "half an "}
        # A reader arriving right here sees everything above.
        snapshot = live_runs.by_conversation("conv-1")
        assert snapshot["text"] == "half an "
        assert snapshot["user_message"] == "how do I ship this?"
        assert snapshot["run_id"] == "run-1"
        assert snapshot["status"] == "running"
        assert [x["content"] for x in snapshot["thinking"]] == ["checking the docs"]
        assert snapshot["tools"][0]["tool"] == "shell"
        yield {"type": "token", "token": "answer"}

    async for _ in broadcast_turn(FakeRequest(), slow()):
        pass

    done = live_runs.by_run("run-1")
    assert done["text"] == "half an answer"
    assert done["status"] == "finished"


def test_a_relayed_run_is_tracked_by_its_run_id():
    """Task and node runs execute in another process and reach the backend as
    relayed events; they get the same live tail."""
    from common import live_runs

    # Every relayed event names its run (``SessionPublishCallback`` stamps it),
    # which is the only way the backend can tell one run on a session from
    # another. An event without it has nowhere to go and is dropped.
    live_runs.record_run_event({"type": "meta", "run_id": "r9", "agent_id": "worker"},
                               session_id="sess-9")
    live_runs.record_run_event({"type": "token", "token": "working", "run_id": "r9"},
                               session_id="sess-9")
    live_runs.record_run_event({"type": "token", "token": " (lost)"}, session_id="sess-9")

    snapshot = live_runs.by_run("r9")
    assert snapshot["text"] == "working"
    assert snapshot["agent_id"] == "worker"
    assert snapshot["session_id"] == "sess-9"


def test_the_live_tail_is_bounded():
    from common import live_runs

    turn = live_runs.start_turn(conversation_id="c", user_message="go")
    live_runs.record(turn, {"type": "meta", "run_id": "r1"})
    live_runs.record(turn, {"type": "token", "token": "x" * (live_runs.MAX_TEXT + 500)})

    assert len(live_runs.by_run("r1")["text"]) == live_runs.MAX_TEXT


def test_one_conversation_keeps_one_live_turn():
    from common import live_runs

    first = live_runs.start_turn(conversation_id="c", user_message="first")
    live_runs.record(first, {"type": "meta", "run_id": "r1"})
    live_runs.start_turn(conversation_id="c", user_message="second")

    assert live_runs.by_conversation("c")["user_message"] == "second"
    assert live_runs.by_run("r1") is None


# ── the turn survives the browser ────────────────────────────────────────────

async def _body_test_a_finished_turn_is_written_to_the_chat():
    """A tab closed mid-answer used to take the answer with it, although the run
    had completed and been recorded."""
    from chat.broadcast import broadcast_turn
    from common import chat_store

    chat_store.save_chat({"id": "conv-1", "title": "shipping", "messages": []})
    done = {"type": "done", "ok": True, "response": "ship it", "run_id": "run-1",
            "usage": {"total_tokens": 42}, "duration_ms": 1200}

    async for _ in broadcast_turn(FakeRequest(), _pipeline([done])):
        pass

    messages = chat_store.get_chat("conv-1")["messages"]
    assert [m["role"] for m in messages] == ["user", "agent"]
    assert messages[0]["content"] == "how do I ship this?"
    assert messages[1]["content"] == "ship it"
    assert messages[1]["run_id"] == "run-1"
    assert messages[1]["total_tokens"] == 42


async def _body_test_the_browsers_own_version_of_a_turn_is_not_duplicated():
    """The page that ran the turn writes a richer version of it. Whoever gets
    there first holds the turn; the other leaves it alone."""
    from chat.broadcast import broadcast_turn
    from common import chat_store

    chat_store.save_chat({"id": "conv-1", "messages": [
        {"id": "m1", "role": "user", "content": "how do I ship this?"},
        {"id": "m2", "role": "agent", "content": "ship it", "run_id": "run-1",
         "tool_calls": 3},
    ]})

    async for _ in broadcast_turn(FakeRequest(), _pipeline([
        {"type": "done", "ok": True, "response": "ship it", "run_id": "run-1"},
    ])):
        pass

    messages = chat_store.get_chat("conv-1")["messages"]
    assert len(messages) == 2
    assert messages[1]["tool_calls"] == 3


async def _body_test_a_conversation_the_dashboard_never_kept_is_not_invented():
    """A Telegram thread or an instance delivery has no chat row; writing one
    would put conversations in the sidebar that nothing ever opens."""
    from chat.broadcast import broadcast_turn
    from common import chat_store

    async for _ in broadcast_turn(FakeRequest(conversation_id="tg-77", source="telegram"),
                                  _pipeline([{"type": "done", "ok": True,
                                              "response": "hi", "run_id": "run-2"}])):
        pass

    assert chat_store.get_chat("tg-77") is None


async def _body_test_a_telegram_turn_does_not_append_to_its_mirror_row():
    """The dashboard does keep a row for a Telegram thread, but its transcript
    is rebuilt from the runs; appending here would be a second copy."""
    from chat.broadcast import broadcast_turn
    from common import chat_store

    chat_store.save_chat({"id": "tg-77", "origin": "telegram", "messages": []})

    async for _ in broadcast_turn(FakeRequest(conversation_id="tg-77", source="telegram"),
                                  _pipeline([{"type": "done", "ok": True,
                                              "response": "hi", "run_id": "run-2"}])):
        pass

    assert chat_store.get_chat("tg-77")["messages"] == []


async def _body_test_a_failed_turn_is_not_written():
    from chat.broadcast import broadcast_turn
    from common import chat_store

    chat_store.save_chat({"id": "conv-1", "messages": []})

    async for _ in broadcast_turn(FakeRequest(), _pipeline([
        {"type": "done", "ok": False, "error": "model died", "run_id": "run-3"},
    ])):
        pass

    assert chat_store.get_chat("conv-1")["messages"] == []


# ── the endpoints ────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import chats

    app = FastAPI()
    app.include_router(chats.router)
    return TestClient(app)


def test_the_live_endpoint_answers_null_for_an_idle_conversation(client):
    assert client.get("/api/chats/nothing-here/live").json() == {"turn": None}


def test_the_live_endpoint_returns_the_turn_in_flight(client):
    from common import live_runs

    turn = live_runs.start_turn(conversation_id="conv-9", user_message="hello")
    live_runs.record(turn, {"type": "meta", "run_id": "run-9"})
    live_runs.record(turn, {"type": "token", "token": "partial"})

    body = client.get("/api/chats/conv-9/live").json()["turn"]
    assert body["text"] == "partial"
    assert body["run_id"] == "run-9"


# ── running the async bodies above ──────────────────────────────────────────
# The suite has no pytest-asyncio; async work is driven with asyncio.run, and
# the broker's queues are created and read inside that same loop.


def test_every_event_reaches_the_conversation_channel(broker):
    asyncio.run(_body_test_every_event_reaches_the_conversation_channel(broker))

def test_published_events_name_the_tab_that_started_the_turn(broker):
    asyncio.run(_body_test_published_events_name_the_tab_that_started_the_turn(broker))

def test_a_conversationless_turn_is_not_broadcast(broker):
    asyncio.run(_body_test_a_conversationless_turn_is_not_broadcast(broker))

def test_a_failing_pipeline_still_tells_the_channel(broker):
    asyncio.run(_body_test_a_failing_pipeline_still_tells_the_channel(broker))

def test_a_turn_in_flight_can_be_read_before_it_ends():
    asyncio.run(_body_test_a_turn_in_flight_can_be_read_before_it_ends())


def test_a_viewer_is_told_when_the_asker_walks_away(broker):
    asyncio.run(_body_test_a_viewer_is_told_when_the_asker_walks_away(broker))

def test_a_finished_turn_is_written_to_the_chat():
    asyncio.run(_body_test_a_finished_turn_is_written_to_the_chat())

def test_the_browsers_own_version_of_a_turn_is_not_duplicated():
    asyncio.run(_body_test_the_browsers_own_version_of_a_turn_is_not_duplicated())

def test_a_conversation_the_dashboard_never_kept_is_not_invented():
    asyncio.run(_body_test_a_conversation_the_dashboard_never_kept_is_not_invented())


def test_a_telegram_turn_does_not_append_to_its_mirror_row():
    asyncio.run(_body_test_a_telegram_turn_does_not_append_to_its_mirror_row())

def test_a_failed_turn_is_not_written():
    asyncio.run(_body_test_a_failed_turn_is_not_written())
