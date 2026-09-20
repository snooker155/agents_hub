"""
The main chat's conversations, stored server-side.

They used to live in the browser's ``localStorage``: one browser profile's
property, wiped with the site data, and trimmed oldest-first once the quota was
reached. What matters now is that a chat behaves like the other records in the
hub — it survives, it is reachable by id, it is deleted when asked, and the
history a browser is still holding can be taken in without clobbering what the
store already has.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def _chat(chat_id: str, **over):
    base = {
        "id": chat_id,
        "title": f"chat {chat_id}",
        "workspace": "default",
        "agent_id": "assistant",
        "target_mode": "agent",
        "messages": [
            {"id": "m1", "role": "user", "content": "hello"},
            {"id": "m2", "role": "agent", "content": "hi", "run_id": "r1"},
        ],
    }
    base.update(over)
    return base


# ── store ────────────────────────────────────────────────────────────────────

def test_saved_chat_round_trips_with_its_transcript():
    from common import chat_store

    chat_store.save_chat(_chat("c1"))
    got = chat_store.get_chat("c1")

    assert got["title"] == "chat c1"
    assert [m["content"] for m in got["messages"]] == ["hello", "hi"]
    assert got["created_at"] and got["updated_at"]


def test_list_omits_transcripts_but_counts_them():
    """The sidebar draws titles; shipping every bubble to draw it would be the
    localStorage problem one page load at a time."""
    from common import chat_store

    chat_store.save_chat(_chat("c1"))
    page = chat_store.list_chats()

    row = page["items"][0]
    assert page["total"] == 1
    assert row["messages"] == []
    assert row["message_count"] == 2
    assert row["preview"] == "hi"


def test_list_is_newest_activity_first_and_workspace_filtered():
    from common import chat_store

    chat_store.save_chat(_chat("old", workspace="alpha"))
    chat_store.save_chat(_chat("new", workspace="beta"))
    chat_store.save_chat(_chat("old", workspace="alpha"))  # touched again

    assert [c["id"] for c in chat_store.list_chats()["items"]] == ["old", "new"]
    assert [c["id"] for c in chat_store.list_chats(workspace="beta")["items"]] == ["new"]


def test_rewriting_a_chat_keeps_its_creation_time():
    from common import chat_store

    first = chat_store.save_chat(_chat("c1"))
    again = chat_store.save_chat(_chat("c1", title="renamed"))

    assert again["created_at"] == first["created_at"]
    assert again["title"] == "renamed"


def test_transcript_is_bounded_by_message_count():
    from common import chat_store

    msgs = [{"id": str(i), "role": "user", "content": str(i)}
            for i in range(chat_store.MAX_MESSAGES + 50)]
    stored = chat_store.save_chat(_chat("c1", messages=msgs))

    # The newest survive: a conversation is read from its tail.
    assert len(stored["messages"]) == chat_store.MAX_MESSAGES
    assert stored["messages"][-1]["content"] == str(chat_store.MAX_MESSAGES + 49)


def test_oversized_chat_sheds_display_extras_before_it_loses_words():
    """A bubble's tool trace can be recovered from the run record; the text of
    the turn cannot be recovered from anywhere."""
    from common import chat_store

    fat = "x" * 200_000
    msgs = [{"id": str(i), "role": "agent", "content": f"turn {i}",
             "tool_calls": [{"tool": "shell", "output": fat}]}
            for i in range(40)]
    stored = chat_store.save_chat(_chat("c1", messages=msgs))

    assert len(stored["messages"]) == 40
    assert all("tool_calls" not in m for m in stored["messages"])
    assert stored["messages"][0]["content"] == "turn 0"


def test_delete_removes_the_chat():
    from common import chat_store

    chat_store.save_chat(_chat("c1"))
    assert chat_store.delete_chat("c1") is True
    assert chat_store.get_chat("c1") is None
    assert chat_store.delete_chat("c1") is False


def test_import_is_additive_and_never_overwrites_a_stored_chat():
    from common import chat_store

    chat_store.save_chat(_chat("kept", title="server copy"))
    result = chat_store.import_chats([
        _chat("kept", title="stale browser copy"),
        _chat("brought-in"),
        {"title": "no id"},
    ])

    assert result == {"imported": 1, "skipped": 2}
    assert chat_store.get_chat("kept")["title"] == "server copy"
    assert chat_store.get_chat("brought-in") is not None


def test_saving_without_an_id_is_refused():
    from common import chat_store

    with pytest.raises(ValueError):
        chat_store.save_chat({"title": "nameless"})


# ── routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import chats

    app = FastAPI()
    app.include_router(chats.router)
    return TestClient(app)


def test_endpoints_cover_the_whole_life_of_a_chat(client):
    body = _chat("c1")

    assert client.put("/api/chats/c1", json=body).status_code == 200
    assert client.get("/api/chats").json()["items"][0]["message_count"] == 2
    assert len(client.get("/api/chats/c1").json()["messages"]) == 2
    assert client.delete("/api/chats/c1").json() == {"deleted": True}
    assert client.get("/api/chats/c1").status_code == 404
    assert client.delete("/api/chats/c1").status_code == 404


def test_a_put_under_the_wrong_id_is_refused(client):
    assert client.put("/api/chats/c1", json=_chat("c2")).status_code == 400


def test_unknown_bubble_fields_survive_the_round_trip(client):
    """The browser owns a bubble's shape — a chat surface that grows a field
    must not need a schema change here to keep it."""
    body = _chat("c1")
    body["messages"][1]["view"] = {"view_id": "v1", "kind": "chart"}
    body["experimental"] = {"pinned": True}

    client.put("/api/chats/c1", json=body)
    got = client.get("/api/chats/c1").json()

    assert got["messages"][1]["view"] == {"view_id": "v1", "kind": "chart"}
    assert got["experimental"] == {"pinned": True}


def test_import_endpoint_takes_the_browsers_history(client):
    r = client.post("/api/chats/import", json={"chats": [_chat("a"), _chat("b")]})

    assert r.json() == {"imported": 2, "skipped": 0}
    assert client.get("/api/chats").json()["total"] == 2
