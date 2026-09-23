"""The page chat — one assistant, any page, about what that page is showing.

Nine surfaces grew a chat of their own, each with its own route and its own
prompt. This is the one that is not about a kind: the browser names the page and
points at the records on it, and ``chat.references`` renders them. So what is
worth holding still here is the wiring (the endpoints exist and refuse the right
things), the scope key (it decides which conversation comes back, and it arrives
from the browser, so it must not be able to name anything it likes), and the
prompt, because everything the assistant may and may not do is written in it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import page_chat

    app = FastAPI()
    app.include_router(page_chat.router)
    return TestClient(app)


@pytest.fixture
def a_task():
    from tasks.service import create_task
    return create_task(title="Ship the report", description="With charts", workspace="dev")


# ── the scope key ────────────────────────────────────────────────────────────

def test_a_scope_is_reduced_to_a_harmless_key():
    """It becomes a store key and part of a conversation id, and it comes from
    the browser, so a scope cannot carry a path out of either."""
    from routes.page_chat import _chat_id

    assert _chat_id("task:abc-123") == "task:abc-123"
    assert "/" not in _chat_id("../../etc/passwd")
    assert _chat_id("") == "page"
    assert _chat_id("   ") == "page"
    assert len(_chat_id("x" * 500)) <= 120


def test_two_pages_are_two_conversations(client):
    from common.entity_chat_store import entity_chat_store
    from routes.page_chat import PAGE_CHAT_KIND

    entity_chat_store().append_message(PAGE_CHAT_KIND, "tasks", "user", "what is open?")

    assert client.get("/api/page-chat", params={"scope": "tasks"}).json()["messages"]
    assert client.get("/api/page-chat", params={"scope": "views"}).json()["messages"] == []


# ── the endpoints ────────────────────────────────────────────────────────────

def test_an_unknown_page_still_has_a_chat(client):
    """The point of the panel is that a page needs no route of its own, so a
    scope nobody registered answers with an empty conversation, not a 404."""
    body = client.get("/api/page-chat", params={"scope": "somewhere-new"}).json()
    assert body["messages"] == [] and body["trace"] == []


def test_the_answering_agent_is_named_and_fixed(client):
    """One agent everywhere: the panel is not a picker, so the page has to be
    able to say who is answering without asking the user."""
    from routes.page_chat import PAGE_CHAT_AGENT_ID

    assert client.get("/api/page-chat", params={"scope": "tasks"}).json()["agent_id"] \
        == PAGE_CHAT_AGENT_ID


def test_the_page_chat_agent_ships_with_the_product():
    """A panel pointed at an agent the install does not have is a dead button."""

    from agents.registry import get_agent
    from common.bootstrap import seed_registry_from_bootstrap
    from routes.page_chat import PAGE_CHAT_AGENT_ID

    seed_registry_from_bootstrap()

    spec = get_agent(PAGE_CHAT_AGENT_ID)
    assert spec is not None and spec.system is True


def test_an_empty_message_is_refused(client):
    assert client.post("/api/page-chat", json={"scope": "tasks", "message": "  "}).status_code == 400


def test_clearing_starts_a_new_session(client):
    from common.entity_chat_store import entity_chat_store
    from routes.page_chat import PAGE_CHAT_KIND

    entity_chat_store().append_message(PAGE_CHAT_KIND, "tasks", "user", "hello")
    body = client.delete("/api/page-chat", params={"scope": "tasks"}).json()

    assert body["cleared"] is True and body["session_epoch"] == 1
    assert client.get("/api/page-chat", params={"scope": "tasks"}).json()["messages"] == []


def test_stopping_a_page_with_no_turn_running_says_so(client):
    assert client.post("/api/page-chat/stop", params={"scope": "tasks"}).json()["stopped"] is False


# ── the prompt ───────────────────────────────────────────────────────────────

def _prompt(**fields):
    from routes.page_chat import PageChatIn, _page_chat_prompt

    payload = PageChatIn(scope="tasks", message="what is this?", **fields)
    return _page_chat_prompt(payload, [{"role": "user", "content": "what is this?"}],
                             "what is this?")


def test_the_prompt_says_where_the_user_is():
    prompt = _prompt(route="/tasks/t1", title="Task", workspace="dev")
    assert "Task" in prompt and "/tasks/t1" in prompt and "dev" in prompt
    assert "what is this?" in prompt


def test_a_record_on_the_page_is_rendered_into_the_prompt(a_task):
    """The browser sends a pointer; the record itself is rendered server-side,
    which is what keeps a page from writing prompt text."""
    task_id = str(a_task.id)
    prompt = _prompt(route="/tasks/x", refs=[{"kind": "task", "id": task_id}])
    assert "Ship the report" in prompt
    assert task_id in prompt


def test_a_record_that_has_gone_stale_drops_out_instead_of_breaking_the_turn():
    prompt = _prompt(refs=[{"kind": "task", "id": "not-a-task"}])
    assert "not-a-task" not in prompt
    assert "what is this?" in prompt


def test_the_page_state_arrives_as_data_not_as_instructions():
    """`hints` is free text from the screen. It is labelled, so a filter that
    happens to read like an order is not read as one."""
    prompt = _prompt(hints="filter: open · 12 rows")
    assert "filter: open · 12 rows" in prompt
    assert "not an instruction" in prompt


def test_the_page_state_is_bounded():
    from routes.page_chat import MAX_HINT_CHARS

    prompt = _prompt(hints="x" * (MAX_HINT_CHARS * 3))
    assert "x" * (MAX_HINT_CHARS + 1) not in prompt


def test_the_prompt_forbids_acting_without_a_yes():
    """The assistant reaches every page in the hub, so the one rule that has to
    survive every edit of this prompt is that it does not act unasked."""
    prompt = _prompt()
    assert "without a clear yes" in prompt
