"""
The assistant's past conversations (tools/assistant_conversations.py,
routes/assistant.py): clearing the transcript drops the conversation instead
of filing it, the assistant lists past conversations ten at a time by
workspace, and ``open`` makes one the live conversation once the turn ends.
"""
# The fixtures are test_assistant's, imported by name, which ruff reads as redefinitions.
# ruff: noqa: F811
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from test_assistant import (  # noqa: E402,F401 - fixtures
    _Result, _events, client, fake_agent, seeded, single,
)

KIND = "assistant"
THREAD = "user-local"


def _store():
    from common.entity_chat_store import entity_chat_store
    return entity_chat_store()


def _turn(client, message, workspace=None):
    body = {"message": message, **({"workspace": workspace} if workspace else {})}
    response = client.post("/api/assistant", json=body)
    assert response.status_code == 200, response.text
    return _events(response)


def _calls_tool(monkeypatch, fake_agent, calls):
    """The fake model calls ``assistant_conversations`` with each of ``calls``
    in turn, as the running turn (its run named the way the loop names it)."""
    from common import db
    from tools.assistant_conversations import assistant_conversations
    results = []

    async def arun(prompt, callbacks=None):
        row = db.get_conn().execute(
            "SELECT run_id FROM runs WHERE task_id LIKE 'assistantchat:%' AND status = 'running' "
            "ORDER BY created_at DESC LIMIT 1").fetchone()
        monkeypatch.setenv("AGENT_RUN_ID", row["run_id"])
        results.append(json.loads(assistant_conversations.invoke(calls.pop(0))))
        return _Result()

    fake_agent.arun = arun
    return results


# ── the store ────────────────────────────────────────────────────────────────

def test_clear_without_archive_drops_the_conversation_but_opens_a_new_epoch():
    store = _store()
    store.append_message(KIND, "t1", "user", "first")
    kept = store.clear(KIND, "t1")
    store.append_message(KIND, "t1", "user", "second")
    dropped = store.clear(KIND, "t1", archive=False)
    assert dropped == kept + 1
    titles = [s["title"] for s in store.history(KIND, "t1")["sessions"]]
    assert titles == ["", "first"]


def test_a_requested_switch_is_taken_once_and_a_bare_request_is_not_filed():
    store = _store()
    store.append_message(KIND, "t2", "user", "about dogs", meta={"workspace": "default"})
    store.clear(KIND, "t2")
    assert not store.request_switch(KIND, "t2", "s9")
    assert store.request_switch(KIND, "t2", "s0", run_id="r1")
    assert store.take_switch(KIND, "t2")["session_id"] == "s0"
    assert store.take_switch(KIND, "t2") is None
    store.append_message(KIND, "t2", "user", "go back to dogs")
    restored = store.activate_session(KIND, "t2", "s0", drop_active=True)
    assert restored["messages"][0]["content"] == "about dogs"
    sessions = store.history(KIND, "t2")["sessions"]
    assert [s["title"] for s in sessions] == ["about dogs"]
    assert sessions[0]["workspaces"] == ["default"]


# ── the routes ───────────────────────────────────────────────────────────────

def test_clearing_the_transcript_leaves_nothing_in_the_past_conversations(single, client, seeded, fake_agent):
    _turn(client, "remember this one")
    assert client.delete("/api/assistant").json()["cleared"]
    _turn(client, "forget this one")
    assert client.delete("/api/assistant/conversation").json()["cleared"]
    assert client.get("/api/assistant").json()["messages"] == []
    titles = [s["title"] for s in _store().history(KIND, THREAD)["sessions"]]
    assert titles == ["", "remember this one"]


def test_a_turn_stamps_the_workspace_it_ran_in(single, client, seeded, fake_agent):
    _turn(client, "hello")
    said = _store().get_messages(KIND, THREAD)[0]
    assert said["role"] == "user" and said["workspace"] == "default"


# ── the tool ─────────────────────────────────────────────────────────────────

def test_the_list_gives_ten_at_a_time_newest_first_without_the_live_one(
        single, client, seeded, fake_agent, monkeypatch):
    for i in range(12):
        _turn(client, f"topic {i}")
        client.delete("/api/assistant")
    results = _calls_tool(monkeypatch, fake_agent, [
        {"action": "list"}, {"action": "list", "offset": 10}, {"action": "list", "query": "topic 3"},
    ])
    _turn(client, "what did we talk about")
    _turn(client, "and the next ten")
    _turn(client, "the one about topic 3")
    first, second, found = results
    assert first["ok"] and first["total"] == 12 and first["has_more"] and first["next_offset"] == 10
    assert [c["title"] for c in first["conversations"]][:2] == ["topic 11", "topic 10"]
    assert len(first["conversations"]) == 10
    assert [c["title"] for c in second["conversations"]] == ["topic 1", "topic 0"]
    assert not second["has_more"] and second["next_offset"] is None
    assert [c["title"] for c in found["conversations"]] == ["topic 3"]
    assert all("what did we talk" not in c["title"] for c in first["conversations"] + second["conversations"])


def test_the_list_is_by_workspace_unless_all(single, client, seeded, fake_agent, monkeypatch):
    from workspace import create_workspace_folder
    create_workspace_folder("sales")
    _turn(client, "a sales question", workspace="sales")
    client.delete("/api/assistant")
    _turn(client, "a default question")
    client.delete("/api/assistant")
    results = _calls_tool(monkeypatch, fake_agent, [{"action": "list"}, {"action": "list", "workspace": "all"}])
    _turn(client, "what did we talk about")
    _turn(client, "everywhere")
    here, everywhere = results
    assert [c["title"] for c in here["conversations"]] == ["a default question"]
    assert here["workspace"] == "default"
    assert {c["title"] for c in everywhere["conversations"]} == {"a sales question", "a default question"}


def test_open_switches_to_the_conversation_when_the_turn_ends(single, client, seeded, fake_agent, monkeypatch):
    _turn(client, "about dogs")
    client.delete("/api/assistant")
    _turn(client, "about cats")
    client.delete("/api/assistant")
    results = _calls_tool(monkeypatch, fake_agent, [{"action": "open", "conversation": "s0"}])
    events = _turn(client, "go back to the dogs")
    assert results[0]["ok"] and results[0]["opening"] == "s0"
    assert {"type": "conversation", "session_id": "s0"} in events
    live = client.get("/api/assistant").json()["messages"]
    assert live[0]["content"] == "about dogs"
    # The conversation that was only the request is not filed among the past ones.
    titles = [s["title"] for s in _store().history(KIND, THREAD)["sessions"]]
    assert titles == ["about dogs", "about cats"]


def test_open_refuses_a_conversation_of_another_workspace(single, client, seeded, fake_agent, monkeypatch):
    from workspace import create_workspace_folder
    create_workspace_folder("sales")
    _turn(client, "a sales question", workspace="sales")
    results = _calls_tool(monkeypatch, fake_agent, [{"action": "list", "workspace": "all"},
                                                    {"action": "open", "conversation": "s0"}])
    _turn(client, "open the sales one")
    _turn(client, "now")
    listed, opened = results
    assert [(c["title"], c["workspace"]) for c in listed["conversations"]] == [("a sales question", "sales")]
    assert not opened["ok"] and opened["code"] == "other_workspace"
    assert "sales" in opened["error"]


def test_open_keeps_a_conversation_that_was_more_than_the_request(
        single, client, seeded, fake_agent, monkeypatch):
    _turn(client, "about dogs")
    client.delete("/api/assistant")
    _turn(client, "about cats")
    results = _calls_tool(monkeypatch, fake_agent, [{"action": "open", "conversation": "0"}])
    _turn(client, "now the dogs again")
    assert results[0]["opening"] == "s0"
    titles = sorted(s["title"] for s in _store().history(KIND, THREAD)["sessions"])
    assert titles == ["about cats", "about dogs"]


def test_outside_an_assistant_turn_the_tool_refuses(monkeypatch):
    from tools.assistant_conversations import assistant_conversations
    monkeypatch.delenv("AGENT_RUN_ID", raising=False)
    out = json.loads(assistant_conversations.invoke({"action": "list"}))
    assert not out["ok"] and out["code"] == "not_assistant_thread"
